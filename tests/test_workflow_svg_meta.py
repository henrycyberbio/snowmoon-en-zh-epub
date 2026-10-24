"""Offline tests for the hidden-SVG-metadata recheck (workflow/recheck_svg_meta.py).

The request bodies are built and parsed for real; only the transport is mocked, and the
answers are the retained raw responses in
``tests/fixtures/workflow/decision-responses-retained.json``.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "workflow"))

import judge  # noqa: E402
import recheck_svg_meta as meta  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "workflow"
TARGETS = REPO / "prompts" / "svg-metadata-targets.json"
RETAINED = json.loads((FIX / "decision-responses-retained.json").read_text(encoding="utf-8"))
BY_MODEL = {r["requested_model"]: json.loads(r["raw_response"]) for r in RETAINED["responses"]}


def fake_identity():
    models = {}
    for model, pin in judge.MODELS.items():
        models[model] = {"ok": True, "provider_pin": pin,
                         "returned_aliases": sorted({model, BY_MODEL[model]["model"]}),
                         "canonical_model_id": BY_MODEL[model]["model"],
                         "pricing": {"prompt": "0.0000001", "completion": "0.0000002"}}
    return {"catalog_http_status": 200, "catalog_count": 3, "models": models}


class TargetsFile(unittest.TestCase):
    def test_real_targets_cover_three_figures_and_five_elements(self):
        targets = meta.load_targets(TARGETS)
        self.assertEqual([t["target_id"] for t in targets], ["27-118", "27-120", "27-125"])
        elements = [(t["target_id"], e["element"]) for t in targets for e in t["elements"]]
        self.assertEqual(elements, [("27-118", "title"), ("27-118", "desc"),
                                    ("27-120", "desc"),
                                    ("27-125", "title"), ("27-125", "desc")])
        for t in targets:
            self.assertTrue(t["asset"].startswith("assets/ch27-fig"))
            self.assertTrue(t["visible_labels"])

    def test_rejects_unknown_element_and_missing_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps([{"target_id": "27-118", "asset": "a.svg",
                                        "elements": [{"element": "label"}],
                                        "visible_labels": []}], ensure_ascii=False),
                           encoding="utf-8")
            with self.assertRaises(SystemExit):
                meta.load_targets(bad)
            missing = Path(tmp) / "missing.json"
            missing.write_text(json.dumps([{"target_id": "27-118"}]), encoding="utf-8")
            with self.assertRaises(SystemExit):
                meta.load_targets(missing)


class MetadataPayload(unittest.TestCase):
    def setUp(self):
        self.target = meta.load_targets(TARGETS)[0]

    def test_state_keeps_visible_labels_and_marks_the_scope(self):
        payload = meta.build_payload(self.target, "typesafe/jev-1.14")
        state = payload["state"]
        self.assertEqual(state["scope"], "svg_metadata")
        self.assertEqual(state["target_id"], "27-118")
        self.assertEqual([e["element"] for e in state["elements"]], ["title", "desc"])
        self.assertEqual(len(state["visible_labels"]), 5)
        self.assertIn("stays as-is", state["notes"])

    def test_instructions_judge_only_the_metadata_strings(self):
        q = meta.build_payload(self.target, "typesafe/jev-1.14")["questions"][judge.QUESTION_ID]
        self.assertIn("hidden accessibility metadata", q["instructions"])
        self.assertIn("NOT under judgment", q["instructions"])
        self.assertIn("27-118", q["criteria"]["true"])
        self.assertEqual(q["type"], "noul")

    def test_state_identical_across_models(self):
        payloads = [meta.build_payload(self.target, m) for m in judge.MODELS]
        self.assertEqual(len({judge.fingerprint(p) for p in payloads}), 1)
        self.assertEqual([p["provider"]["only"][0] for p in payloads],
                         [judge.MODELS[m] for m in judge.MODELS])


class OfflineRun(unittest.TestCase):
    def test_runs_against_mocked_transport_and_writes_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp) / "rechecks" / "27"
            calls = []

            def fake_http(method, url, key=None, body=None, timeout=120):
                calls.append(body)
                return 200, json.dumps(BY_MODEL[body["model"]])

            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), \
                    mock.patch.object(judge, "live_identity", lambda key: fake_identity()), \
                    mock.patch.object(judge, "http", fake_http):
                code = meta.main(["--targets", str(TARGETS), "--output-dir", str(outdir),
                                  "--budget-usd", "0.02"])
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 9)  # 3 figures x 3 pinned models
            for body in calls:
                self.assertEqual(body["state"]["scope"], "svg_metadata")
                self.assertIn("elements", body["state"])
            files = sorted(p.name for p in outdir.glob("svg-metadata-*.json"))
            self.assertEqual(files, ["svg-metadata-27_118.json", "svg-metadata-27_120.json",
                                     "svg-metadata-27_125.json"])
            record = json.loads((outdir / "svg-metadata-27_118.json").read_text(encoding="utf-8"))
            self.assertTrue(record["state_consistent_across_models"])
            self.assertTrue(record["all_below_adoption_threshold"])
            self.assertEqual(len(record["calls"]), 3)
            for call in record["calls"]:
                self.assertEqual(call["status"], "ok")
                self.assertLess(Decimal(call["probability"]), meta.ADOPT_BELOW)
            jsonl = outdir / "svg-metadata-27_118.records.jsonl"
            self.assertEqual(len(jsonl.read_text(encoding="utf-8").splitlines()), 3)
            self.assertIn("raw_response", json.loads(
                jsonl.read_text(encoding="utf-8").splitlines()[0]))

    def test_refuses_to_overwrite_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp)
            (outdir / "svg-metadata-27_118.json").write_text("{}", encoding="utf-8")
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), \
                    mock.patch.object(judge, "live_identity", lambda key: fake_identity()), \
                    mock.patch.object(judge, "http", lambda *a, **k: (200, "{}")):
                code = meta.main(["--targets", str(TARGETS), "--output-dir", str(outdir)])
            self.assertEqual(code, 3)

    def test_refuses_when_live_pin_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = fake_identity()
            ident["models"]["typesafe/jev-1.14"] = {"ok": False, "error": "no endpoint"}
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), \
                    mock.patch.object(judge, "live_identity", lambda key: ident), \
                    mock.patch.object(judge, "http", lambda *a, **k: (200, "{}")):
                code = meta.main(["--targets", str(TARGETS), "--output-dir", str(tmp)])
            self.assertEqual(code, 4)
            self.assertEqual(list(Path(tmp).glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
