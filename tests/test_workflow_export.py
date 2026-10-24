"""Offline validation test for the opinion exporter (workflow/export_opinions.py).

Only ``--validate-only`` is exercised: it is the gate the revision lanes actually ran.
Writing the workbook needs openpyxl and is not required to check the reconciliation
rules, so this test keeps the offline path dependency-free.

svg_metadata rows are reconciled against the fixed archive original named by
``source.json`` ``figures[].file``; the working asset is ``figures[].zh``.  No
``baseline/NN/assets`` copy is expected (prepare_inputs never writes one).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "workflow"))

import export_opinions as export  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "workflow"
CHAPTER = FIX / "chapter-02"
BASELINE_TEXT = "对军队的信心下降到 39%。"
ADOPTED_TEXT = "受访者对军队的信心跌至 39%。"
ARCHIVE_TITLE = "Chart title"
ADOPTED_TITLE = "图表标题"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


class ValidateOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        zh = json.loads((CHAPTER / "zh.json").read_text(encoding="utf-8"))
        write_json(self.root / "baseline" / "02" / "zh.json", zh)
        working = json.loads(json.dumps(zh))
        for seg in working["segments"]:
            if seg["id"] == "02-002":
                seg["zh"] = ADOPTED_TEXT
        write_json(self.root / "chapters" / "02" / "zh.json", working)
        for name in ("glossary.json", "alignment.json"):
            (self.root / "chapters" / "02" / name).write_text(
                (CHAPTER / name).read_text(encoding="utf-8"), encoding="utf-8")
        # body-only chapter: the figure list is empty but still declared, because the
        # figure map is built for every chapter rather than guessed from an assets/ dir.
        write_json(self.root / "chapters" / "02" / "source.json",
                   {"chapter": 2, "figures": []})
        self.opinions = {"chapter": 2, "reviewed": True, "items": [{
            "id": "02-002", "status": "adopted",
            "zh_original": BASELINE_TEXT, "zh_recommended": ADOPTED_TEXT,
            "reason": "原译「下降到」丢失了 fell 的完成含义。",
            "evidence_en": "Confidence in the military fell to 39%."}]}

    def tearDown(self):
        self.tmp.cleanup()

    def run_validate(self) -> int:
        write_json(self.root / "reviews" / "02" / "opinions.json", self.opinions)
        return export.main(["--validate-only", "--chapters", "2",
                            "--reviews-root", str(self.root / "reviews"),
                            "--baseline-root", str(self.root / "baseline"),
                            "--chapters-root", str(self.root / "chapters")])

    def test_adopted_row_reconciling_with_working_text_passes(self):
        self.assertEqual(self.run_validate(), 0)

    def test_wrong_literal_original_is_rejected(self):
        self.opinions["items"][0]["zh_original"] = "对军队的信心降至 39%。"
        self.assertNotEqual(self.run_validate(), 0)

    def test_unregistered_working_difference_is_rejected(self):
        self.opinions["items"] = []
        self.assertNotEqual(self.run_validate(), 0)

    def test_pending_row_must_leave_the_working_text_at_baseline(self):
        self.opinions["items"][0].update({"status": "pending",
                                          "zh_recommended": ADOPTED_TEXT})
        self.assertNotEqual(self.run_validate(), 0)

    def test_chapter_scope_is_explicit(self):
        self.assertEqual(export.parse_chapters("1,5,9"), [1, 5, 9])
        self.assertEqual(export.parse_chapters("1-3"), [1, 2, 3])


class ArchiveMetadata(unittest.TestCase):
    """svg_metadata reconciliation reads the original from the fixed archive."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        zh = json.loads((CHAPTER / "zh.json").read_text(encoding="utf-8"))
        write_json(self.root / "baseline" / "02" / "zh.json", zh)
        write_json(self.root / "chapters" / "02" / "zh.json", zh)
        write_json(self.root / "chapters" / "02" / "source.json", {
            "chapter": 2,
            "segments": [],
            "figures": [{
                "segment_id": "02-003",
                "file": "ch02-fig01.svg",
                "en": "assets/ch02-fig01-en.svg",
                "zh": "assets/ch02-fig01-zh.svg",
            }],
        })
        original = (CHAPTER / "assets" / "fig1-zh.svg").read_text(encoding="utf-8")
        self.archive = self.root / "archive"
        (self.archive / "assets").mkdir(parents=True)
        (self.archive / "assets" / "ch02-fig01.svg").write_text(original, encoding="utf-8")
        assets = self.root / "chapters" / "02" / "assets"
        assets.mkdir(parents=True)
        (assets / "ch02-fig01-zh.svg").write_text(
            original.replace(ARCHIVE_TITLE, ADOPTED_TITLE), encoding="utf-8")
        self.opinions = {"chapter": 2, "reviewed": True, "items": [{
            "id": "02-003", "status": "adopted", "scope": "svg_metadata",
            "asset": "assets/ch02-fig01-zh.svg", "element": "title",
            "zh_original": ARCHIVE_TITLE, "zh_recommended": ADOPTED_TITLE,
            "reason": "无障碍标题改为中文，与可见图例一致。",
            "evidence_en": "Chart title"}]}

    def tearDown(self):
        self.tmp.cleanup()

    def run_validate(self, archive: Path | None = None) -> int:
        write_json(self.root / "reviews" / "02" / "opinions.json", self.opinions)
        return export.main(["--validate-only", "--chapters", "2",
                            "--reviews-root", str(self.root / "reviews"),
                            "--baseline-root", str(self.root / "baseline"),
                            "--chapters-root", str(self.root / "chapters"),
                            "--archive", str(archive or self.archive)])

    def run_export(self, output: Path, archive: Path | None = None) -> int:
        write_json(self.root / "reviews" / "02" / "opinions.json", self.opinions)
        return export.main(["--chapters", "2", "--output", str(output),
                            "--reviews-root", str(self.root / "reviews"),
                            "--baseline-root", str(self.root / "baseline"),
                            "--chapters-root", str(self.root / "chapters"),
                            "--archive", str(archive or self.archive)])

    def test_adopted_metadata_row_reconciles_against_archive_original(self):
        self.assertEqual(self.run_validate(), 0)

    def test_unregistered_metadata_change_is_rejected(self):
        self.opinions["items"] = []
        self.assertNotEqual(self.run_validate(), 0)

    def test_working_copy_without_archive_original_is_rejected(self):
        (self.archive / "assets" / "ch02-fig01.svg").unlink()
        self.assertNotEqual(self.run_validate(), 0)

    def test_geometry_change_is_still_rejected(self):
        asset = self.root / "chapters" / "02" / "assets" / "ch02-fig01-zh.svg"
        asset.write_text(asset.read_text(encoding="utf-8").replace(
            "<svg", "<svg data-geometry-drift=\"1\"", 1), encoding="utf-8")
        self.assertNotEqual(self.run_validate(), 0)

    def test_figure_without_declared_file_cannot_hide_a_metadata_change(self):
        source = self.root / "chapters" / "02" / "source.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        del data["figures"][0]["file"]
        write_json(source, data)
        self.opinions["items"] = []
        self.assertNotEqual(self.run_validate(), 0)

    def test_explicit_mapping_need_not_follow_a_zh_suffix(self):
        assets = self.root / "chapters" / "02" / "assets"
        (assets / "ch02-fig01-zh.svg").rename(assets / "plate-02.svg")
        source = self.root / "chapters" / "02" / "source.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        data["figures"][0]["zh"] = "assets/plate-02.svg"
        write_json(source, data)
        self.opinions["items"][0]["asset"] = "assets/plate-02.svg"
        self.assertEqual(self.run_validate(), 0)

    def test_declared_file_with_a_directory_is_rejected(self):
        source = self.root / "chapters" / "02" / "source.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        data["figures"][0]["file"] = "assets/ch02-fig01.svg"
        write_json(source, data)
        self.assertNotEqual(self.run_validate(), 0)

    def test_declared_working_svg_that_is_missing_is_rejected(self):
        # source.json still declares the figure, but the working SVG was never produced.
        # There is no opinion row for it, so nothing else in the chapter can catch it.
        (self.root / "chapters" / "02" / "assets" / "ch02-fig01-zh.svg").unlink()
        self.opinions["items"] = []
        self.assertNotEqual(self.run_validate(), 0)

    def test_missing_declared_working_svg_writes_no_workbook(self):
        (self.root / "chapters" / "02" / "assets" / "ch02-fig01-zh.svg").unlink()
        self.opinions["items"] = []
        out = self.root / "out" / "opinions.xlsx"
        code = self.run_export(out)
        self.assertNotEqual(code, 0)
        self.assertFalse(out.exists(), "校验失败时不得写出工作簿")

    def test_archive_option_overrides_the_default_root(self):
        custom = self.root / "custom-archive"
        shutil.copytree(self.archive, custom)
        (custom / "assets" / "ch02-fig01.svg").write_text(
            (CHAPTER / "assets" / "fig1-zh.svg").read_text(encoding="utf-8").replace(
                ARCHIVE_TITLE, "Original title"), encoding="utf-8")
        self.opinions["items"][0]["zh_original"] = "Original title"
        self.assertEqual(self.run_validate(custom), 0)


if __name__ == "__main__":
    unittest.main()
