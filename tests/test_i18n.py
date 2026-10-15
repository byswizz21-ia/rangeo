"""Les traductions FR/EN sont complètes et couvrent toutes les clés utilisées par le code."""

import json
import os
import re

from rangeo.core import filetypes as ft
from rangeo.core import rules as R
from rangeo.core.config import CONFLICT_STRATEGIES, DUP_ACTIONS, DUP_CRITERIA, IDENTICAL_STRATEGIES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N = os.path.join(ROOT, "rangeo", "i18n")


def load(lang):
    with open(os.path.join(I18N, f"{lang}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_same_keys_in_all_languages():
    fr, en = load("fr"), load("en")
    assert set(fr) - set(en) == set(), "manquant en anglais"
    assert set(en) - set(fr) == set(), "manquant en français"


def test_placeholders_match():
    fr, en = load("fr"), load("en")
    examples = {"rules.target_placeholder", "rules.rename_placeholder", "rules.template_help"}  # modèles d'exemple
    for key in fr:
        if key in examples:
            continue
        assert set(re.findall(r"\{(\w+)\}", fr[key])) == set(re.findall(r"\{(\w+)\}", en[key])), key


def test_static_keys_exist():
    fr = load("fr")
    pattern = re.compile(r"\b(?:tr|plural)\(\s*\"([a-z_0-9.]+)\"")
    missing = set()
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "rangeo")):
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    for key in pattern.findall(fh.read()):
                        if key not in fr and f"{key}.one" not in fr:
                            missing.add(key)
    assert not missing, sorted(missing)


def test_dynamic_families_exist():
    fr = load("fr")
    expected = []
    expected += [f"kind.{k}" for k in ft.KINDS]
    expected += [f"group.{g}" for g in ft.GROUPS]
    expected += [f"field.{f}" for f in R.FIELD_TYPES]
    expected += [f"cond.{o}" for ops in R.OPERATORS.values() for o in ops]
    expected += [f"action.{a}" for a in R.ACTIONS]
    expected += [f"var.{v}" for v in set(R.VARIABLES.values())]
    expected += [f"conflict.{c}" for c in CONFLICT_STRATEGIES]
    expected += [f"identical.{c}" for c in IDENTICAL_STRATEGIES]
    expected += [f"dups.action_{a}" for a in DUP_ACTIONS] + [f"dups.action_{a}_hint" for a in DUP_ACTIONS]
    expected += [f"dups.confirm_{a}" for a in DUP_ACTIONS]
    expected += [f"dup_keep.{c}" for c in DUP_CRITERIA]
    expected += [f"stage.{s}" for s in ("scan", "analyze", "plan", "execute", "undo", "dup_partial", "dup_full")]
    expected += [f"status.{s}" for s in ("running", "paused", "completed", "completed_errors", "cancelled",
                                         "interrupted", "undone", "partially_undone")]
    expected += [f"state.{s}" for s in ("planned", "running", "done", "failed", "skipped", "undone", "undo_failed")]
    expected += [f"op.{o}" for o in ("move", "copy", "trash", "hardlink", "mkdir", "rmdir", "skip")]
    expected += [f"exec.{m}" for m in ("done", "done_errors", "cancelled", "device_gone", "disk_full")]
    expected += [f"exec.{m}_text" for m in ("done", "done_errors", "cancelled", "device_gone", "disk_full")]
    expected += [f"error.{e}" for e in ("verify_failed", "source_changed", "not_identical", "source_missing",
                                        "target_exists", "locked", "access_denied", "disk_full", "device_gone",
                                        "path_too_long", "other")]
    expected += [f"bundle.{k}" for k in ft.BUNDLE_KINDS]
    expected += [f"scan.root_is_{k}" for k in ft.BUNDLE_KINDS]
    expected += [f"skip.{r}.one" for r in ("hidden", "partial", "excluded", "link", "protected")]
    # notes du planificateur
    src = open(os.path.join(ROOT, "rangeo", "core", "planner.py"), encoding="utf-8").read()
    expected += sorted(set(re.findall(r"\"(plan\.[a-z_]+)\"", src)))
    src = open(os.path.join(ROOT, "rangeo", "core", "scanner.py"), encoding="utf-8").read()
    expected += [f"bundle_reason.{r}" for r in set(re.findall(r"\"([a-z_]+_marker|technical_files|marked)\"", src))]
    missing = [k for k in expected if k not in fr]
    assert not missing, missing
