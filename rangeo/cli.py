"""Ligne de commande de Rangeo (pour les scripts et les tâches planifiées).

Exemples :
    Rangeo sort "D:\\Téléchargements" --dest "D:\\Rangement" --dry-run
    Rangeo sort "D:\\Téléchargements" --in-place --yes
    Rangeo duplicates "D:\\Photos" --mode images --action move --yes
    Rangeo history
    Rangeo undo 12 --yes
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

COMMANDS = ("sort", "duplicates", "undo", "history", "run-job", "selftest", "resume")


def _attach_console() -> None:
    """L'exe n'a pas de console : on se rattache à celle qui l'a lancé (cmd, PowerShell)."""
    if not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = sys.stdout
    except Exception:
        pass


def out(text: str = "") -> None:
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.write(text + "\n")
        stream.flush()
    except (OSError, ValueError, UnicodeEncodeError):
        try:
            stream.write(text.encode("ascii", "replace").decode() + "\n")
        except Exception:
            pass


def _progress(control, stop: threading.Event) -> None:
    from .ui.i18n import fmt_int, tr

    last = ""
    while not stop.wait(1.0):
        snap = control.snapshot()
        total = snap["total"]
        pct = f" {int(snap['done'] * 100 / total)} %" if total else ""
        line = f"  {tr('stage.' + (snap['stage'] or 'scan'))} : {fmt_int(snap['done'])}{pct}"
        if line != last and sys.stdout is not None:
            out(line)
            last = line


def _run(fn, *args):
    from .core.models import TaskControl

    control = TaskControl()
    stop = threading.Event()
    t = threading.Thread(target=_progress, args=(control, stop), daemon=True)
    t.start()
    try:
        return fn(*args, control)
    finally:
        stop.set()


def _confirm(args, question: str) -> bool:
    if getattr(args, "yes", False):
        return True
    if sys.stdin is None or not sys.stdin.isatty():
        out("→ ajoutez --yes pour exécuter sans confirmation.")
        return False
    return input(question + " [o/N] ").strip().lower() in ("o", "oui", "y", "yes")


def _profile(engine, wanted: str | None) -> dict:
    cfg = engine.config
    if wanted:
        for p in cfg.profiles:
            if p["id"] == wanted or p["name"].lower() == wanted.lower():
                return cfg.profile(p["id"])
        raise SystemExit(f"Profil inconnu : {wanted}")
    return cfg.profile(cfg.get("last_profile"))


def main(argv: list[str]) -> int:
    _attach_console()
    parser = argparse.ArgumentParser(prog="Rangeo", description="Rangeo – rangement de disques")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sort", help="ranger un ou plusieurs dossiers")
    p.add_argument("sources", nargs="+")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dest", help="dossier de rangement")
    g.add_argument("--in-place", action="store_true", help="ranger dans chaque dossier source")
    p.add_argument("--profile", help="nom ou identifiant du profil de règles")
    p.add_argument("--no-recursive", action="store_true", help="ne pas inclure les sous-dossiers")
    p.add_argument("--dry-run", action="store_true", help="afficher le plan sans rien modifier")
    p.add_argument("--yes", action="store_true", help="ne pas demander de confirmation")
    p = sub.add_parser("duplicates", help="traiter les doublons")
    p.add_argument("sources", nargs="+")
    p.add_argument("--mode", choices=["exact", "images", "music", "videos"], default="exact")
    p.add_argument("--sensitivity", choices=["strict", "normal", "loose"], default="normal")
    p.add_argument("--action", choices=["move", "trash", "hardlink"], default="move")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("undo", help="annuler une session")
    p.add_argument("session", type=int)
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("resume", help="reprendre une session interrompue")
    p.add_argument("session", type=int)
    p = sub.add_parser("history", help="afficher l'historique")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("run-job", help="exécuter un rangement planifié")
    p.add_argument("job")
    p = sub.add_parser("selftest", help="autotest")
    p.add_argument("folder")
    args = parser.parse_args(argv)

    from .core.config import Config
    from .core.engine import Engine, SortRequest
    from .ui.i18n import fmt_date, fmt_duration, fmt_int, fmt_size, kind_label, set_language, tr

    if args.command == "selftest":
        from .core import selftest

        report = selftest.run(args.folder)
        out("OK" if report["ok"] else f"ÉCHEC : {report.get('error')}")
        return 0 if report["ok"] else 1

    config = Config()
    set_language(config.get("language"))
    engine = Engine(config, kind_label=kind_label)
    try:
        if args.command == "history":
            for s in engine.journal.sessions(args.limit):
                files = s.summary.get("files", s.summary.get("done", 0))
                out(f"#{s.id:<5} {fmt_date(s.created)}  {tr('session.kind_' + s.kind):<22} "
                    f"{fmt_int(files):>8} fichiers  {tr('status.' + s.status)}")
            return 0

        if args.command == "undo":
            s = engine.journal.session(args.session)
            if s is None:
                out("Session introuvable.")
                return 1
            if not _confirm(args, f"Annuler la session #{s.id} du {fmt_date(s.created)} ?"):
                return 1
            result = _run(engine.undo, s.id)
            out(f"{fmt_int(result.undone)} opérations annulées, {result.failed} échec(s).")
            return 0 if result.failed == 0 else 2

        if args.command == "resume":
            result = _run(engine.resume, args.session)
            out(f"{tr(result.message)} — {fmt_int(result.files)} fichiers, {result.failed} erreur(s).")
            return 0 if result.failed == 0 else 2

        if args.command == "run-job":
            from .core import scheduler

            job = next((j for j in config.get("schedules", []) or [] if j.get("id") == args.job), None)
            if job is None:
                out("Rangement planifié introuvable.")
                return 1
            result = _run(scheduler.run_job, engine, job)
            if result is None:
                out("Rien à faire.")
                return 0
            out(f"{tr(result.message)} — {fmt_int(result.files)} fichiers.")
            return 0 if result.failed == 0 else 2

        sources = [os.path.abspath(s) for s in args.sources]
        if args.command == "sort":
            req = SortRequest(sources=sources, profile=_profile(engine, args.profile),
                              destination=os.path.abspath(args.dest) if args.dest else "",
                              in_place=args.in_place or not args.dest, recursive=not args.no_recursive)
            problems = engine.check_request(req)
            if problems:
                for key, path in problems:
                    out(f"✗ {tr(key)} {path}")
                return 1
            out(tr("sort.analyzing"))
            _scan, plan = _run(engine.analyze, req)
        else:
            if args.mode == "exact":
                _scan, groups = _run(engine.find_duplicates, sources)
            else:
                _scan, groups = _run(lambda c: engine.find_similar(sources, args.mode, args.sensitivity, c))
            if args.mode != "exact" and args.action == "hardlink":
                out("Les liens physiques ne sont possibles que pour les doublons exacts.")
                return 1
            plan = engine.duplicate_plan(groups, args.action)
            out(f"{len(groups)} groupe(s) de doublons.")

        active = plan.active_ops()
        for op in active[:200]:
            target = op.dst or tr("op.trash_target")
            out(f"  {op.src}\n    → {target}")
        if len(active) > 200:
            out(f"  … et {fmt_int(len(active) - 200)} autres")
        for key, detail in plan.errors:
            out(f"✗ {tr(key)} {detail}")
        out(f"{fmt_int(len(active))} élément(s), {fmt_size(sum(o.size for o in active))}.")
        if args.dry_run or not active or plan.errors:
            return 1 if plan.errors else 0
        if not _confirm(args, "Exécuter ?"):
            return 1
        start = time.monotonic()
        kind = "sort" if args.command == "sort" else "duplicates"
        cleanup = None if args.command == "sort" else False
        result = _run(lambda c: engine.execute(plan, c, kind, plan.profile_name, cleanup))
        out(f"{tr(result.message)} — {fmt_int(result.files)} fichiers en {fmt_duration(time.monotonic() - start)}, "
            f"{result.failed} erreur(s). Session #{result.session_id} (annulable : Rangeo undo {result.session_id}).")
        for path, code, _detail in result.errors[:50]:
            out(f"  ✗ {path} : {tr('error.' + code)}")
        return 0 if result.failed == 0 else 2
    finally:
        engine.close()
