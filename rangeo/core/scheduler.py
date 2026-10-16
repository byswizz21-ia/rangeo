"""Rangements planifiés via le Planificateur de tâches de Windows.

Chaque tâche lance « Rangeo run-job <id> » (sans interface). Les tâches sont créées dans le dossier
« \\Rangeo\\ » du Planificateur, avec « exécuter dès que possible si l'heure a été manquée ».
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from xml.sax.saxutils import escape

from .engine import Engine, SortRequest
from .executor import ExecResult
from .models import TaskControl

log = logging.getLogger(__name__)

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
_XML_DAYS = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday", "FRI": "Friday",
             "SAT": "Saturday", "SUN": "Sunday"}
_NO_WINDOW = 0x08000000


def new_job(name: str, sources: list[str], profile: str) -> dict:
    return {
        "id": uuid.uuid4().hex[:10],
        "name": name,
        "enabled": True,
        "sources": sources,
        "profile": profile,
        "destination": "",
        "in_place": True,
        "recursive": True,
        "frequency": "daily",  # daily | weekly | monthly
        "time": "20:00",
        "days": ["MON"],
        "day": 1,
        "last_run": None,
        "last_session": None,
    }


def task_path(job_id: str) -> str:
    return f"\\Rangeo\\Rangeo-{job_id}"


def launcher() -> tuple[str, list[str]]:
    """Programme et arguments qui lancent Rangeo sans fenêtre."""
    if getattr(sys, "frozen", False):
        return sys.executable, []
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "__main__.py")
    return (pythonw if os.path.exists(pythonw) else sys.executable), [main]


def task_xml(job: dict) -> str:
    exe, base_args = launcher()
    args = " ".join(f'"{a}"' for a in base_args + ["run-job", job["id"]])
    hh, mm = (job.get("time") or "20:00").split(":")[:2]
    start = dt.datetime.now().replace(hour=int(hh), minute=int(mm), second=0, microsecond=0).isoformat()
    freq = job.get("frequency", "daily")
    if freq == "weekly":
        days = "".join(f"<{_XML_DAYS[d]} />" for d in job.get("days") or ["MON"] if d in _XML_DAYS)
        schedule = f"<ScheduleByWeek><DaysOfWeek>{days}</DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>"
    elif freq == "monthly":
        day = max(1, min(28, int(job.get("day") or 1)))
        months = "".join(f"<{m} />" for m in ("January", "February", "March", "April", "May", "June", "July",
                                             "August", "September", "October", "November", "December"))
        schedule = (f"<ScheduleByMonth><DaysOfMonth><Day>{day}</Day></DaysOfMonth>"
                    f"<Months>{months}</Months></ScheduleByMonth>")
    else:
        schedule = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{escape("Rangeo : " + job.get("name", ""))}</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger><StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>{schedule}</CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType>
    <RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>{"true" if job.get("enabled", True) else "false"}</Enabled>
    <ExecutionTimeLimit>PT12H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(exe)}</Command><Arguments>{escape(args)}</Arguments></Exec></Actions>
</Task>"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, creationflags=_NO_WINDOW)


def install(job: dict) -> None:
    """Crée ou met à jour la tâche Windows correspondant à ce rangement planifié."""
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-16") as fh:
            fh.write(task_xml(job))
        result = _schtasks("/Create", "/TN", task_path(job["id"]), "/XML", path, "/F")
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or result.stdout.strip() or "schtasks")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def remove(job_id: str) -> None:
    _schtasks("/Delete", "/TN", task_path(job_id), "/F")


def exists(job_id: str) -> bool:
    return _schtasks("/Query", "/TN", task_path(job_id)).returncode == 0


def sync(jobs: list[dict], previous_ids: set[str] | None = None) -> list[str]:
    """Aligne le Planificateur sur la configuration. Renvoie les erreurs."""
    errors = []
    for job in jobs:
        try:
            install(job)
        except OSError as exc:
            errors.append(f"{job.get('name')}: {exc}")
    for old in (previous_ids or set()) - {j["id"] for j in jobs}:
        remove(old)
    return errors


def next_run(job: dict, now: dt.datetime | None = None) -> dt.datetime | None:
    if not job.get("enabled", True):
        return None
    now = now or dt.datetime.now()
    hh, mm = (job.get("time") or "20:00").split(":")[:2]
    candidate = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    freq = job.get("frequency", "daily")
    for _ in range(400):
        ok = candidate > now
        if freq == "weekly":
            ok = ok and DAYS[candidate.weekday()] in (job.get("days") or ["MON"])
        elif freq == "monthly":
            ok = ok and candidate.day == max(1, min(28, int(job.get("day") or 1)))
        if ok:
            return candidate
        candidate += dt.timedelta(days=1)
    return None


RESULTS = ("done", "done_errors", "nothing", "sources_missing", "invalid", "no_space", "interrupted")


def _record(engine: Engine, job: dict, outcome: str, result: ExecResult | None = None) -> None:
    """Note le résultat du passage dans le rangement planifié (affiché dans Automatisation)."""
    def apply(jobs):
        for j in jobs or []:
            if j.get("id") == job.get("id"):
                j["last_run"] = dt.datetime.now().timestamp()
                j["last_result"] = outcome
                j["last_files"] = result.files if result is not None else 0
                j["last_failed"] = result.failed if result is not None else 0
                if result is not None:
                    j["last_session"] = result.session_id
        return jobs or []

    try:
        engine.config.update("schedules", apply)
    except OSError:
        log.exception("Rangement planifié : résultat non enregistré")


def run_job(engine: Engine, job: dict, control: TaskControl | None = None) -> ExecResult | None:
    """Exécute un rangement planifié (sans aperçu : les règles s'appliquent directement)."""
    control = control or TaskControl()
    profile = engine.config.profile(job.get("profile"))
    sources = [s for s in job.get("sources", []) if os.path.isdir(s)]
    if not sources:
        log.warning("Rangement planifié %s : aucun dossier disponible", job.get("name"))
        _record(engine, job, "sources_missing")
        return None
    in_place = bool(job.get("in_place", True)) or not job.get("destination")
    req = SortRequest(sources=sources, profile=profile, destination=job.get("destination", ""),
                      in_place=in_place, recursive=bool(job.get("recursive", True)))
    problems = engine.check_request(req)
    if problems:
        log.warning("Rangement planifié %s impossible : %s", job.get("name"), problems)
        _record(engine, job, "invalid")
        return None
    _scan, plan = engine.analyze(req, control)
    if plan.errors:
        log.warning("Rangement planifié %s impossible : %s", job.get("name"), plan.errors)
        _record(engine, job, "no_space" if any(k == "plan.not_enough_space" for k, _ in plan.errors) else "invalid")
        return None
    if not plan.active_ops():
        _record(engine, job, "nothing")
        return None
    result = engine.execute(plan, control, kind="scheduled", profile=job.get("name", ""),
                            cleanup=profile.get("cleanup_empty", True))
    outcome = "interrupted" if result.status in ("interrupted", "cancelled") else (
        "done_errors" if result.failed else "done")
    _record(engine, job, outcome, result)
    return result
