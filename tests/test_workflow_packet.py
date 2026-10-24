"""Offline tests for the compact-JSON 改稿包 (workflow/packet.py).

The judgments file here is a labelled synthetic stand-in for the paid screening JSON
(same shape, no fake "live" numbers passed off as evidence); the chapter input is the
synthetic fixture chapter.  Nothing calls the network.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "workflow"))

import packet  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "workflow"
CHAPTER = FIX / "chapter-02"


def judgments_fixture() -> dict:
    return {
        "chapter": 2,
        "mode": "execute",
        "coverage": {m: {"expected": 3, "ok": 3} for m in packet.judge.MODELS},
        "routing": {"counts": {"suspected_issue": 1, "uncertain": 1, "retain": 1}},
        "records": [
            {"target_id": "02-002", "requested_model": model, "status": "ok",
             "probability": "0.82"}
            for model in packet.judge.MODELS
        ] + [
            {"target_id": "02-003", "requested_model": model, "status": "ok",
             "probability": "0.41"}
            for model in packet.judge.MODELS
        ] + [
            {"target_id": "02-057", "requested_model": model, "status": "ok",
             "probability": "0.05"}
            for model in packet.judge.MODELS
        ],
    }


class BuildPacket(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.judgments = self.dir / "chapter-02.json"
        self.judgments.write_text(json.dumps(judgments_fixture(), ensure_ascii=False),
                                  encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_routing_uses_band_thresholds_and_drops_retain_by_default(self):
        built = packet.build(CHAPTER, self.judgments)
        ids = [i["id"] for i in built["items"]]
        self.assertEqual(ids, ["02-002", "02-003"])
        self.assertEqual({i["id"]: i["route"] for i in built["items"]},
                         {"02-002": "suspected_issue", "02-003": "uncertain"})
        with_retain = packet.build(CHAPTER, self.judgments, include_retain=True)
        self.assertIn("02-057", [i["id"] for i in with_retain["items"]])

    def test_item_carries_context_protected_spans_and_signals(self):
        built = packet.build(CHAPTER, self.judgments, include_retain=True)
        item = next(i for i in built["items"] if i["id"] == "02-057")
        self.assertEqual(item["en"], "In 2024 the toll was 17/33 of the old levy.")
        self.assertEqual(item["zh_current"], "2024 年的收费是旧税的 16/31。")
        self.assertEqual([g["en"] for g in item["glossary"]], ["levy"])
        self.assertEqual([b["id"] for b in item["before"]], ["02-003", "02-004"])
        self.assertEqual([b["id"] for b in item["after"]], ["02-058"])
        self.assertEqual({j["model"] for j in item["judges"]}, set(packet.judge.MODELS))
        self.assertTrue(all(j["probability"] == 0.05 for j in item["judges"]))
        self.assertIn("16", item["numbers_and_latin"] + item["must_keep"])

    def test_rules_keep_the_evidence_over_probability_rule(self):
        built = packet.build(CHAPTER, self.judgments)
        self.assertIn("routing signals, not ground truth", built["rules"]["signals"])
        self.assertIn("do not revise them", built["rules"]["context"])
        self.assertIn("must_keep", built["rules"]["preserve"])
        self.assertEqual(built["packet"], packet.PACKET_VERSION)
        self.assertEqual(built["encoding"]["kind"], "compact-json")
        self.assertNotIn("toon", json.dumps(built["rules"]).lower())

    def test_figure_item_uses_svg_labels_and_marks_scope(self):
        built = packet.build(CHAPTER, self.judgments)
        item = next(i for i in built["items"] if i["id"] == "02-003")
        self.assertEqual(item["kind"], "figure_label")
        self.assertEqual(item["scope"], "svg_label")
        self.assertTrue(item["asset"].endswith("fig1-zh.svg"))
        self.assertEqual([l["zh"] for l in item["labels"]], ["一点", "39%"])
        self.assertIn("alt placeholder", built["rules"]["figures"])
        self.assertEqual(built["structural_only"], [])

    def test_compact_json_roundtrip_preserves_literal_chinese(self):
        built = packet.build(CHAPTER, self.judgments, include_retain=True)
        document = packet.dumps(built)
        self.assertNotIn("\\u", document)
        self.assertEqual(packet.roundtrip(document, built), built)
        self.assertIn("16/31", document)

    def test_cli_writes_packet_and_refuses_overwrite(self):
        out = self.dir / "packets" / "chapter-02.json"
        code = packet.main(["--chapter-dir", str(CHAPTER), "--judgments",
                            str(self.judgments), "--output", str(out)])
        self.assertEqual(code, 0)
        written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(written["chapter"], 2)
        self.assertEqual(len(written["items"]), 2)
        again = packet.main(["--chapter-dir", str(CHAPTER), "--judgments",
                             str(self.judgments), "--output", str(out)])
        self.assertEqual(again, 3)


class VerifyRevision(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.judgments = self.dir / "chapter-02.json"
        self.judgments.write_text(json.dumps(judgments_fixture(), ensure_ascii=False),
                                  encoding="utf-8")
        self.packet_path = self.dir / "packet.json"
        self.packet_path.write_text(
            packet.dumps(packet.build(CHAPTER, self.judgments, include_retain=True)),
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _verify(self, revisions: list[dict]) -> int:
        path = self.dir / "revisions.json"
        path.write_text(json.dumps({"packet_path": str(self.packet_path),
                                    "revisions": revisions}, ensure_ascii=False),
                        encoding="utf-8")
        return packet.verify_revision(path)

    def test_accepts_a_changed_paragraph_with_a_reason(self):
        self.assertEqual(self._verify([{
            "id": "02-002",
            "revised_chinese": "受访者对军队的信心跌至 39%。",
            "reason": "原译「下降到」丢失了原文 fell 的完成含义。"}]), 0)

    def test_rejects_unchanged_text_and_missing_reason(self):
        self.assertEqual(self._verify([{"id": "02-002",
                                        "revised_chinese": "对军队的信心下降到 39%。",
                                        "reason": "x"}]), 2)
        self.assertEqual(self._verify([{"id": "02-002",
                                        "revised_chinese": "受访者对军队的信心跌至 39%。",
                                        "reason": "  "}]), 2)

    def test_rejects_unknown_and_duplicate_ids(self):
        self.assertEqual(self._verify([{"id": "09-999", "revised_chinese": "x",
                                        "reason": "y"}]), 2)
        self.assertEqual(self._verify([
            {"id": "02-002", "revised_chinese": "受访者对军队的信心跌至 39%。", "reason": "a"},
            {"id": "02-002", "revised_chinese": "受访者对军队的信心跌至 40%。", "reason": "b"},
        ]), 2)

    def test_dropped_number_warns_instead_of_failing(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = self._verify([{
                "id": "02-057",
                "revised_chinese": "2024 年的收费是旧税的两倍。",
                "reason": "原译误把比例当税额。"}])
        out = json.loads(buf.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(out["problems"], [])
        self.assertTrue(any("numeric/Latin change" in w for w in out["warnings"]))
        self.assertTrue(any("reason does not name the token" in w for w in out["warnings"]))

    def test_lost_hard_span_fails_verification(self):
        # A hard span (inline markup the translator must never drop) is a problem.
        minimal = {"packet": packet.PACKET_VERSION, "chapter": 2, "items": [{
            "id": "02-002", "route": "suspected_issue", "kind": "text",
            "en": "Confidence fell to <b>39%</b>.", "zh_current": "信心跌至 <b>39%</b>。",
            "must_keep": ["<b>", "</b>"], "numbers_and_latin": ["39"]}]}
        path = self.dir / "minimal-packet.json"
        path.write_text(json.dumps(minimal, ensure_ascii=False), encoding="utf-8")
        rev = self.dir / "minimal-revisions.json"
        rev.write_text(json.dumps({"packet_path": str(path), "revisions": [{
            "id": "02-002", "revised_chinese": "信心跌至 39%。",
            "reason": "去掉粗体。"}]}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(packet.verify_revision(rev), 2)


if __name__ == "__main__":
    unittest.main()
