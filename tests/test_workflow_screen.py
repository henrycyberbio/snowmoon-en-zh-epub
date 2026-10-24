"""Offline tests for the whole-book driver's pure parts (workflow/screen_all.py).

Only local logic is exercised: input verification, budget allocation and evidence
merging.  The live-metadata pricing path is deliberately NOT called here - a dry run
still needs free GETs to price the run, so it is not part of the offline suite.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "workflow"))

import judge  # noqa: E402
import screen_all as screen  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "workflow"
CHAPTER = FIX / "chapter-02"


class InputChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(CHAPTER, self.root / "chapters" / "02")

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_chapter_is_ready_and_lists_excluded_items(self):
        check = screen.check_chapter(self.root, 2)
        self.assertTrue(check["ok"], check["errors"])
        self.assertEqual(check["segments"], 6)
        self.assertEqual(check["targets"], 6)
        self.assertEqual(check["excluded"], {})
        self.assertEqual(check["structural_only"], [])

    def test_missing_baseline_file_blocks_inference(self):
        (self.root / "chapters" / "02" / "alignment.json").unlink()
        check = screen.check_chapter(self.root, 2)
        self.assertFalse(check["ok"])
        self.assertIn("alignment.json missing", check["errors"])

    def test_id_prefix_and_order_mismatch_are_reported(self):
        zh_path = self.root / "chapters" / "02" / "zh.json"
        zh = json.loads(zh_path.read_text(encoding="utf-8"))
        zh["segments"][1]["id"] = "09-002"
        zh_path.write_text(json.dumps(zh, ensure_ascii=False), encoding="utf-8")
        check = screen.check_chapter(self.root, 2)
        self.assertFalse(check["ok"])
        self.assertTrue(any("id order differs" in e for e in check["errors"]))

    def test_unresolved_alignment_blocks_block_inference(self):
        path = self.root / "chapters" / "02" / "alignment.json"
        path.write_text(json.dumps({"unresolved_blocks": [
            {"segment_ids": ["02-058"], "reason": "mapping ambiguous"}]}), encoding="utf-8")
        check = screen.check_chapter(self.root, 2)
        self.assertFalse(check["ok"])
        self.assertTrue(any("unresolved alignment blocks" in e for e in check["errors"]))


class Allocation(unittest.TestCase):
    def test_caps_are_disjoint_and_never_exceed_the_allowance(self):
        caps, warnings = screen.allocate({1: Decimal("0.5"), 2: Decimal("1.2"),
                                          3: Decimal("9")}, Decimal("1.0"))
        self.assertEqual(sorted(caps), [1, 2, 3])
        self.assertLessEqual(sum(caps.values()), Decimal("1.0"))
        self.assertTrue(warnings)
        self.assertTrue(all(cap > 0 for cap in caps.values()))

    def test_small_run_is_not_scaled(self):
        caps, warnings = screen.allocate({2: Decimal("0.001"), 3: Decimal("0.001")},
                                         Decimal("1.0"))
        self.assertEqual(warnings, [])
        self.assertLess(sum(caps.values()), Decimal("0.01"))


class EvidenceMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(CHAPTER, self.root / "chapters" / "02")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_evidence(self, probabilities: list[str], folder: Path | None = None,
                        name: str = "chapter-02.json", identity: bool = True) -> Path:
        folder = folder or (self.root / "screening")
        folder.mkdir(parents=True, exist_ok=True)
        result = folder / name
        records = []
        for model, prob in zip(judge.MODELS, probabilities):
            for tid in ("02-001", "02-002"):
                rec = {"target_id": tid, "requested_model": model, "status": "ok",
                       "probability": prob, "response_id": f"id-{tid}-{model}"}
                if identity:
                    rec["identity"] = {"model_matched_exactly": True, "provider_matched": True}
                records.append(rec)
        result.write_text(json.dumps({"all_checks_ok": True,
                                      "records": records}), encoding="utf-8")
        return result

    def test_complete_matrix_is_detected(self):
        self._write_evidence(["0.1", "0.2", "0.9"])
        row = screen.merged_matrix(self.root, 2)
        self.assertFalse(row["complete"])  # only 2 of 6 targets covered
        self.assertEqual(len(row["missing"]), 12)
        self.assertEqual(row["duplicate_response_ids"], 0)

    def test_cells_without_verified_identity_are_not_counted(self):
        self._write_evidence(["0.1", "0.2", "0.9"], identity=False)
        row = screen.merged_matrix(self.root, 2)
        self.assertEqual(row["ok_cells"], 0)
        self.assertFalse(row["complete"])


class ChapterSpec(unittest.TestCase):
    def test_ranges_and_lists(self):
        self.assertEqual(screen.parse_chapters("2-4,9"), [2, 3, 4, 9])
        self.assertEqual(screen.parse_chapters("1,1,2"), [1, 2])
        with self.assertRaises(SystemExit):
            screen.parse_chapters("0,33")


class RetryGaps(unittest.TestCase):
    """``--retry`` fills only still-missing cells, one evidence file per (chapter, model).

    ``screen_all.subprocess.run`` is the only stub: the fake child parses the real argv,
    writes real result/records files beside the real ``--output`` path, and the run is
    judged afterwards by reading the evidence back through ``merged_matrix``.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(CHAPTER, self.root / "chapters" / "02")
        self.screen = self.root / "screening"
        self.screen.mkdir()
        self.targets = ["02-001", "02-002", "02-003", "02-004", "02-057", "02-058"]
        self.models = list(judge.MODELS)

    def tearDown(self):
        self.tmp.cleanup()

    def _cell(self, tid: str, model: str, rid: str) -> dict:
        return {"target_id": tid, "requested_model": model, "status": "ok",
                "probability": "0.1", "response_id": rid,
                "identity": {"model_matched_exactly": True, "provider_matched": True,
                             "returned_model": model,
                             "returned_provider": judge.MODELS[model]}}

    def _write_base(self, skip: tuple[str, ...] = ("02-002",)) -> Path:
        records = [self._cell(t, m, f"base-{t}-{m}")
                   for m in self.models for t in self.targets if t not in skip]
        data: dict = {"all_checks_ok": True, "records": records}
        path = self.screen / "chapter-02.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _gaps(self, cells: list[tuple[str, str]]) -> Path:
        path = self.root / "gaps.json"
        path.write_text(json.dumps({
            "schema": "snowmoon-fullbook-gaps/1",
            "missing": [{"chapter": 2, "target_id": t, "model": m}
                        for t, m in cells]}, ensure_ascii=False), encoding="utf-8")
        return path

    def _fake_run(self, calls: list[dict], fail_models: tuple[str, ...] = ()):
        def fake_run(cmd, **kwargs):
            assert "--execute" in cmd, cmd
            output = Path(cmd[cmd.index("--output") + 1])
            model = cmd[cmd.index("--only-model") + 1]
            targets = cmd[cmd.index("--target-ids") + 1].split(",")
            calls.append({"model": model, "targets": targets,
                          "output": str(output), "cmd": list(cmd)})
            if model in fail_models:
                return subprocess.CompletedProcess(cmd, 1, "", "simulated child failure")
            records = [self._cell(t, model, f"retry-{t}-{model}") for t in targets]
            output.write_text(json.dumps({
                "all_checks_ok": True,
                "coverage": {model: {"expected": len(targets), "ok": len(targets),
                                     "missing": []}},
                "records": records}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            output.with_suffix(".records.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "simulated ok", "")

        return fake_run

    def test_every_model_gap_is_refilled_in_its_own_evidence_file(self):
        base = self._write_base()
        before = base.read_bytes()
        # 02-001 is already covered (stale gap); only 02-002 is really missing
        gap_path = self._gaps([(t, m) for m in self.models
                               for t in ("02-001", "02-002")])
        calls: list[dict] = []
        with mock.patch.object(screen.subprocess, "run", self._fake_run(calls)):
            rc = screen.retry(self.root, gap_path, 1)

        self.assertEqual(rc, 0)
        row = screen.merged_matrix(self.root, 2)
        self.assertTrue(row["complete"], row["missing"])
        self.assertEqual(row["ok_cells"], len(self.targets) * len(self.models))
        self.assertEqual(base.read_bytes(), before)  # paid base evidence untouched

        self.assertEqual(len(calls), len(self.models))  # no break after the first model
        self.assertEqual({c["model"] for c in calls}, set(self.models))
        self.assertEqual({tuple(c["targets"]) for c in calls}, {("02-002",)})
        self.assertEqual(len({c["output"] for c in calls}), len(self.models))
        for call in calls:
            self.assertIn(call["model"].replace("/", "_"), call["output"])

        files = sorted(self.screen.glob("chapter-02.retry-*.json"))
        self.assertEqual(len(files), len(self.models))
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len({r["requested_model"] for r in data["records"]}), 1)
            self.assertEqual({r["target_id"] for r in data["records"]}, {"02-002"})
            self.assertTrue(path.with_suffix(".records.jsonl").is_file())

    def test_rerunning_the_old_gaps_file_requests_nothing(self):
        self._write_base()
        gap_path = self._gaps([("02-002", m) for m in self.models])
        first_calls: list[dict] = []
        with mock.patch.object(screen.subprocess, "run", self._fake_run(first_calls)):
            first = screen.retry(self.root, gap_path, 1)
        files = sorted(p.name for p in self.screen.iterdir())
        second_calls: list[dict] = []
        with mock.patch.object(screen.subprocess, "run", self._fake_run(second_calls)):
            second = screen.retry(self.root, gap_path, 1)

        self.assertEqual((first, second), (0, 0))
        self.assertEqual(len(first_calls), len(self.models))
        self.assertEqual(second_calls, [])  # successful cells are never re-requested
        self.assertEqual(sorted(p.name for p in self.screen.iterdir()), files)

    def test_partial_retry_returns_nonzero_for_the_remaining_gaps(self):
        self._write_base()
        gap_path = self._gaps([("02-002", m) for m in self.models])
        failing = ("openai/gpt-6-luna-decisions", "perplexity/pplx-decider-flash-v1.2")
        calls: list[dict] = []
        with mock.patch.object(screen.subprocess, "run",
                               self._fake_run(calls, fail_models=failing)):
            rc = screen.retry(self.root, gap_path, 1)

        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), len(self.models))
        row = screen.merged_matrix(self.root, 2)
        self.assertFalse(row["complete"])
        self.assertEqual({(m["target_id"], m["model"]) for m in row["missing"]},
                         {("02-002", m) for m in failing})

    def test_existing_retry_evidence_is_never_overwritten(self):
        self._write_base()
        existing = self.screen / "chapter-02.retry-1.typesafe_jev-1.14.json"
        existing.write_text('{"paid": "earlier retry"}\n', encoding="utf-8")
        before = existing.read_bytes()
        gap_path = self._gaps([("02-002", m) for m in self.models])
        calls: list[dict] = []
        with mock.patch.object(screen.subprocess, "run", self._fake_run(calls)):
            rc = screen.retry(self.root, gap_path, 1)

        self.assertEqual(rc, 0)
        self.assertEqual(existing.read_bytes(), before)
        self.assertNotIn(str(existing), {c["output"] for c in calls})
        self.assertTrue(screen.merged_matrix(self.root, 2)["complete"])


if __name__ == "__main__":
    unittest.main()
