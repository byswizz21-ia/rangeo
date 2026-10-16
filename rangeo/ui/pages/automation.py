"""Page « Automatisation » : surveillance, rangements planifiés, disques externes."""

from __future__ import annotations

import datetime as dt
import os

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QWidget,
)

from ...core import devices, scheduler
from ...core.models import TaskControl
from .. import icons, theme
from ..context import AppContext
from ..i18n import fmt_date, fmt_int, tr
from ..widgets import (
    Banner,
    Card,
    FolderList,
    ScrollPage,
    Toggle,
    button,
    choose_folder,
    confirm,
    error,
    hbox,
    icon_label,
    info,
    label,
    vbox,
)
from ..workers import run_task
from .watch import WatchPage

DAY_KEYS = scheduler.DAYS


def describe(job: dict) -> str:
    when = job.get("time", "20:00")
    freq = job.get("frequency", "daily")
    if freq == "weekly":
        days = ", ".join(tr(f"day.{d}") for d in job.get("days") or ["MON"])
        text = tr("schedule.desc_weekly", days=days, time=when)
    elif freq == "monthly":
        text = tr("schedule.desc_monthly", day=job.get("day", 1), time=when)
    else:
        text = tr("schedule.desc_daily", time=when)
    target = tr("sort.in_place_short") if job.get("in_place", True) or not job.get("destination") \
        else job.get("destination")
    return f"{text} · {', '.join(os.path.basename(s) or s for s in job.get('sources', []))} → {target}"


class JobDialog(QDialog):
    def __init__(self, ctx: AppContext, job: dict, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.job = dict(job)
        self.setWindowTitle(tr("schedule.edit_title"))
        self.setWindowIcon(icons.app_icon())
        self.setMinimumWidth(640)
        lay = vbox(self, (22, 18, 22, 16), 10)
        lay.addWidget(label(tr("schedule.name")))
        self.name = QLineEdit(job.get("name", ""))
        lay.addWidget(self.name)
        lay.addWidget(label(tr("schedule.what")))
        self.sources = FolderList(tr("sort.add_folder"), tr("sort.no_source"), tr("sort.choose_source"), max_height=90)
        self.sources.set_paths(job.get("sources", []))
        lay.addWidget(self.sources)
        group = QButtonGroup(self)
        self.in_place = QRadioButton(tr("sort.in_place"))
        self.to_folder = QRadioButton(tr("sort.to_folder"))
        group.addButton(self.in_place)
        group.addButton(self.to_folder)
        (self.in_place if job.get("in_place", True) else self.to_folder).setChecked(True)
        lay.addWidget(self.in_place)
        drow = hbox(spacing=6)
        drow.addWidget(self.to_folder)
        self.dest = QLineEdit(job.get("destination", ""))
        drow.addWidget(self.dest, 1)
        drow.addWidget(button(tr("common.browse"), "folder", on_click=self._browse))
        lay.addLayout(drow)
        prow = hbox(spacing=8)
        prow.addWidget(label(tr("sort.profile")))
        self.profile = QComboBox()
        for p in ctx.config.profiles:
            self.profile.addItem(p["name"], p["id"])
        self.profile.setCurrentIndex(max(0, self.profile.findData(job.get("profile"))))
        prow.addWidget(self.profile, 1)
        self.recursive = QCheckBox(tr("sort.recursive"))
        self.recursive.setChecked(bool(job.get("recursive", True)))
        prow.addWidget(self.recursive)
        lay.addLayout(prow)
        frow = hbox(spacing=8)
        frow.addWidget(label(tr("schedule.when")))
        self.freq = QComboBox()
        for f in ("daily", "weekly", "monthly"):
            self.freq.addItem(tr(f"schedule.freq_{f}"), f)
        self.freq.setCurrentIndex(max(0, self.freq.findData(job.get("frequency", "daily"))))
        frow.addWidget(self.freq)
        frow.addWidget(label(tr("schedule.at")))
        self.time = QTimeEdit(QTime.fromString(job.get("time", "20:00"), "HH:mm"))
        self.time.setDisplayFormat("HH:mm")
        frow.addWidget(self.time)
        frow.addStretch(1)
        lay.addLayout(frow)
        self.days_row = QWidget()
        dl = hbox(self.days_row, spacing=6)
        self.days = {}
        for d in DAY_KEYS:
            cb = QCheckBox(tr(f"day.{d}"))
            cb.setChecked(d in (job.get("days") or ["MON"]))
            self.days[d] = cb
            dl.addWidget(cb)
        dl.addStretch(1)
        lay.addWidget(self.days_row)
        self.month_row = QWidget()
        ml = hbox(self.month_row, spacing=6)
        ml.addWidget(label(tr("schedule.day_of_month")))
        self.day = QSpinBox()
        self.day.setRange(1, 28)
        self.day.setValue(int(job.get("day", 1)))
        ml.addWidget(self.day)
        ml.addStretch(1)
        lay.addWidget(self.month_row)
        lay.addWidget(Banner("warning", tr("schedule.warning")))
        btns = hbox(spacing=8)
        btns.addStretch(1)
        btns.addWidget(button(tr("common.cancel"), on_click=self.reject))
        btns.addWidget(button(tr("schedule.save"), "check", "primary", on_click=self._save))
        lay.addLayout(btns)
        self.freq.currentIndexChanged.connect(self._freq_changed)
        self._freq_changed()

    def _browse(self) -> None:
        path = choose_folder(self, tr("sort.choose_destination"), self.dest.text())
        if path:
            self.dest.setText(path)
            self.to_folder.setChecked(True)

    def _freq_changed(self) -> None:
        f = self.freq.currentData()
        self.days_row.setVisible(f == "weekly")
        self.month_row.setVisible(f == "monthly")

    def _save(self) -> None:
        sources = self.sources.paths()
        if not sources:
            error(self, tr("schedule.edit_title"), tr("check.no_source"))
            return
        if self.to_folder.isChecked() and not self.dest.text().strip():
            error(self, tr("schedule.edit_title"), tr("check.no_destination"))
            return
        self.job.update(
            name=self.name.text().strip() or tr("schedule.default_name"),
            sources=sources,
            in_place=self.in_place.isChecked(),
            destination=self.dest.text().strip() if self.to_folder.isChecked() else "",
            profile=self.profile.currentData(),
            recursive=self.recursive.isChecked(),
            frequency=self.freq.currentData(),
            time=self.time.time().toString("HH:mm"),
            days=[d for d, cb in self.days.items() if cb.isChecked()] or ["MON"],
            day=self.day.value(),
        )
        self.accept()


class ScheduleTab(ScrollPage):
    def __init__(self, ctx: AppContext):
        super().__init__(max_width=None)
        self.ctx = ctx
        self.widget().layout().setContentsMargins(0, 12, 0, 0)
        b = self.body
        b.addWidget(Banner("info", tr("schedule.how")))
        self.card = Card(tr("schedule.title"), tr("schedule.subtitle"), "clock")
        self.rows = vbox(spacing=6)
        self.card.add(self.rows)
        row = hbox(spacing=8)
        row.addWidget(button(tr("schedule.new"), "plus", "primary", on_click=self._new))
        row.addStretch(1)
        self.card.add(row)
        b.addWidget(self.card)
        b.addStretch(1)
        self.fill()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.fill()

    def jobs(self) -> list[dict]:
        return self.ctx.config.get("schedules", []) or []

    def fill(self) -> None:
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        jobs = self.jobs()
        if not jobs:
            self.rows.addWidget(label(tr("schedule.empty"), "Muted", wrap=True))
        c = theme.colors()
        for job in jobs:
            w = QWidget()
            lay = hbox(w, (0, 4, 0, 4), 10)
            toggle = Toggle(job.get("enabled", True))
            toggle.toggled.connect(lambda on, j=job: self._toggle(j, on))
            lay.addWidget(toggle)
            lay.addWidget(icon_label("clock", c["accent"], 20))
            texts = vbox(spacing=1)
            texts.addWidget(label(job.get("name", ""), "SectionTitle"))
            texts.addWidget(label(describe(job), "Faint", wrap=True))
            nxt = scheduler.next_run(job)
            status = tr("schedule.next", when=fmt_date(nxt.timestamp())) if nxt else tr("schedule.disabled")
            if job.get("last_run"):
                status += " · " + tr("schedule.last", when=fmt_date(job["last_run"]))
            texts.addWidget(label(status, "Faint"))
            lay.addLayout(texts, 1)
            lay.addWidget(button(tr("schedule.run_now"), "play", "ghost", on_click=lambda _=False, j=job: self._run(j)))
            lay.addWidget(button("", "edit", "ghost", tooltip=tr("schedule.edit_title"),
                                 on_click=lambda _=False, j=job: self._edit(j)))
            lay.addWidget(button("", "trash", "ghost", tooltip=tr("common.delete"),
                                 on_click=lambda _=False, j=job: self._delete(j)))
            self.rows.addWidget(w)

    def _store(self, jobs: list[dict]) -> None:
        self.ctx.config.set("schedules", jobs)
        errors = []
        for job in jobs:
            try:
                scheduler.install(job)
            except OSError as exc:
                errors.append(f"{job.get('name')} : {exc}")
        if errors:
            error(self, tr("schedule.title"), tr("schedule.install_failed"), "\n".join(errors))
        self.fill()

    def _new(self) -> None:
        sources = []
        from PySide6.QtCore import QStandardPaths

        dl = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        if dl:
            sources = [os.path.normpath(dl)]
        profile = "downloads" if any(p["id"] == "downloads" for p in self.ctx.config.profiles) else None
        job = scheduler.new_job(tr("schedule.default_name"), sources, profile or self.ctx.config.profiles[0]["id"])
        dlg = JobDialog(self.ctx, job, self)
        if dlg.exec():
            self._store(self.jobs() + [dlg.job])

    def _edit(self, job: dict) -> None:
        dlg = JobDialog(self.ctx, job, self)
        if dlg.exec():
            self._store([dlg.job if j["id"] == job["id"] else j for j in self.jobs()])

    def _toggle(self, job: dict, on: bool) -> None:
        jobs = self.jobs()
        for j in jobs:
            if j["id"] == job["id"]:
                j["enabled"] = on
        self._store(jobs)

    def _delete(self, job: dict) -> None:
        if confirm(self, tr("common.delete"), tr("schedule.delete_text", name=job.get("name")), tr("common.delete"),
                   danger=True):
            scheduler.remove(job["id"])
            self.ctx.config.set("schedules", [j for j in self.jobs() if j["id"] != job["id"]])
            self.fill()

    def _run(self, job: dict) -> None:
        if self.ctx.busy:
            error(self, tr("common.busy_title"), tr("common.busy_text", task=self.ctx.busy_label))
            return
        self.ctx.set_busy(True, tr("busy.sort"))

        def done(result):
            self.ctx.set_busy(False)
            self.ctx.sessionsChanged.emit()
            self.fill()
            if result is None:
                info(self, job.get("name", ""), tr("schedule.nothing"))
            else:
                info(self, tr(result.message), tr("sort.notify_done", n=fmt_int(result.files)))

        def failed(exc):
            self.ctx.set_busy(False)
            error(self, job.get("name", ""), str(exc), getattr(exc, "traceback_text", None))

        run_task(scheduler.run_job, self.ctx.engine, job, TaskControl(), on_done=done, on_error=failed)


class DevicesTab(ScrollPage):
    def __init__(self, ctx: AppContext):
        super().__init__(max_width=None)
        self.ctx = ctx
        self.widget().layout().setContentsMargins(0, 12, 0, 0)
        b = self.body
        b.addWidget(Banner("info", tr("devices.how")))
        opt = Card(margins=(16, 12, 16, 12))
        row = hbox(spacing=12)
        row.addWidget(label(tr("devices.ask_unknown")), 1)
        self.ask = Toggle(bool(ctx.config.get("devices_ask_unknown", True)))
        self.ask.toggled.connect(lambda on: ctx.config.set("devices_ask_unknown", on))
        row.addWidget(self.ask)
        opt.add(row)
        b.addWidget(opt)
        self.card = Card(tr("devices.title"), tr("devices.subtitle"), "drive")
        self.rows = vbox(spacing=6)
        self.card.add(self.rows)
        self.card.add(button(tr("history.refresh"), "refresh", "ghost", on_click=self.fill))
        b.addWidget(self.card)
        b.addStretch(1)
        self.fill()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.fill()

    def rules(self) -> list[dict]:
        return self.ctx.config.get("devices", []) or []

    def fill(self) -> None:
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        sysdrive = (os.environ.get("SystemDrive") or "C:").upper()
        connected = [v for v in devices.volumes() if not v.root.upper().startswith(sysdrive) and v.kind != "network"]
        known = {r.get("serial"): r for r in self.rules()}
        shown = set()
        for vol in connected:
            shown.add(vol.serial)
            self.rows.addWidget(self._row(vol.serial, vol.display, True, known.get(vol.serial)))
        for serial, rule in known.items():
            if serial not in shown:
                self.rows.addWidget(self._row(serial, rule.get("label", serial), False, rule))
        if not connected and not known:
            self.rows.addWidget(label(tr("devices.none"), "Muted", wrap=True))

    def _row(self, serial: str, name: str, connected: bool, rule: dict | None) -> QWidget:
        rule = dict(rule or {"serial": serial, "label": name, "action": "none", "profile": "general",
                             "subfolder": "", "destination": ""})
        c = theme.colors()
        w = QWidget()
        lay = vbox(w, (0, 6, 0, 6), 4)
        top = hbox(spacing=10)
        top.addWidget(icon_label("drive", c["accent"] if connected else c["text3"], 20))
        top.addWidget(label(name, "SectionTitle"))
        top.addWidget(label(tr("devices.connected") if connected else tr("devices.disconnected"), "Faint"))
        top.addStretch(1)
        action = QComboBox()
        for a in ("none", "ask", "auto"):
            action.addItem(tr(f"devices.action_{a}"), a)
        action.setCurrentIndex(max(0, action.findData(rule.get("action", "none"))))
        top.addWidget(action)
        lay.addLayout(top)
        detail = hbox(spacing=6)
        detail.setContentsMargins(30, 0, 0, 0)
        detail.addWidget(label(tr("devices.subfolder")))
        sub = QLineEdit(rule.get("subfolder", ""))
        sub.setPlaceholderText(tr("devices.subfolder_hint"))
        sub.setMaximumWidth(160)
        detail.addWidget(sub)
        detail.addWidget(label(tr("sort.profile")))
        profile = QComboBox()
        for p in self.ctx.config.profiles:
            profile.addItem(p["name"], p["id"])
        profile.setCurrentIndex(max(0, profile.findData(rule.get("profile"))))
        detail.addWidget(profile)
        detail.addWidget(label(tr("devices.destination")))
        dest = QLineEdit(rule.get("destination", ""))
        dest.setPlaceholderText(tr("devices.dest_hint"))
        detail.addWidget(dest, 1)
        browse = button("", "folder", tooltip=tr("common.browse"))
        detail.addWidget(browse)
        lay.addLayout(detail)

        def save(*_):
            rule.update(action=action.currentData(), subfolder=sub.text().strip(), profile=profile.currentData(),
                        destination=dest.text().strip(), label=name)
            rules = [r for r in self.rules() if r.get("serial") != serial]
            if rule["action"] != "none" or rule["destination"] or rule["subfolder"]:
                rules.append(rule)
            self.ctx.config.set("devices", rules)

        def pick():
            path = choose_folder(self, tr("sort.choose_destination"), dest.text())
            if path:
                dest.setText(path)
                save()

        action.currentIndexChanged.connect(save)
        profile.currentIndexChanged.connect(save)
        sub.editingFinished.connect(save)
        dest.editingFinished.connect(save)
        browse.clicked.connect(pick)
        return w


class AutomationPage(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        outer = vbox(self, (28, 22, 28, 18), 10)
        outer.addWidget(label(tr("auto.title"), "PageTitle"))
        outer.addWidget(label(tr("auto.subtitle"), "PageSubtitle", wrap=True))
        self.tabs = QTabWidget()
        self.watch = WatchPage(ctx, embedded=True)
        self.schedule = ScheduleTab(ctx)
        self.devices = DevicesTab(ctx)
        self.tabs.addTab(self.watch, icons.icon("eye"), tr("auto.tab_watch"))
        self.tabs.addTab(self.schedule, icons.icon("clock"), tr("auto.tab_schedule"))
        self.tabs.addTab(self.devices, icons.icon("drive"), tr("auto.tab_devices"))
        outer.addWidget(self.tabs, 1)

    def receive(self, payload) -> None:
        if payload and payload.get("tab") is not None:
            self.tabs.setCurrentIndex(int(payload["tab"]))


__all__ = ["AutomationPage", "dt", "Qt"]
