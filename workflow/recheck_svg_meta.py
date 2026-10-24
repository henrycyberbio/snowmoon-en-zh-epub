#!/usr/bin/env python3
"""Recheck hidden SVG <title>/<desc> Chinese candidates with the pinned decision models.

Reuses workflow/judge.py helpers (same HTTP path, provider pin, answer parsing, budget
accounting).  Native JSON state, no chat scorer.

Why this is a separate pass: the ordinary figure branch of judge.py only reads the
visible <text>/<tspan> labels, so a state without the hidden accessibility metadata
cannot verify those strings.  Each figure's candidate <title>/<desc> is therefore judged
as its own metadata target, with the visible label pairs and percentages supplied as
term background.

  python workflow/recheck_svg_meta.py --targets prompts/svg-metadata-targets.json \
      --output-dir work/rechecks/27 --budget-usd 0.02
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import judge  # noqa: E402  (shared transport, identity pin, parsing, budget)

REPO_ROOT = HERE.parent
ADOPT_BELOW = Decimal("0.3")

META_INSTRUCTIONS = (
    "Target `{tid}` is the hidden accessibility metadata of one figure: the SVG root "
    "<title id=\"ttl\"> and/or <desc id=\"desc\"> strings referenced by aria-labelledby. "
    "The figure is a poll result chart; its visible labels are supplied as ordered "
    "English/Chinese/value triples plus the raw visible text in reading order. "
    "Judge only the proposed Chinese metadata strings listed under `elements` "
    "(element title/desc). Question: does the proposed Chinese metadata contain a "
    "substantive error - a mistranslation, omitted or added meaning, a wrong referent, "
    "or a number, percentage, option label or ordering that contradicts the English "
    "metadata or the visible labels? Visible labels, drawing, layout and coordinates "
    "are NOT under judgment and are not proposed for change."
)
META_CRITERIA = {
    "true": ("The proposed Chinese metadata for `{tid}` contains at least one substantive error: "
             "it mistranslates the English metadata, drops meaning, adds meaning the English does not "
             "carry, or states a number/percentage/option label that differs from the English metadata "
             "or the visible labels."),
    "false": ("The proposed Chinese metadata for `{tid}` preserves the English metadata meaning and "
              "matches the figure's visible labels and percentages; only wording differs."),
}


def build_payload(target: dict, model: str) -> dict:
    tid = target["target_id"]
    return {
        "model": model,
        "provider": {"only": [judge.MODELS[model]], "allow_fallbacks": False},
        "state": {
            "task": "chapter translation review - SVG accessibility metadata",
            "scope": "svg_metadata",
            "target_id": tid,
            "asset": target["asset"],
            "elements": target["elements"],
            "visible_labels": target["visible_labels"],
            "visible_text_reading_order": target["visible_text"],
            "notes": "The candidate Chinese replaces only the hidden root <title>/<desc> text; "
                     "every visible <text> node, coordinate, style, defs and viewBox stays as-is.",
        },
        "questions": {
            judge.QUESTION_ID: {
                "type": "noul",
                "instructions": META_INSTRUCTIONS.replace("{tid}", tid),
                "criteria": {k: v.replace("{tid}", tid) for k, v in META_CRITERIA.items()},
            }
        },
    }


def load_targets(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    targets = data.get("targets") if isinstance(data, dict) else data
    if not isinstance(targets, list) or not targets:
        raise SystemExit(f"{path}: expected a non-empty list of metadata targets")
    for t in targets:
        missing = [k for k in ("target_id", "asset", "elements", "visible_labels")
                   if not t.get(k)]
        if missing:
            raise SystemExit(f"{path}: target {t.get('target_id')!r} lacks {missing}")
        for el in t["elements"]:
            if el.get("element") not in ("title", "desc"):
                raise SystemExit(f"{t['target_id']}: element must be title or desc")
            for key in ("english", "current_chinese", "candidate_chinese"):
                if key not in el:
                    raise SystemExit(f"{t['target_id']}/{el.get('element')}: missing {key}")
    return targets


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets", type=Path, required=True,
                    help="JSON list of metadata targets (asset, elements, visible labels)")
    ap.add_argument("--output-dir", type=Path, required=True,
                    help="directory for the immutable per-target evidence files")
    ap.add_argument("--budget-usd", default="0.02",
                    help="hard cap for this metadata pass (never a whole-lane cap)")
    ap.add_argument("--tag", default="svg-metadata", help="evidence file name prefix")
    args = ap.parse_args(argv)

    targets = load_targets(args.targets)
    cap = Decimal(args.budget_usd)
    key = judge.load_key()
    ident = judge.live_identity(key)
    missing = {m: ident["models"].get(m, {}).get("error") or "unavailable"
               for m in judge.MODELS if not ident["models"].get(m, {}).get("ok")}
    if missing:
        print(json.dumps({"refused": "live identity/pin unavailable", "models": missing},
                         ensure_ascii=False))
        return 4
    budget = judge.Budget(cap)
    total = Decimal(0)
    unknown = 0
    summary = []
    for target in targets:
        tid = target["target_id"]
        out = args.output_dir / f"{args.tag}-{tid.replace('-', '_')}.json"
        jsonl = out.with_suffix(".records.jsonl")
        if (out.is_file() and out.stat().st_size > 0) or \
                (jsonl.is_file() and jsonl.stat().st_size > 0):
            print(f"refused: {out} exists with content (paid evidence is never overwritten)")
            return 3
        out.parent.mkdir(parents=True, exist_ok=True)
        fps = {judge.fingerprint(build_payload(target, m)) for m in judge.MODELS}
        record = {
            "target_id": tid,
            "scope": "svg_metadata",
            "asset": target["asset"],
            "mode": "svg_metadata_recheck",
            "endpoint": judge.ENDPOINT,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fingerprints": sorted(fps),
            "state_consistent_across_models": len(fps) == 1,
            "models": judge.MODELS,
            "elements": target["elements"],
            "visible_labels": target["visible_labels"],
            "calls": [],
            "budget_cap_usd": str(cap),
        }
        if len(fps) != 1:
            out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            print(f"identity divergence for {tid}")
            return 2
        jsonl.touch()
        for model in judge.MODELS:
            payload = build_payload(target, model)
            est = judge.estimated_cost(ident, model, {}, payload)
            ok, why = budget.allow(est)
            entry = {"target_id": tid, "requested_model": model,
                     "provider_pin": judge.MODELS[model],
                     "state_fingerprint": judge.fingerprint(payload),
                     "attempted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "estimated_cost_usd": str(est) if est is not None else None,
                     "request": payload}
            if not ok:
                entry.update({"status": "budget_stop", "error": why})
            else:
                status, raw = judge.http("POST", judge.ENDPOINT, key, payload)
                entry.update({"http_status": status, "raw_response": raw})
                if status == 200:
                    try:
                        parsed = judge.parse_answer(raw)
                    except Exception as exc:  # noqa: BLE001
                        entry.update({"status": "parse_error", "error": repr(exc)})
                    else:
                        good, detail = judge.identity_ok(model, parsed, ident)
                        entry.update(parsed)
                        entry["identity"] = detail
                        entry["status"] = "ok" if good else "identity_mismatch"
                        budget.settle(est, parsed.get("cost_usd"), est)
                        if parsed.get("cost_usd") is None:
                            unknown += 1
                        else:
                            total += Decimal(parsed["cost_usd"])
                else:
                    budget.settle(est, None, est)
                    entry.update({"status": "http_error"})
                    unknown += 1
            with jsonl.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            record["calls"].append({k: entry.get(k) for k in
                                    ("requested_model", "provider_pin", "status", "probability",
                                     "cost_usd", "http_status", "identity", "error")})
            print(tid, model, entry.get("status"), entry.get("probability"),
                  entry.get("cost_usd"))
        probs = [Decimal(c["probability"]) for c in record["calls"]
                 if c.get("probability") is not None]
        record["probabilities"] = [format(p, "f") for p in probs]
        record["all_below_adoption_threshold"] = bool(
            len(probs) == len(judge.MODELS) and all(p < ADOPT_BELOW for p in probs)
            and all(c.get("status") == "ok" for c in record["calls"]))
        record["cost_usd_calls"] = [c.get("cost_usd") for c in record["calls"]]
        out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        summary.append({"target_id": tid, "probs": record["probabilities"],
                        "below_threshold": record["all_below_adoption_threshold"]})
    print(json.dumps({"summary": summary, "actual_cost_usd": format(total, "f"),
                      "unknown_cost_calls": unknown,
                      "reserved_usd": format(budget.reserved, "f")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
