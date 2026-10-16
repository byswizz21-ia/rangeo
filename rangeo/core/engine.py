"""Point d'entrée du moteur : enchaîne scan → analyse → plan → exécution."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from . import duplicates as dup_mod
from .classify import ClassifyOptions, classify
from .config import Config
from .defaults import FOLDERS
from .executor import ExecOptions, ExecResult, Executor
from .fsutil import is_strictly_within, is_within, nkey, norm
from .index import MetaIndex
from .journal import Journal
from .models import Plan, ScanResult, TaskControl
from .planner import PlanOptions, build_plan
from .protect import Protection
from .scanner import ScanOptions, scan
from .trash import QUARANTINE_DIR
from .undo import UndoResult, undo_session

log = logging.getLogger(__name__)


@dataclass
class SortRequest:
    sources: list[str]
    profile: dict
    destination: str = ""
    in_place: bool = False
    recursive: bool = True
    open_up: list[str] | None = None
    keep_whole: list[str] | None = None


class Engine:
    def __init__(self, config: Config, journal: Journal | None = None, index: MetaIndex | None = None,
                 kind_label=None):
        self.config = config
        self.journal = journal or Journal()
        self.index = index or MetaIndex()
        self.kind_label = kind_label

    @property
    def lang(self) -> str:
        return self.config.get("language", "fr")

    # -- vérifications -------------------------------------------------------------
    def check_request(self, req: SortRequest) -> list[tuple[str, str]]:
        """Problèmes bloquants (clé de traduction, chemin)."""
        problems = []
        prot = Protection(self.config.get("protected"))
        if not req.sources:
            problems.append(("check.no_source", ""))
        for s in req.sources:
            if not os.path.isdir(s):
                problems.append(("check.source_missing", s))
            elif prot.source_problem(s):
                problems.append((prot.source_problem(s), s))
        if not req.in_place:
            if not req.destination:
                problems.append(("check.no_destination", ""))
            else:
                dest = norm(req.destination)
                if prot.destination_problem(dest):
                    problems.append((prot.destination_problem(dest), dest))
                for s in req.sources:
                    if is_strictly_within(norm(s), dest):
                        problems.append(("check.source_inside_destination", s))
        return problems

    @staticmethod
    def _normalize(req: SortRequest) -> SortRequest:
        """Destination = dossier source : c'est un rangement « sur place ».
        (Sinon la destination, exclue du scan pour ne pas re-ranger, masquerait toute la source : rien ne bougerait.)"""
        if not req.in_place and req.destination and req.sources \
                and all(nkey(norm(s)) == nkey(norm(req.destination)) for s in req.sources):
            req.in_place = True
            req.destination = ""
        return req

    def _scan_options(self, req: SortRequest) -> ScanOptions:
        cfg = self.config
        exclude_paths = []
        dup_name = cfg.get("duplicates_folder") or FOLDERS[self.lang]["duplicates"]
        if req.destination and not req.in_place:
            dest = norm(req.destination)
            # le dossier de destination n'est jamais re-scanné (fichiers déjà rangés)
            if any(is_within(dest, s) for s in req.sources):
                exclude_paths.append(dest)
            exclude_paths.append(os.path.join(dest, dup_name))
        for s in req.sources:
            exclude_paths.append(os.path.join(norm(s), dup_name))
        return ScanOptions(
            recursive=req.recursive,
            skip_hidden=cfg.get("skip_hidden", True),
            exclusions=cfg.get("exclusions", []) + [QUARANTINE_DIR],
            exclude_paths=exclude_paths,
            keep_whole=(cfg.get("keep_whole") or []) + (req.keep_whole or []),
            open_up=req.open_up or [],
            detect_bundles=cfg.get("detect_bundles", True),
            extra_protected=cfg.get("protected", []),
        )

    def _classify_options(self) -> ClassifyOptions:
        cfg = self.config
        return ClassifyOptions(
            deep_check=cfg.get("deep_check", False),
            date_order=cfg.get("date_order") or ["metadata", "filename", "oldest"],
            low_priority=cfg.get("priority") == "discreet",
            threads=2 if cfg.get("priority") == "discreet" else None,
            lang=self.lang,
        )

    # -- rangement -------------------------------------------------------------------
    def scan_and_classify(self, req: SortRequest, control: TaskControl) -> ScanResult:
        self._normalize(req)
        scan_result = scan(req.sources, self._scan_options(req), control)
        classify(scan_result.entries, self._classify_options(), control, self.index)
        return scan_result

    def plan(self, req: SortRequest, scan_result: ScanResult, control: TaskControl) -> Plan:
        cfg = self.config
        self._normalize(req)
        options = PlanOptions(
            dest_root=norm(req.destination) if req.destination and not req.in_place else "",
            in_place=req.in_place,
            conflict=cfg.get("conflict", "rename"),
            identical=cfg.get("identical", "duplicates"),
            duplicates_folder=cfg.get("duplicates_folder") or FOLDERS[self.lang]["duplicates"],
            lang=self.lang,
            fix_extensions=cfg.get("fix_extensions", False),
            companions=cfg.get("companions", True),
            extra_dirs=req.profile.get("extra_dirs", []),
            extra_protected=cfg.get("protected", []),
            kind_label=self.kind_label,
        )
        plan = build_plan(scan_result.entries, req.profile.get("rules", []), options, control)
        plan.sources = list(scan_result.roots)
        plan.profile_name = req.profile.get("name", "")
        plan.warnings.extend(scan_result.warnings)
        return plan

    def analyze(self, req: SortRequest, control: TaskControl) -> tuple[ScanResult, Plan]:
        scan_result = self.scan_and_classify(req, control)
        return scan_result, self.plan(req, scan_result, control)

    def exec_options(self, cleanup: bool | None = None) -> ExecOptions:
        discreet = self.config.get("priority") == "discreet"
        return ExecOptions(
            cleanup_empty=self.config.get("cleanup_empty", True) if cleanup is None else cleanup,
            low_priority=discreet,
            threads=1 if discreet else None,
        )

    def execute(self, plan: Plan, control: TaskControl, kind: str = "sort", profile: str = "",
                cleanup: bool | None = None) -> ExecResult:
        executor = Executor(self.journal, control, self.exec_options(cleanup), self.index)
        return executor.run_plan(plan, kind, profile)

    def resume(self, session_id: int, control: TaskControl, retry_failed: bool = False) -> ExecResult:
        executor = Executor(self.journal, control, self.exec_options(), self.index)
        return executor.execute_session(session_id, resume=True, retry_failed=retry_failed)

    def undo(self, session_id: int, control: TaskControl, only_ids: set[int] | None = None) -> UndoResult:
        if self.journal.owner_alive(session_id):
            raise RuntimeError("session_in_use")  # un autre processus (rangement planifié) l'exécute encore
        return undo_session(self.journal, session_id, control, only_ids, self.index)

    # -- doublons -----------------------------------------------------------------
    def find_duplicates(self, sources: list[str], control: TaskControl,
                        recursive: bool = True) -> tuple[ScanResult, list[dup_mod.DupGroup]]:
        cfg = self.config
        dup_name = cfg.get("duplicates_folder") or FOLDERS[self.lang]["duplicates"]
        options = ScanOptions(
            recursive=recursive,
            skip_hidden=cfg.get("skip_hidden", True),
            exclusions=cfg.get("exclusions", []) + [QUARANTINE_DIR, dup_name],
            detect_bundles=cfg.get("detect_bundles", True),
            keep_whole=cfg.get("keep_whole") or [],
            extra_protected=cfg.get("protected", []),
        )
        scan_result = scan(sources, options, control)
        dup_options = dup_mod.DupOptions(
            min_size=int(cfg.get("dup_min_size", 1) or 1),
            keep_criteria=cfg.get("dup_keep") or ["preferred", "clean_name", "oldest", "shortest_path"],
            preferred=cfg.get("dup_preferred") or [],
            threads=2 if cfg.get("priority") == "discreet" else None,
        )
        groups = dup_mod.find_duplicates(scan_result.entries, dup_options, control, self.index)
        return scan_result, groups

    def find_similar(self, sources: list[str], mode: str, sensitivity: str, control: TaskControl,
                     recursive: bool = True) -> tuple[ScanResult, list[dup_mod.DupGroup]]:
        """Images / musiques / vidéos ressemblantes."""
        from . import similar
        from .classify import ClassifyOptions, classify

        cfg = self.config
        dup_name = cfg.get("duplicates_folder") or FOLDERS[self.lang]["duplicates"]
        options = ScanOptions(
            recursive=recursive,
            skip_hidden=cfg.get("skip_hidden", True),
            exclusions=cfg.get("exclusions", []) + [QUARANTINE_DIR, dup_name],
            detect_bundles=cfg.get("detect_bundles", True),
            keep_whole=cfg.get("keep_whole") or [],
            extra_protected=cfg.get("protected", []),
        )
        scan_result = scan(sources, options, control)
        group = similar.MODE_GROUPS[mode]
        from . import filetypes as ft

        media = [e for e in scan_result.entries if not e.is_bundle and ft.kind_from_ext(e.ext) == group]
        classify(media, ClassifyOptions(lang=self.lang, date_order=cfg.get("date_order") or ["metadata", "oldest"]),
                 control, self.index)
        sim_options = similar.SimilarOptions(mode=mode, sensitivity=sensitivity,
                                             min_size=int(cfg.get("dup_min_size", 1) or 1),
                                             threads=2 if cfg.get("priority") == "discreet" else None)
        groups = similar.find_similar(media, sim_options, control, self.index)
        dup_options = dup_mod.DupOptions(
            keep_criteria=cfg.get("dup_keep") or ["preferred", "best_quality", "clean_name", "oldest"],
            preferred=cfg.get("dup_preferred") or [],
        )
        return scan_result, dup_mod.similar_groups(groups, dup_options)

    def duplicate_plan(self, groups: list[dup_mod.DupGroup], action: str, dup_root: str | None = None) -> Plan:
        folder = self.config.get("duplicates_folder") or FOLDERS[self.lang]["duplicates"]
        return dup_mod.build_dup_plan(groups, action, dup_root, folder, self.lang)

    def close(self) -> None:
        for obj in (self.journal, self.index):
            try:
                obj.close()
            except Exception:
                pass


def same_path(a: str, b: str) -> bool:
    return nkey(norm(a)) == nkey(norm(b))
