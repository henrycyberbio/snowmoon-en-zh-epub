# -*- coding: utf-8 -*-
"""Behaviour tests for input preparation, adopted-edit application and the merge.

The fixture is the shared synthetic shape agreed for this repo: one chapter 02
with a body segment 02-057, a note 02-057, and one figure whose Chinese asset
carries hidden <title>/<desc> metadata. Each test materialises that fixture in a
temporary directory, so the tests never touch real inputs and never write inside
the repository.

Run from the repository root:

    uv run python -m unittest discover -s tests -p "test_book*.py" -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREPARE = os.path.join(ROOT, "book", "prepare_inputs.py")
BUILD = os.path.join(ROOT, "book", "build_full.py")

SNAPSHOT = """<!DOCTYPE html>
<html><body><div class="document-page">
<h1>Chapter Two</h1>
<p>Body text before.</p>
<div class="device-view"><svg viewBox="0 0 100 100" role="img"><rect x="0" y="0" width="100" height="100" fill="#0a0e27"></rect><text x="5" y="20">LABEL</text></svg></div>
</div></body></html>
"""

EN_SVG = ('<?xml version="1.0" encoding="UTF-8"?>\n'
          '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
          '<rect x="0" y="0" width="100" height="100" fill="#0a0e27"/>'
          '<rect x="0" y="0" width="100" height="100" fill="#05070f"/>'
          '<text x="5" y="20">LABEL</text></svg>\n')

ZH_SVG = ('<?xml version="1.0" encoding="UTF-8"?>\n'
          '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
          '<title id="fig-title">TITLE_BEFORE</title>'
          '<desc id="fig-desc">DESC_BEFORE</desc>'
          '<rect x="0" y="0" width="100" height="100" fill="#0a0e27"/>'
          '<rect x="0" y="0" width="100" height="100" fill="#05070f"/>'
          '<text x="5" y="20">LABEL</text></svg>\n')

STYLE = "# Style\n\n## 基础统一术语\n\nchapter 章；body 正文\n"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def change(scope, ident, before, after, **kw):
    c = {"chapter": "02", "id": ident, "scope": scope, "before": before, "after": after,
         "evidence_en": "evidence", "reason": "reason"}
    c.update(kw)
    return c


class Fixture:
    """Materialise the shared synthetic chapter-02 input tree."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.snap = os.path.join(tmp, "inputs", "snapshot-20261023T101827Z", "html")
        self.arch = os.path.join(tmp, "inputs", "upstream")
        self.out = os.path.join(tmp, "work", "chapters")
        self.base = os.path.join(tmp, "work", "baseline")
        self.edits_path = os.path.join(tmp, "edits.json")
        write(os.path.join(self.snap, "chapter-2.html"), SNAPSHOT)
        write(os.path.join(self.arch, "STYLE.md"), STYLE)
        write(os.path.join(self.arch, "figure_terms.json"), "{}")
        write(os.path.join(self.arch, "source", "chapter-02.json"), json.dumps({
            "chapter": 2, "url": "https://example.invalid/ch2", "sha256": "0" * 64,
            "segments": [
                {"id": "02-001", "type": "heading", "en": "Chapter Two"},
                {"id": "02-057", "type": "body", "en": "Body text before."},
                {"id": "02-003", "type": "body", "en": "Figure (assets/ch02-fig01.svg)"},
            ],
            "figures": [{"file": "ch02-fig01.svg", "labels": ["LABEL"]}],
        }, ensure_ascii=False))
        write(os.path.join(self.arch, "zh", "chapter-02.json"), json.dumps({
            "chapter": 2,
            "segments": [{"id": "02-001", "zh": "第二章"},
                         {"id": "02-057", "zh": "BEFORE"},
                         {"id": "02-003", "zh": "图一"}],
            "notes": [{"id": "02-057", "note": "NOTE_BEFORE"}],
            "terms": {},
        }, ensure_ascii=False))
        write(os.path.join(self.arch, "assets-original", "ch02-fig01.svg"), EN_SVG)
        write(os.path.join(self.arch, "assets", "ch02-fig01.svg"), ZH_SVG)
        self.set_changes(self.default_changes())

    def default_changes(self):
        return [
            change("body", "02-057", "BEFORE", "AFTER"),
            change("note", "02-057", "NOTE_BEFORE", "NOTE_AFTER"),
            change("svg_metadata", "02-057", "TITLE_BEFORE", "标题之后",
                   asset="assets/ch02-fig01-zh.svg", element="title"),
            change("svg_metadata", "02-057", "DESC_BEFORE", "描述之后",
                   asset="assets/ch02-fig01-zh.svg", element="desc"),
        ]

    def set_changes(self, changes, state=None):
        write(self.edits_path, json.dumps({
            "changes": changes,
            "chapter_state": state if state is not None else {"02": {"reviewed": True, "reviewed_targets": 37}},
        }, ensure_ascii=False))

    def prepare(self, *extra, edits=None):
        cmd = [sys.executable, PREPARE,
               "--snapshot", self.snap, "--archive", self.arch,
               "--edits", self.edits_path if edits is None else edits,
               "--chapters", "2", "--out-root", self.out, "--baseline-root", self.base]
        cmd.extend(extra)
        return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)

    # -- readers -----------------------------------------------------------
    def zh(self):
        with open(os.path.join(self.out, "02", "zh.json"), encoding="utf-8") as f:
            return json.load(f)

    def baseline(self):
        with open(os.path.join(self.base, "02", "zh.json"), encoding="utf-8") as f:
            return json.load(f)

    def revision(self):
        with open(os.path.join(self.out, "02", "revision.json"), encoding="utf-8") as f:
            return json.load(f)

    def run_record(self):
        with open(os.path.join(self.out, "_prepare-run.json"), encoding="utf-8") as f:
            return json.load(f)

    def svg(self, name="ch02-fig01-zh.svg"):
        with open(os.path.join(self.out, "02", "assets", name), encoding="utf-8", newline="") as f:
            return f.read()


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="snowmoon-book-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fx = Fixture(self.tmp)

    def ok(self, res):
        self.assertEqual(res.returncode, 0, "prepare failed:\n%s\n%s" % (res.stdout, res.stderr))

    # ---------------------------------------------------------------- happy path
    def test_all_three_scopes_applied(self):
        self.ok(self.fx.prepare())
        zh = self.fx.zh()
        segs = {s["id"]: s["zh"] for s in zh["segments"]}
        self.assertEqual(segs["02-057"], "AFTER")
        notes = {n["id"]: n["note"] for n in zh["notes"]}
        self.assertEqual(notes["02-057"], "NOTE_AFTER")
        svg = self.fx.svg()
        self.assertIn("<title id=\"fig-title\">标题之后</title>", svg)
        self.assertIn("<desc id=\"fig-desc\">描述之后</desc>", svg)
        # the immutable baseline keeps the archive text
        base = {s["id"]: s["zh"] for s in self.fx.baseline()["segments"]}
        self.assertEqual(base["02-057"], "BEFORE")

    def test_svg_edit_preserves_non_metadata_bytes(self):
        self.ok(self.fx.prepare())
        before, after = ZH_SVG, self.fx.svg()
        head = '<title id="fig-title">'
        tail = '</desc>'
        self.assertEqual(after[:after.index(head)], before[:before.index(head)])
        self.assertEqual(after[after.index(tail):], before[before.index(tail):])
        self.assertEqual(after.count("<title"), 1)
        self.assertEqual(after.count("<desc"), 1)

    def test_revision_state_is_derived(self):
        self.ok(self.fx.prepare())
        rev = self.fx.revision()
        self.assertTrue(rev["reviewed"])
        self.assertTrue(rev["revised"])
        self.assertEqual(rev["edits"], 1)
        self.assertEqual(rev["scope_counts"], {"body": 1, "note": 1, "svg_metadata": 2})
        self.assertEqual(rev["reviewed_targets"], 37)

    # ------------------------------------------------------------------ reruns
    def test_rerun_does_not_double_notes_or_drift(self):
        self.ok(self.fx.prepare())
        first = self.fx.zh()
        first_svg = self.fx.svg()
        self.ok(self.fx.prepare())
        second = self.fx.zh()
        self.assertEqual(len(first["notes"]), len(second["notes"]))
        self.assertEqual([n["note"] for n in second["notes"]], ["NOTE_AFTER"])
        self.assertEqual(first["segments"], second["segments"])
        self.assertEqual(first_svg, self.fx.svg())
        self.assertEqual(self.fx.run_record()["edits_applied"], 4)
        self.assertEqual(self.fx.run_record()["edits_failed"], [])

    def test_rerun_writes_identical_revision_state(self):
        """A second prepare of the same edits must not drift or touch the baseline."""
        self.ok(self.fx.prepare())
        first_rev, first_zh, first_base = self.fx.revision(), self.fx.zh(), self.fx.baseline()
        first_svg = self.fx.svg()
        self.ok(self.fx.prepare())
        second_rev = self.fx.revision()
        first_rev.pop("generated_at")
        second_rev.pop("generated_at")
        self.assertEqual(first_rev, second_rev)
        self.assertEqual(first_zh, self.fx.zh())
        self.assertEqual(first_base, self.fx.baseline())
        self.assertEqual(first_svg, self.fx.svg())

    # ------------------------------------------------- revision.json full write
    def test_zero_change_unreviewed_chapter_writes_revision_state(self):
        """Every prepared chapter gets revision.json, including unreviewed zero-change ones."""
        self.fx.set_changes([], {})
        self.ok(self.fx.prepare())
        rev = self.fx.revision()
        self.assertFalse(rev["reviewed"])
        self.assertFalse(rev["revised"])
        self.assertEqual(rev["edits"], 0)
        self.assertEqual(rev["scope_counts"], {"body": 0, "note": 0, "svg_metadata": 0})
        self.assertEqual(rev["adopted_ids"], [])

    def test_zero_change_reviewed_chapter_writes_revision_state(self):
        """A reviewed chapter with no adopted edits keeps its reviewed facts."""
        self.fx.set_changes([], {"02": {"reviewed": True, "reviewed_targets": 37}})
        self.ok(self.fx.prepare())
        rev = self.fx.revision()
        self.assertTrue(rev["reviewed"])
        self.assertFalse(rev["revised"])
        self.assertEqual(rev["edits"], 0)
        self.assertEqual(rev["scope_counts"], {"body": 0, "note": 0, "svg_metadata": 0})
        self.assertEqual(rev["adopted_ids"], [])
        self.assertEqual(rev["reviewed_targets"], 37)

    def test_withdrawing_all_edits_clears_stale_revision_state(self):
        """Withdrawn edits revert the text and must not leave a revised record behind."""
        self.ok(self.fx.prepare())
        self.assertTrue(self.fx.revision()["revised"])
        # the review pass that withdrew the edits no longer lists chapter 02 at all
        self.fx.set_changes([], {})
        self.ok(self.fx.prepare())
        zh = self.fx.zh()
        self.assertEqual({s["id"]: s["zh"] for s in zh["segments"]}["02-057"], "BEFORE")
        self.assertEqual({n["id"]: n["note"] for n in zh["notes"]}["02-057"], "NOTE_BEFORE")
        svg = self.fx.svg()
        self.assertIn(">TITLE_BEFORE<", svg)
        self.assertIn(">DESC_BEFORE<", svg)
        rev = self.fx.revision()
        self.assertFalse(rev["reviewed"])
        self.assertFalse(rev["revised"])
        self.assertEqual(rev["edits"], 0)
        self.assertEqual(rev["scope_counts"], {"body": 0, "note": 0, "svg_metadata": 0})
        self.assertEqual(rev["adopted_ids"], [])
        self.assertEqual(self.fx.run_record()["edits_applied"], 0)
        base = {s["id"]: s["zh"] for s in self.fx.baseline()["segments"]}
        self.assertEqual(base["02-057"], "BEFORE")

    def test_withdrawal_keeps_reviewed_state(self):
        """Removing the edits of a reviewed chapter must not mark it unreviewed."""
        self.ok(self.fx.prepare())
        self.fx.set_changes([], {"02": {"reviewed": True, "reviewed_targets": 37}})
        self.ok(self.fx.prepare())
        rev = self.fx.revision()
        self.assertTrue(rev["reviewed"])
        self.assertFalse(rev["revised"])
        self.assertEqual(rev["reviewed_targets"], 37)

    # ------------------------------------------------------------------ failures
    def test_wrong_before_fails_and_leaves_text_alone(self):
        ch = self.fx.default_changes()
        ch[0]["before"] = "SOMETHING ELSE"
        self.fx.set_changes(ch)
        res = self.fx.prepare()
        self.assertEqual(res.returncode, 1)
        self.assertIn("before text does not match", res.stdout)
        zh = self.fx.zh()
        self.assertEqual({s["id"]: s["zh"] for s in zh["segments"]}["02-057"], "BEFORE")

    def test_missing_target_fails(self):
        self.fx.set_changes([change("body", "02-999", "BEFORE", "AFTER")])
        res = self.fx.prepare()
        self.assertEqual(res.returncode, 1)
        self.assertIn("expected exactly 1 target, found 0", res.stdout)

    def test_duplicate_note_target_fails(self):
        zh_path = os.path.join(self.fx.arch, "zh", "chapter-02.json")
        doc = json.load(open(zh_path, encoding="utf-8"))
        doc["notes"].append({"id": "02-057", "note": "NOTE_BEFORE"})
        write(zh_path, json.dumps(doc, ensure_ascii=False))
        res = self.fx.prepare()
        self.assertEqual(res.returncode, 1)
        self.assertIn("expected exactly 1 target, found 2", res.stdout)

    def test_duplicate_svg_element_fails(self):
        two = ZH_SVG.replace("<desc", "<title>dup</title><desc")
        write(os.path.join(self.fx.arch, "assets", "ch02-fig01.svg"), two)
        res = self.fx.prepare()
        self.assertEqual(res.returncode, 1)
        self.assertIn("expected exactly 1 <title>, found 2", res.stdout)

    def test_missing_asset_fails(self):
        self.fx.set_changes([change("svg_metadata", "02-057", "TITLE_BEFORE", "x",
                                    asset="assets/ch02-fig09-zh.svg", element="title")])
        res = self.fx.prepare()
        self.assertEqual(res.returncode, 1)
        self.assertIn("asset missing", res.stdout)

    def test_invalid_scope_is_rejected(self):
        self.fx.set_changes([change("paragraph", "02-057", "BEFORE", "AFTER")])
        res = self.fx.prepare()
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("scope must be one of", res.stderr)

    def test_missing_edits_file_fails_instead_of_dropping_all_edits(self):
        """A mistyped --edits path must not succeed with zero edits applied."""
        missing = os.path.join(self.tmp, "edits-typo.json")
        self.assertFalse(os.path.exists(missing))
        res = self.fx.prepare(edits=missing)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("missing edits file", res.stderr)
        # the run stops before writing an unreviewed tree that looks prepared
        self.assertFalse(os.path.exists(os.path.join(self.fx.out, "02", "zh.json")))

    def test_non_string_chapter_is_rejected_not_silently_skipped(self):
        """The contract is a two-digit string; an int must fail, not pass and be missed."""
        self.fx.set_changes([change("body", "02-057", "BEFORE", "AFTER", chapter=12)])
        res = self.fx.prepare()
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("chapter must be a two-digit string", res.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.fx.out, "02", "zh.json")))


class MergeContractTest(unittest.TestCase):
    """The revision.json preparation writes must be the shape build_full reads."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="snowmoon-merge-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fx = Fixture(self.tmp)

    def test_build_full_reads_prepared_revision_state(self):
        res = self.fx.prepare()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        sys.path.insert(0, os.path.join(ROOT, "book"))
        import build_full
        import render
        ch, err = build_full.load_chapter(self.fx.out, 2)
        self.assertIsNone(err)
        state = build_full.revision_state(render, ch)
        self.assertTrue(state["reviewed"])
        self.assertTrue(state["revised"])
        self.assertEqual(state["edits"], 1)
        self.assertEqual(state["scopes"], {"body": 1, "note": 1, "svg_metadata": 2})
        self.assertEqual(state["targets"], 37)

    def test_withdrawn_chapter_is_not_declared_revised(self):
        """build_full must stop reporting a chapter as revised once its edits are gone."""
        res = self.fx.prepare()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.fx.set_changes([], {})
        res = self.fx.prepare()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        sys.path.insert(0, os.path.join(ROOT, "book"))
        import build_full
        import render
        ch, err = build_full.load_chapter(self.fx.out, 2)
        self.assertIsNone(err)
        state = build_full.revision_state(render, ch)
        # build_full's unreviewed branch returns only the two booleans
        self.assertEqual(state, {"reviewed": False, "revised": False})
        ch["rev"] = state
        html = build_full.notes_xhtml(render, [ch], "Snowmoon", "style.css")
        self.assertNotIn("最小修改", html)
        self.assertIn("未作专项校对记录", html)


if __name__ == "__main__":
    unittest.main()
