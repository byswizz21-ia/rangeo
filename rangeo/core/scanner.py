"""Scan multi-thread des dossiers.

Le scan construit un arbre de dossiers en mémoire, puis décide dossier par
dossier s'il faut :
  * ranger ses fichiers un par un ;
  * le traiter comme un bloc (projet de code, logiciel/jeu, données
    d'application, dossier marqué « ne pas éclater »).
"""

from __future__ import annotations

import fnmatch
import logging
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field

from . import filetypes as ft
from . import metadata as md
from .fsutil import (
    FILE_ATTRIBUTE_HIDDEN,
    FILE_ATTRIBUTE_SYSTEM,
    creation_time,
    disk_kind,
    is_within,
    nkey,
    norm,
    xp,
)
from .models import Cancelled, FileEntry, ScanResult, TaskControl
from .protect import Protection

log = logging.getLogger(__name__)

PROJECT_FILE_MARKERS = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pipfile", "cargo.toml",
    "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "cmakelists.txt",
    "composer.json", "gemfile", "makefile", "meson.build", "pubspec.yaml", "mix.exs", "stack.yaml",
    "deno.json", "project.godot", "angular.json", "tsconfig.json", "vite.config.js", "vite.config.ts",
    "next.config.js", "build.sbt", "dune-project", "rebar.config", "platformio.ini", "west.yml",
}
PROJECT_DIR_MARKERS = {".git", ".hg", ".svn", ".idea", ".vscode", ".vs"}
PROJECT_EXT_MARKERS = {"sln", "csproj", "vbproj", "fsproj", "vcxproj", "uproject", "xcodeproj", "xcworkspace"}
SOFTWARE_MARKER_FILES = {
    "steam_api.dll", "steam_api64.dll", "unityplayer.dll", "unins000.exe", "unins000.dat", "uninstall.exe",
    "steam_appid.txt", "eossdk-win64-shipping.dll", "galaxy64.dll", "galaxy.dll", "uninst.exe",
    "gameassembly.dll", "ue4prereqsetup_x64.exe", "launcher.exe",
}
SOFTWARE_MARKER_DIRS = {".egstore", "steamapps", "__installer", "_commonredist", "redist", "binaries"}

# Fichiers « personnels » : signe qu'un dossier appartient à l'utilisateur et pas à un logiciel
_PERSONAL_DOCS = set("pdf doc docx odt xls xlsx ods ppt pptx odp epub mobi azw3 pages numbers key".split())
_PERSONAL_AUDIO = set("mp3 flac m4a aac wma alac ape".split())
_ALWAYS_PERSONAL = ft.RAW_EXT | {"heic", "heif", "mts", "m2ts", "3gp"}
_MEDIA_NAMED = set("jpg jpeg png gif webp mp4 mov mkv avi wmv m4v".split())
_APP_DOC_NAMES = re.compile(r"(readme|read_me|lisez|license|licence|eula|manual|manuel|guide|changelog|credits|"
                            r"notice|copying|authors|install|third.?party|patch.?notes|terms|privacy)", re.I)


def is_personal_file(stem: str, ext: str) -> bool:
    """Indice rapide (sans lire le fichier) qu'un fichier a été créé ou choisi par l'utilisateur."""
    if ext in _ALWAYS_PERSONAL or ext in _PERSONAL_AUDIO:
        return True
    if ext in _PERSONAL_DOCS:
        return not _APP_DOC_NAMES.search(stem)
    if ext in _MEDIA_NAMED:
        return bool(
            md.is_camera_name(stem) or md.is_screenshot_name(stem) or md.date_from_name(stem)
            or md.parse_series(stem) or md.parse_movie(stem, 1 << 40)
        )
    return False


@dataclass
class ScanOptions:
    recursive: bool = True
    skip_hidden: bool = True
    exclusions: list[str] = field(default_factory=list)  # noms (jokers * ?) ou chemins complets
    exclude_paths: list[str] = field(default_factory=list)  # ex. dossier de destination
    keep_whole: list[str] = field(default_factory=list)  # dossiers « ne pas éclater »
    open_up: list[str] = field(default_factory=list)  # blocs que l'utilisateur veut quand même ranger
    detect_bundles: bool = True
    threads: int | None = None
    extra_protected: list[str] = field(default_factory=list)


class _Node:
    __slots__ = (
        "path", "parent", "depth", "files", "children", "child_names", "project", "keep", "has_exe",
        "has_dll", "software", "strong_software", "excluded_children", "sub_files", "sub_size", "sub_user",
        "sub_tech", "sub_software", "sub_mtime", "root", "loose_personal", "sub_personal",
    )

    def __init__(self, path: str, parent: _Node | None, root: str):
        self.path = path
        self.parent = parent
        self.depth = 0 if parent is None else parent.depth + 1
        self.root = root
        self.files: list[FileEntry] = []
        self.children: list[_Node] = []
        self.child_names: list[str] = []
        self.project = False
        self.keep = False
        self.has_exe = False
        self.has_dll = False
        self.software = False  # marqueur direct (exe+dll, steam_api.dll…)
        self.strong_software = False  # marqueur sans ambiguïté
        self.excluded_children = False
        self.sub_files = 0
        self.sub_size = 0
        self.sub_user = 0
        self.sub_tech = 0
        self.sub_software = False
        self.sub_mtime = 0.0
        self.loose_personal = 0
        self.sub_personal = 0


class _Matcher:
    """Exclusions : jokers sur le nom, ou chemins complets."""

    def __init__(self, patterns: list[str], paths: list[str]):
        self.names: list[str] = []
        self.globs: list[str] = []
        self.paths: list[str] = [norm(p) for p in paths if p]
        for pat in patterns:
            pat = (pat or "").strip()
            if not pat:
                continue
            if "\\" in pat or "/" in pat or ":" in pat:
                self.paths.append(norm(pat))
            elif any(c in pat for c in "*?["):
                self.globs.append(pat.lower())
            else:
                self.names.append(pat.lower())
        self.name_set = set(self.names)

    def match(self, path: str, lname: str) -> bool:
        if lname in self.name_set:
            return True
        for g in self.globs:
            if fnmatch.fnmatchcase(lname, g):
                return True
        for p in self.paths:
            if is_within(path, p):
                return True
        return False


class Scanner:
    SAMPLE_LIMIT = 5000

    def __init__(self, options: ScanOptions, control: TaskControl | None = None):
        self.opt = options
        self.control = control or TaskControl()
        self.protection = Protection(options.extra_protected)
        self.matcher = _Matcher(options.exclusions, options.exclude_paths)
        self.keep_keys = {nkey(norm(p)) for p in options.keep_whole}
        self.open_keys = {nkey(norm(p)) for p in options.open_up}
        self._lock = threading.Lock()
        self._nodes: list[_Node] = []
        self._result: ScanResult | None = None
        self._cancelled = False

    # -- utilitaires -------------------------------------------------------------
    def _skip(self, path: str, reason: str) -> None:
        res = self._result
        with self._lock:
            res.skip_counts[reason] = res.skip_counts.get(reason, 0) + 1
            if len(res.skipped) < self.SAMPLE_LIMIT:
                res.skipped.append((path, reason))

    def _error(self, path: str, message: str) -> None:
        with self._lock:
            self._result.errors.append((path, message))
        self.control.error()

    # -- scan -------------------------------------------------------------------
    def scan(self, roots: list[str]) -> ScanResult:
        start = time.monotonic()
        roots = [norm(r) for r in roots]
        # évite de scanner deux fois un dossier inclus dans un autre
        unique: list[str] = []
        for r in sorted(roots, key=len):
            if not any(is_within(r, u) for u in unique):
                unique.append(r)
        self._result = res = ScanResult(roots=unique)
        self.control.set_stage("scan")

        root_nodes: list[_Node] = []
        for r in unique:
            if not os.path.isdir(xp(r)):
                self._error(r, "not_found")
                continue
            if self.protection.source_problem(r):
                res.warnings.append((self.protection.source_problem(r), r))
                continue
            node = _Node(r, None, r)
            root_nodes.append(node)
            self._nodes.append(node)

        threads = self.opt.threads or self._auto_threads(unique)
        q: queue.Queue = queue.Queue()
        for node in root_nodes:
            q.put(node)
        workers = [threading.Thread(target=self._worker, args=(q,), daemon=True) for _ in range(threads)]
        for w in workers:
            w.start()
        q.join()
        for _ in workers:
            q.put(None)
        for w in workers:
            w.join()
        if self._cancelled or self.control.cancelled:
            raise Cancelled()

        self._aggregate()
        for node in root_nodes:
            self._decide(node, is_root=True)
        res.dir_count = len(self._nodes)
        res.file_count = sum(1 for e in res.entries if not e.is_bundle) + sum(
            e.bundle_files for e in res.entries if e.is_bundle
        )
        res.total_size = sum(e.size for e in res.entries)
        res.duration = time.monotonic() - start
        log.info(
            "Scan terminé : %d dossiers, %d éléments en %.1fs", res.dir_count, len(res.entries), res.duration
        )
        return res

    def _auto_threads(self, roots: list[str]) -> int:
        kinds = {disk_kind(r) for r in roots} if roots else {"unknown"}
        if "hdd" in kinds or "removable" in kinds:
            return 4
        if "network" in kinds:
            return 8
        return 16 if kinds == {"ssd"} else 8

    def _worker(self, q: queue.Queue) -> None:
        while True:
            node = q.get()
            if node is None:
                q.task_done()
                return
            try:
                if not self._cancelled:
                    self.control.checkpoint()
                    self._list(node, q)
            except Cancelled:
                self._cancelled = True
            except Exception as exc:  # ne jamais bloquer la file
                log.exception("Erreur pendant le scan de %s", node.path)
                self._error(node.path, f"{type(exc).__name__}: {exc}")
            finally:
                q.task_done()

    def _list(self, node: _Node, q: queue.Queue) -> None:
        opt = self.opt
        prefix = node.path if node.path.endswith("\\") else node.path + "\\"
        try:
            it = os.scandir(xp(node.path))
        except OSError as exc:
            self._error(node.path, exc.strerror or str(exc))
            return
        new_children: list[_Node] = []
        count = 0
        with it:
            for de in it:
                name = de.name
                lname = name.lower()
                path = prefix + name
                try:
                    st = de.stat(follow_symlinks=False)
                    is_dir = de.is_dir(follow_symlinks=False)
                except OSError as exc:
                    self._error(path, exc.strerror or str(exc))
                    continue
                attrs = getattr(st, "st_file_attributes", 0)
                if is_dir:
                    node.child_names.append(lname)
                    if lname in PROJECT_DIR_MARKERS:
                        node.project = True
                    if lname in SOFTWARE_MARKER_DIRS:
                        node.software = True
                        if lname in (".egstore", "steamapps"):
                            node.strong_software = True
                    if de.is_symlink() or de.is_junction():
                        self._skip(path, "link")
                        continue
                    if self.matcher.match(path, lname):
                        node.excluded_children = True
                        self._skip(path, "excluded")
                        continue
                    if self.protection.is_protected(path):
                        self._skip(path, "protected")
                        continue
                    if opt.skip_hidden and attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
                        node.excluded_children = True
                        self._skip(path, "hidden")
                        continue
                    if not opt.recursive:
                        continue
                    new_children.append(_Node(path, node, node.root))
                    continue

                # --- fichier ---
                # (les fichiers OneDrive sont des « reparse points » mais pas des liens : on les garde)
                if de.is_symlink():
                    self._skip(path, "link")
                    continue
                stem, dot_ext = os.path.splitext(lname)
                ext = dot_ext[1:] if stem else ""
                if lname in PROJECT_FILE_MARKERS or ext in PROJECT_EXT_MARKERS:
                    node.project = True
                if ext == "exe":
                    node.has_exe = True
                elif ext == "dll":
                    node.has_dll = True
                if lname in SOFTWARE_MARKER_FILES:
                    node.software = True
                    node.strong_software = True
                if lname == ".rangeo-keep":
                    node.keep = True
                    continue
                if lname in ft.ALWAYS_SKIP_NAMES:
                    continue
                if ext in ft.PARTIAL_EXT or lname.startswith("~$") or lname.startswith("~rangeo-"):
                    self._skip(path, "partial")
                    continue
                if opt.skip_hidden and attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
                    self._skip(path, "hidden")
                    continue
                if self.matcher.match(path, lname):
                    self._skip(path, "excluded")
                    continue
                if is_personal_file(name[: len(name) - len(dot_ext)] if stem else name, ext):
                    node.loose_personal += 1
                node.files.append(
                    FileEntry(
                        dir=node.path,
                        name=name,
                        size=st.st_size,
                        mtime=st.st_mtime,
                        ctime=creation_time(st),
                        attrs=attrs,
                        ext=ext,
                        kind=ft.kind_from_ext(ext),
                        root=node.root,
                    )
                )
                count += 1
        if node.has_exe and node.has_dll:
            node.software = True
        node.children = new_children
        with self._lock:
            self._nodes.extend(new_children)
        self.control.advance(count, current=node.path)
        for child in new_children:
            q.put(child)

    # -- analyse de l'arbre ------------------------------------------------------
    def _aggregate(self) -> None:
        # les enfants sont toujours ajoutés après leur parent : ordre inverse = de bas en haut
        for node in reversed(self._nodes):
            files = node.files
            n_user = n_tech = 0
            size = 0
            newest = 0.0
            for f in files:
                size += f.size
                if f.mtime > newest:
                    newest = f.mtime
                group = ft.group_of(f.kind)
                if group in ft.USER_GROUPS:
                    n_user += 1
                elif group in ("technical", "unknown", "code"):
                    n_tech += 1
            node.sub_files = len(files)
            node.sub_size = size
            node.sub_user = n_user
            node.sub_tech = n_tech
            node.sub_software = node.software
            node.sub_mtime = newest
            node.sub_personal = node.loose_personal
            for c in node.children:
                node.sub_personal += c.sub_personal
                node.sub_files += c.sub_files
                node.sub_size += c.sub_size
                node.sub_user += c.sub_user
                node.sub_tech += c.sub_tech
                node.sub_software = node.sub_software or c.sub_software
                if c.sub_mtime > node.sub_mtime:
                    node.sub_mtime = c.sub_mtime

    @staticmethod
    def _looks_like_app_data(node: _Node) -> bool:
        """Dossier rempli de fichiers techniques/inconnus (sauvegardes de jeux, configs…)."""
        total = node.sub_files
        if total < 3:
            return False
        return node.sub_tech / total >= 0.8 and node.sub_user <= max(2, total // 10)

    @staticmethod
    def _is_user_container(node: _Node) -> bool:
        """Dossier de l'utilisateur qui *contient* un logiciel (≠ dossier du logiciel lui-même).

        Ex. « Vieux disque » contient « Jeux/SuperJeu » mais aussi des photos et documents :
        seul le jeu doit rester entier, le reste est rangé.
        """
        outside = node.loose_personal + sum(c.sub_personal for c in node.children if not c.sub_software)
        if outside >= 3:
            return True
        if not node.software and not node.has_exe and not node.strong_software:
            # plusieurs logiciels côte à côte (ex. dossier « Jeux ») : chacun est un bloc
            return sum(1 for c in node.children if c.sub_software) >= 2
        return False

    def _bundle(self, node: _Node, kind: str, reason: str) -> None:
        parent, name = os.path.split(node.path)
        entry = FileEntry(
            dir=parent,
            name=name,
            size=node.sub_size,
            mtime=node.sub_mtime or 0.0,
            ctime=node.sub_mtime or 0.0,
            kind=kind,
            is_bundle=True,
            bundle_files=node.sub_files,
            bundle_reason=reason,
            root=node.root,
        )
        if node.excluded_children:
            entry.meta["size_partial"] = True
        self._result.entries.append(entry)

    def _classify_node(self, node: _Node, is_root: bool) -> tuple[str, str] | None:
        """Renvoie (type de bloc, raison) si le dossier doit rester entier, sinon None."""
        if nkey(node.path) in self.open_keys:
            return None
        if not is_root and (nkey(node.path) in self.keep_keys or node.keep):
            return "kept_folder", "marked"
        if not self.opt.detect_bundles:
            return None
        if node.project:
            return "code_project", "project_marker"
        if is_root:
            # pour la racine, seuls les marqueurs sans ambiguïté comptent
            # (un dossier Téléchargements contient souvent un .exe et une .dll)
            return ("software", "software_marker") if node.strong_software else None
        if node.sub_software and not self._is_user_container(node):
            return "software", "software_marker"
        if self._looks_like_app_data(node):
            return "app_data", "technical_files"
        return None

    def _decide(self, root: _Node, is_root: bool) -> None:
        stack = [(root, is_root)]
        while stack:
            node, at_root = stack.pop()
            verdict = self._classify_node(node, at_root)
            if verdict:
                kind, reason = verdict
                if at_root:
                    # on ne déplace jamais le dossier source lui-même : avertissement seulement
                    self._result.warnings.append((f"scan.root_is_{kind}", node.path))
                    continue
                self._bundle(node, kind, reason)
                continue
            self._result.entries.extend(node.files)
            for child in reversed(node.children):
                stack.append((child, False))


def scan(roots: list[str], options: ScanOptions, control: TaskControl | None = None) -> ScanResult:
    return Scanner(options, control).scan(roots)
