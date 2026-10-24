"""Offline tests for the 决策环节 (workflow/judge.py).

No paid call is made.  Transport is replaced by ``unittest.mock`` and the answers come
from retained raw responses of the project's own earlier decision runs
(``tests/fixtures/workflow/decision-responses-retained.json``), so parsing and identity
checks are exercised against real wire bodies, never against invented "live" evidence.
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

FIX = REPO / "tests" / "fixtures" / "workflow"
CHAPTER = FIX / "chapter-02"
RETAINED = json.loads((FIX / "decision-responses-retained.json").read_text(encoding="utf-8"))
BY_MODEL = {r["requested_model"]: json.loads(r["raw_response"]) for r in RETAINED["responses"]}
TARGET = "02-002"


def fake_identity(pricing=True):
    """Minimal live-metadata shape; aliases are the real date-stamped returned ids."""
    models = {}
    for model, pin in judge.MODELS.items():
        raw = BY_MODEL[model]
        models[model] = {
            "ok": True,
            "provider_pin": pin,
            "returned_aliases": sorted({model, raw["model"]}),
            "canonical_model_id": raw["model"],
            "endpoint_name": raw["model"],
            "pricing": ({"prompt": "0.0000001", "completion": "0.0000002"} if pricing else {}),
            "context_length": 100000,
        }
    return {"catalog_http_status": 200, "catalog_count": 3, "models": models}


class PayloadIdentity(unittest.TestCase):
    def setUp(self):
        self.chapter = judge.load_chapter(CHAPTER)

    def test_wire_body_has_only_the_documented_top_level_fields(self):
        payload = judge.build_payload(self.chapter, TARGET, "typesafe/jev-1.14")
        self.assertEqual(set(payload), {"model", "provider", "state", "questions"})
        self.assertEqual(payload["provider"],
                         {"only": ["TypeSafe"], "allow_fallbacks": False})

    def test_questions_carry_the_actual_instructions_and_criteria(self):
        payload = judge.build_payload(self.chapter, TARGET, "typesafe/jev-1.14")
        q = payload["questions"][judge.QUESTION_ID]
        self.assertEqual(q["type"], "noul")
        self.assertIn(TARGET, q["instructions"])
        self.assertEqual(set(q["criteria"]), {"true", "false"})
        self.assertIn(TARGET, q["criteria"]["true"])
        self.assertIn("substantive semantic error", q["instructions"])

    def test_state_is_identical_across_models_but_transport_identity_differs(self):
        payloads = [judge.build_payload(self.chapter, TARGET, m) for m in judge.MODELS]
        self.assertEqual(len({judge.fingerprint(p) for p in payloads}), 1)
        self.assertEqual(len({p["model"] for p in payloads}), len(judge.MODELS))
        self.assertEqual(len({p["provider"]["only"][0] for p in payloads}), len(judge.MODELS))
        for p in payloads:
            self.assertEqual(p["state"], payloads[0]["state"])

    def test_window_is_plus_minus_two_with_roles(self):
        payload = judge.build_payload(self.chapter, "02-057", "typesafe/jev-1.14")
        blocks = payload["state"]["blocks"]
        roles = [b["role"] for b in blocks]
        self.assertEqual(roles.count("target"), 1)
        index = roles.index("target")
        self.assertEqual(roles[:index], ["before"] * index)
        self.assertEqual(roles[index + 1:], ["after"] * (len(roles) - index - 1))
        self.assertEqual([b["id"] for b in blocks][index], "02-057")
        self.assertEqual(len(blocks), 4)  # 2 before, target, 1 after (last block)
        self.assertEqual(blocks[index]["en"],
                         "In 2024 the toll was 17/33 of the old levy.")
        self.assertEqual(blocks[index]["zh"], "2024 年的收费是旧税的 16/31。")

    def test_glossary_and_notes_are_scoped_to_the_target(self):
        payload = judge.build_payload(self.chapter, "02-057", "typesafe/jev-1.14")
        self.assertEqual([g["en"] for g in payload["state"]["glossary"]], ["levy"])
        self.assertEqual([n["id"] for n in payload["state"]["notes"]], ["02-057"])

    def test_figure_target_judges_real_labels_not_the_alt_placeholder(self):
        payload = judge.build_payload(self.chapter, "02-003", "typesafe/jev-1.14")
        target = next(b for b in payload["state"]["blocks"] if b["role"] == "target")
        self.assertEqual(target["kind"], "figure")
        self.assertEqual([l["en"] for l in target["labels"]], ["A little", "39%"])
        self.assertEqual([l["zh"] for l in target["labels"]], ["一点", "39%"])
        self.assertIn("figure's label set", payload["questions"][judge.QUESTION_ID]["instructions"])

    def test_notes_and_empty_translations_block_judgment_instead_of_passing(self):
        chapter = judge.load_chapter(CHAPTER)
        chapter["segments"].append({"id": "02-099", "type": "p", "kind": "text",
                                    "en": "Some English.", "zh": ""})
        excluded = {s["id"]: judge.exclusion_reason(s, chapter["blocked"])
                    for s in chapter["segments"]}
        self.assertTrue(excluded["02-099"].startswith("empty translation"))
        self.assertNotIn("02-002", {k for k, v in excluded.items() if v})


class ParseRetainedResponses(unittest.TestCase):
    def test_retained_bodies_parse_to_probability_and_usage(self):
        for model, raw in BY_MODEL.items():
            parsed = judge.parse_answer(json.dumps(raw))
            self.assertEqual(parsed["returned_model"], raw["model"])
            self.assertEqual(parsed["provider"], raw["provider"])
            self.assertTrue(0 <= Decimal(parsed["probability"]) <= 1)
            # Decimal, not float repr: the returned cost is re-formatted in plain decimal
            self.assertEqual(Decimal(parsed["cost_usd"]), Decimal(str(raw["usage"]["cost"])))
            self.assertEqual(parsed["raw_response"], json.dumps(raw))

    def test_returned_instance_id_is_not_the_requested_alias(self):
        for model, raw in BY_MODEL.items():
            self.assertNotEqual(raw["model"], model)
            self.assertTrue(raw["model"].startswith(model),
                            "returned id is the requested alias plus a date stamp")
            identity = judge.live_identity
            self.assertIsNotNone(identity)
            # exact alias matching is required: the returned id is NOT in MODELS
            self.assertNotIn(raw["model"], judge.MODELS)

    def test_rejects_wrong_answer_set(self):
        raw = json.loads(json.dumps(BY_MODEL["typesafe/jev-1.14"]))
        raw["answers"] = {"other_question": {"type": "noul", "noul": 0.1}}
        with self.assertRaises(ValueError):
            judge.parse_answer(json.dumps(raw))

    def test_rejects_bool_out_of_range_and_non_finite_probability(self):
        for bad in (True, 1.5, -0.1, "0.5", None):
            raw = json.loads(json.dumps(BY_MODEL["typesafe/jev-1.14"]))
            raw["answers"][judge.QUESTION_ID]["noul"] = bad
            with self.assertRaises(ValueError, msg=repr(bad)):
                judge.parse_answer(json.dumps(raw))

    def test_rejects_negative_cost(self):
        raw = json.loads(json.dumps(BY_MODEL["typesafe/jev-1.14"]))
        raw["usage"]["cost"] = -1
        with self.assertRaises(ValueError):
            judge.parse_answer(json.dumps(raw))


class DryRun(unittest.TestCase):
    def test_dry_run_writes_plan_and_verifies_cross_model_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan-02.json"
            code = judge.main(["--chapter-dir", str(CHAPTER), "--output", str(out)])
            self.assertEqual(code, 0)
            plan = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(plan["identity_check"]["ok"])
            self.assertEqual(plan["identity_check"]["targets_with_divergent_state"], {})
            self.assertEqual(plan["estimates"]["requests"], len(plan["targets"]) * 3)
            self.assertNotIn("02-003", plan["excluded"])  # figure has comparable labels
            self.assertEqual(len(plan["sample_requests"]), 3)
            for sample in plan["sample_requests"]:
                self.assertEqual(set(sample["payload"]),
                                 {"model", "provider", "state", "questions"})


class ExecuteGates(unittest.TestCase):
    def test_refuses_to_overwrite_paid_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "j.json"
            out.write_text("{}", encoding="utf-8")
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}):
                code = judge.execute(CHAPTER, out, None, Decimal("1"), None)
            self.assertEqual(code, 3)

    def test_refuses_before_spending_when_pricing_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "j.json"
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), \
                    mock.patch.object(judge, "live_identity", lambda key: fake_identity(False)):
                code = judge.execute(CHAPTER, out, None, Decimal("1"), None)
            self.assertEqual(code, 4)
            self.assertFalse(out.exists())

    def test_execute_writes_real_shaped_records_from_retained_responses(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "j.json"
            calls = []

            def fake_http(method, url, key=None, body=None, timeout=120):
                calls.append((method, url, body))
                model = body["model"]
                return 200, json.dumps(BY_MODEL[model])

            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), \
                    mock.patch.object(judge, "live_identity", lambda key: fake_identity()), \
                    mock.patch.object(judge, "http", fake_http):
                code = judge.execute(CHAPTER, out, ["02-002"], Decimal("0.01"), None)

            self.assertEqual(code, 0)
            self.assertEqual({c[0] for c in calls}, {"POST"})
            self.assertEqual({c[1] for c in calls}, {judge.ENDPOINT})
            result = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(result["all_checks_ok"])
            self.assertEqual(result["calls"]["ok"], 3)
            self.assertEqual(result["routing"]["ids"]["retain"], ["02-002"])
            self.assertEqual(result["unique_response_ids"], 3)
            records = [json.loads(line) for line in
                       (Path(tmp) / "j.records.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 3)
            for rec in records:
                # the stored request is the actual wire body, not a rebuilt approximation
                self.assertEqual(rec["request"]["questions"][judge.QUESTION_ID]["type"], "noul")
                self.assertEqual(rec["identity"]["provider_matched"], True)
                self.assertEqual(rec["status"], "ok")
            self.assertEqual(Decimal(result["cost"]["actual_usd"]), sum(
                Decimal(str(json.loads(r["raw_response"])["usage"]["cost"])) for r in records))

    def test_budget_stop_is_recorded_not_silently_dropped(self):
        budget = judge.Budget(Decimal("0.0000001"))
        allowed, why = budget.allow(Decimal("0.001"))
        self.assertFalse(allowed)
        self.assertIn("budget stop", why)


class Credentials(unittest.TestCase):
    def test_key_file_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            keyfile = Path(tmp) / "key.env"
            keyfile.write_text("OPENROUTER_API_KEY=sk-test-1\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"OPENROUTER_KEY_FILE": str(keyfile)},
                                 clear=True):
                self.assertEqual(judge.load_key(), "sk-test-1")

    def test_missing_credential_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(judge, "REPO_ROOT", Path(tmp)):
                with self.assertRaises(SystemExit):
                    judge.load_key()


class RetryCompletion(unittest.TestCase):
    """The final per-cell evidence decides the verdict, not the number of failed attempts.

    A transport error that the built-in retry resolved is audit history: it stays in
    ``failures`` and in the JSONL records, but it must not keep a chapter failed forever.
    """

    def _execute(self, tmp: str, responder, targets: list[str]):
        out = Path(tmp) / "j.json"
        posts = []

        def fake_http(method, url, key=None, body=None, timeout=120):
            tid = body["state"]["target_id"]
            model = body["model"]
            posts.append((tid, model))
            status, payload = responder(tid, model)
            if isinstance(payload, dict):
                payload = json.dumps(payload)
            return status, payload

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), \
                mock.patch.object(judge, "live_identity", lambda key: fake_identity()), \
                mock.patch.object(judge, "http", fake_http):
            code = judge.execute(CHAPTER, out, targets, Decimal("1"), None)
        result = json.loads(out.read_text(encoding="utf-8"))
        records = [json.loads(line) for line in
                   (Path(tmp) / "j.records.jsonl").read_text(encoding="utf-8").splitlines()]
        return code, result, records, posts

    def test_transport_error_then_success_completes_and_keeps_the_failure_record(self):
        attempts = {}

        def responder(tid, model):
            attempts[(tid, model)] = attempts.get((tid, model), 0) + 1
            if model == "typesafe/jev-1.14" and attempts[(tid, model)] == 1:
                return 503, {"error": {"message": "upstream unavailable"}}
            return 200, BY_MODEL[model]

        with tempfile.TemporaryDirectory() as tmp:
            code, result, records, posts = self._execute(tmp, responder, ["02-002"])

        self.assertEqual(code, 0)
        self.assertTrue(result["all_checks_ok"])
        self.assertEqual(result["coverage"]["typesafe/jev-1.14"]["missing"], [])
        self.assertEqual(result["routing"]["ids"]["retain"], ["02-002"])
        # audit history is not deleted: the 503 attempt and the eventual success coexist
        statuses = [r["status"] for r in records
                    if r["requested_model"] == "typesafe/jev-1.14"]
        self.assertEqual(statuses, ["http_503", "ok"])
        self.assertEqual([(f["model"], f["attempt"], f["status"]) for f in result["failures"]],
                         [("typesafe/jev-1.14", 1, "http_503")])
        self.assertEqual(len(posts), 3 + 1)  # retried exactly the cell that failed

    def test_persistent_transport_error_still_fails_with_the_exact_gap(self):
        def responder(tid, model):
            if model == "openai/gpt-6-luna-decisions":
                return 503, {"error": {"message": "still down"}}
            return 200, BY_MODEL[model]

        with tempfile.TemporaryDirectory() as tmp:
            code, result, records, _ = self._execute(tmp, responder, ["02-002"])

        self.assertEqual(code, 1)
        self.assertFalse(result["all_checks_ok"])
        self.assertEqual(result["coverage"]["openai/gpt-6-luna-decisions"]["missing"],
                         ["02-002"])
        self.assertEqual(result["routing"]["ids"]["pending"], ["02-002"])
        self.assertEqual({f["attempt"] for f in result["failures"]}, {1, 2, 3})
        self.assertEqual(sum(1 for r in records
                             if r["requested_model"] == "openai/gpt-6-luna-decisions"), 3)

    def test_divergent_judgment_state_still_fails_a_complete_matrix(self):
        real_build = judge.build_payload

        def divergent(chapter, tid, model):
            payload = real_build(chapter, tid, model)
            if model == "perplexity/pplx-decider-flash-v1.2":
                payload["state"] = dict(payload["state"], task="altered")
            return payload

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(judge, "build_payload", divergent):
                code, result, _, _ = self._execute(
                    tmp, lambda tid, model: (200, BY_MODEL[model]), ["02-002"])

        self.assertEqual(result["calls"]["ok"], 3)
        self.assertFalse(result["state_consistency"]["02-002"]["consistent"])
        self.assertFalse(result["all_checks_ok"])
        self.assertEqual(code, 1)

    def test_duplicate_response_ids_still_fail_a_complete_matrix(self):
        def responder(tid, model):
            body = json.loads(json.dumps(BY_MODEL[model]))
            body["id"] = "replayed-one-response"  # same node answered both targets
            return 200, body

        with tempfile.TemporaryDirectory() as tmp:
            code, result, _, _ = self._execute(tmp, responder, ["02-001", "02-002"])

        self.assertEqual(result["calls"]["ok"], 6)
        self.assertEqual(result["unique_response_ids"], 1)
        self.assertFalse(result["all_checks_ok"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
