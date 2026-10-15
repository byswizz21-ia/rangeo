"""Rapports de fin de session (CSV et HTML autonome)."""

from __future__ import annotations

import csv
import datetime as dt
import html

from .fsutil import human_size
from .journal import Journal


def export_csv(journal: Journal, session_id: int, path: str, tr=lambda k, **kw: k) -> None:
    rows = journal.ops(session_id)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow([tr("report.col_op"), tr("report.col_state"), tr("report.col_src"), tr("report.col_dst"),
                         tr("report.col_size"), tr("report.col_rule"), tr("report.col_error")])
        for r in rows:
            writer.writerow([tr(f"op.{r.op}"), tr(f"state.{r.state}"), r.src or "", r.dst or "", r.size, r.rule or "",
                             _error_text(r.error, tr)])


def _error_text(error: str | None, tr) -> str:
    if not error:
        return ""
    code, _, detail = error.partition("|")
    text = tr(f"error.{code}")
    return f"{text} ({detail})" if detail and detail not in (code, "in_use") else text


def export_html(journal: Journal, session_id: int, path: str, tr=lambda k, **kw: k, lang: str = "fr") -> None:
    session = journal.session(session_id)
    rows = journal.ops(session_id)
    counts = journal.counts(session_id)
    created = dt.datetime.fromtimestamp(session.created).strftime("%d/%m/%Y %H:%M") if session else ""
    total = sum(r.size for r in rows if r.state == "done" and r.op in ("move", "copy"))
    esc = html.escape

    def row_html(r):
        cls = {"done": "ok", "failed": "err", "undone": "undo"}.get(r.state, "")
        return (f"<tr class='{cls}'><td>{esc(tr(f'op.{r.op}'))}</td><td>{esc(tr(f'state.{r.state}'))}</td>"
                f"<td>{esc(r.src or '')}</td><td>{esc(r.dst or '')}</td><td>{esc(human_size(r.size, lang))}</td>"
                f"<td>{esc(_error_text(r.error, tr))}</td></tr>")

    errors = [r for r in rows if r.state == "failed"]
    doc = f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<title>Rangeo – {esc(tr('report.title'))} #{session_id}</title>
<style>
body{{font-family:'Segoe UI',system-ui,sans-serif;margin:32px;color:#1d2433;background:#f6f7fb}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#667;margin-bottom:24px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}}
.card{{background:#fff;border-radius:10px;padding:14px 18px;box-shadow:0 1px 3px #0001;min-width:140px}}
.card b{{display:block;font-size:22px}} table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px}}
th,td{{padding:6px 8px;border-bottom:1px solid #e4e7ee;text-align:left;word-break:break-all}}
th{{background:#eef1f7;position:sticky;top:0}} tr.err td{{background:#fff1f1}} tr.undo td{{color:#889}}
h2{{font-size:17px;margin-top:28px}}
</style></head><body>
<h1>{esc(tr('report.title'))} #{session_id}</h1>
<div class="sub">{esc(created)} — {esc((session.profile or '') if session else '')}</div>
<div class="cards">
<div class="card"><b>{counts.get('done', 0)}</b>{esc(tr('report.done'))}</div>
<div class="card"><b>{counts.get('failed', 0)}</b>{esc(tr('report.failed'))}</div>
<div class="card"><b>{esc(human_size(total, lang))}</b>{esc(tr('report.moved'))}</div>
</div>
{"<h2>" + esc(tr('report.errors')) + "</h2><table><tr><th>" + esc(tr('report.col_op')) + "</th><th>"
 + esc(tr('report.col_state')) + "</th><th>" + esc(tr('report.col_src')) + "</th><th>" + esc(tr('report.col_dst'))
 + "</th><th>" + esc(tr('report.col_size')) + "</th><th>" + esc(tr('report.col_error')) + "</th></tr>"
 + "".join(row_html(r) for r in errors) + "</table>" if errors else ""}
<h2>{esc(tr('report.all_ops'))}</h2>
<table><tr><th>{esc(tr('report.col_op'))}</th><th>{esc(tr('report.col_state'))}</th><th>{esc(tr('report.col_src'))}</th>
<th>{esc(tr('report.col_dst'))}</th><th>{esc(tr('report.col_size'))}</th><th>{esc(tr('report.col_error'))}</th></tr>
{"".join(row_html(r) for r in rows)}
</table></body></html>"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
