#!/usr/bin/env python3
"""Whole-book driver for the 决策环节 (thin, not a framework).

Wraps the existing per-chapter ``judge.py`` (unchanged contract: the same three
pinned decision models, typed Noul question, exact live alias/provider check, budget
reservation, non-empty-output refusal, transport-only retries).  This script only
adds what a 32-chapter run needs:

  * input verification for all chapters before any spend,
  * optional reuse of already-reviewed chapters (``--reuse-chapters``), whose
    existing evidence is read, marked as reused and never re-charged,
  * per-chapter hard budget caps that do not overlap and sum to at most the
    initial allowance (the recheck and image pools stay untouched),
  * at most 3 chapter workers x 3 model channels = 9 in-flight requests,
  * completion decided by parsed result JSON (coverage + all_checks_ok), never by
    log lines or heartbeats,
  * an exact, per-(chapter, target, model) gap list for partial failures, plus a
    retry mode that re-asks only the missing cells in new, never-overwritten files.

Modes
-----
  (default --dry-run)  verify inputs, price the run, write the plan JSON.
                       No POST to the decisions endpoint.
  --verify             merge all screening evidence for the given chapters and
                       require a complete per-model matrix.
  --execute            spend real money on chapters that are not yet screened.
  --retry GAPS         re-ask only the cells listed in a gaps file, into a new
                       screening/chapter-NN.retry-K.json per chapter.

Exit codes
----------
  0  ok (dry run: inputs + pricing + allocation ok; execute: every chapter complete)
  1  incomplete / failed (gaps file written with the exact missing cells)
  2  refused: chapter inputs invalid or unresolved alignment blocks
  3  refused: budget allocation exceeds the allowance, worker limit, or existing
     paid evidence would have to be overwritten
  4  refused: live pricing/identity unavailable, so nothing can be budgeted
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import judge  # noqa: E402

REPO_ROOT = HERE.parent
ROOT = REPO_ROOT / "work"
RUN_CEILING = judge.RUN_CEILING
INITIAL_ALLOWANCE = judge.INITIAL_ALLOWANCE
RECHECK_ALLOWANCE = judge.RECHECK_ALLOWANCE
IMAGE_ALLOWANCE = judge.IMAGE_ALLOWANCE
MAX_WORKERS = 3
INFLIGHT = MAX_WORKERS * len(judge.MODELS)
MARGIN = Decimal("1.5")          # cap = estimate x margin; screening rarely uses it all
FLOOR_USD = Decimal("0.004")     # minimum cap per chapter so one target is never blocked


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def chdir_of(root: Path, n: int) -> Path:
    return root / "chapters" / f"{n:02d}"


# ------------------------------------------------------------------ input checks
def check_chapter(root: Path, n: int) -> dict:
    """Every blocking condition is explicit and blocks inference, never silently."""
    out = {"chapter": n, "dir": str(chdir_of(root, n)), "ok": False, "errors": []}
    cdir = chdir_of(root, n)
    if not cdir.is_dir():
        out["errors"].append("chapter directory missing")
        return out
    for name in ("source.json", "zh.json", "glossary.json", "alignment.json"):
        if not (cdir / name).is_file():
            out["errors"].append(f"{name} missing")
    if out["errors"]:
        return out
    try:
        source = read_json(cdir / "source.json")
        zh = read_json(cdir / "zh.json")
        glossary = read_json(cdir / "glossary.json")
        alignment = read_json(cdir / "alignment.json")
    except json.JSONDecodeError as exc:
        out["errors"].append(f"unparsable JSON: {exc}")
        return out
    src_ids = [s.get("id") for s in source.get("segments", [])]
    zh_ids = [s.get("id") for s in zh.get("segments", [])]
    out["segments"] = len(src_ids)
    if not src_ids:
        out["errors"].append("source.json has no segments")
    if len(set(src_ids)) != len(src_ids):
        out["errors"].append("duplicate segment ids in source.json")
    if src_ids != zh_ids:
        out["errors"].append(
            f"zh.json id order differs from source.json ({len(zh_ids)} vs {len(src_ids)} ids)")
    prefix = f"{n:02d}-"
    bad_prefix = [i for i in src_ids if not str(i).startswith(prefix)]
    if bad_prefix:
        out["errors"].append(f"segment ids outside chapter prefix: {bad_prefix[:3]}")
    if source.get("chapter") not in (None, n):
        out["errors"].append(f"source.json chapter field is {source.get('chapter')!r}")
    if not isinstance(glossary, list):
        out["errors"].append("glossary.json is not a list")

    chapter = judge.load_chapter(cdir)
    blocked = chapter["blocked"]
    if blocked:
        out["errors"].append(
            f"{len(blocked)} unresolved alignment blocks block inference: "
            + ", ".join(sorted(blocked)[:5]))
    excluded = {s["id"]: r for s in chapter["segments"]
                if (r := judge.exclusion_reason(s, blocked))}
    targets = [s["id"] for s in chapter["segments"]
               if judge.exclusion_reason(s, blocked) is None]
    out.update({
        "targets": len(targets),
        "excluded": excluded,
        "structural_only": sorted(sid for sid, why in excluded.items()
                                  if why.startswith("figure has no text labels")
                                  or why.startswith("figure label counts differ")),
        "empty_translation": sorted(sid for sid, why in excluded.items()
                                    if why.startswith("empty translation")),
        "glossary_entries": len(chapter["glossary"]),
    })
    if not targets:
        out["errors"].append("no judgeable targets")
    out["ok"] = not out["errors"]
    return out


# Measured on the three chapters of the reference run: block counts from the frozen
# HTML authority and real source.json segment counts for the same chapters.  Used only for a
# labelled planning projection when chapter inputs do not exist yet.
PILOT_BLOCKS = {1: 141, 5: 113, 9: 137}
PILOT_SEGMENTS = {1: 141, 5: 118, 9: 141}
PILOT_JUDGED = 389


def projected_targets(chapters: list[int], authority_html: Path | None) -> dict:
    """Planning-only projection for chapters whose inputs do not exist yet.

    Heuristic, explicitly labelled: English block count from the immutable HTML
    authority, scaled by the measured segments-per-block ratio, then by the
    measured judged-targets-per-segment ratio.  It is replaced by the real count as
    soon as chapters/NN exists.  Without --authority-html nothing is projected.
    """
    if authority_html is None:
        return {"reason": "no --authority-html supplied: nothing projected",
                "basis": "none", "per_chapter": {}, "targets_total_est": 0.0}
    seg_ratio = sum(PILOT_SEGMENTS.values()) / sum(PILOT_BLOCKS.values())
    judged_ratio = PILOT_JUDGED / sum(PILOT_SEGMENTS.values())
    rows = {}
    for n in chapters:
        path = authority_html / f"chapter-{n}.html"
        if not path.is_file():
            rows[n] = {"error": "authority html missing"}
            continue
        body = path.read_text(encoding="utf-8", errors="replace").split("<body", 1)[-1]
        body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
        blocks = sum(len(re.findall(rf"<{tag}[\s>]", body, re.I))
                     for tag in ("p", "h1", "h2", "h3", "h4", "blockquote", "table", "figure"))
        segments = blocks * seg_ratio
        rows[n] = {"blocks": blocks, "segments_est": round(segments, 1),
                   "targets_est": round(segments * judged_ratio, 1)}
    total = sum(r.get("targets_est") or 0 for r in rows.values())
    return {"reason": "FULL/chapters inputs absent at plan time",
            "basis": "heuristic: HTML block count x measured segments/block ("
                     f"{seg_ratio:.4f}) x measured judged/segment ({judged_ratio:.4f})",
            "per_chapter": {f"{n:02d}": r for n, r in rows.items()},
            "targets_total_est": round(total, 1)}


def check_all(root: Path, chapters: list[int]) -> dict:
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda n: check_chapter(root, n), chapters))
    return {r["chapter"]: r for r in results}


# ------------------------------------------------------------------ pricing
def live_pricing() -> tuple[dict, dict]:
    """Free GETs only.  Prices come from the live endpoint metadata; nothing is sent."""
    key = judge.load_key()
    ident = judge.live_identity(key)
    prices, problems = {}, {}
    for model, pin in judge.MODELS.items():
        entry = ident["models"].get(model) or {}
        price = judge.price_of(ident, model) if entry.get("ok") else None
        if price is None:
            problems[model] = entry.get("error") or "pricing unavailable"
            continue
        prices[model] = {"prompt_per_token": str(price[0]),
                         "completion_per_token": str(price[1]),
                         "provider_pin": pin,
                         "canonical_model_id": entry.get("canonical_model_id"),
                         "returned_aliases": entry.get("returned_aliases")}
    st, body = judge.http("GET", f"{judge.API}/credits", key)
    credits = json.loads(body).get("data", {}) if st == 200 else {}
    st2, body2 = judge.http("GET", f"{judge.API}/key", key)
    keydata = json.loads(body2).get("data", {}) if st2 == 200 else {}
    return {"models": prices, "identity": ident}, {
        "problems": problems,
        "credits_http_status": st,
        "key_http_status": st2,
        "credits_total": credits.get("total_credits"),
        "credits_usage": credits.get("total_usage"),
        "key_limit_usd": keydata.get("limit"),
        "key_remaining_usd": keydata.get("limit_remaining"),
        "key_usage_usd": keydata.get("usage"),
        "account_remaining_usd": (str(Decimal(str(credits.get("total_credits", 0)))
                                      - Decimal(str(credits.get("total_usage", 0))))
                                  if credits else None),
    }


def historical_usage(screening_dir: Path | None) -> dict:
    """Real per-(target, model) usage from earlier paid attempt records.

    This is measured billing data, not a heuristic - but it belongs to the chapters
    that were run, so per-chapter extrapolation from it is still an estimate.  Without
    ``--calibration-screening`` no calibration is claimed.
    """
    per: dict[str, dict] = {m: {"calls": 0, "input_tokens": 0, "cost_usd": Decimal(0)}
                            for m in judge.MODELS}
    files = sorted(screening_dir.glob("*.records.jsonl")) if screening_dir else []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("status") != "ok":
                continue
            m = rec.get("requested_model")
            if m not in per:
                continue
            per[m]["calls"] += 1
            per[m]["input_tokens"] += int((rec.get("usage") or {}).get("input_tokens") or 0)
            per[m]["cost_usd"] += Decimal(str(rec.get("cost_usd") or "0"))
    out = {"source": [p.name for p in files],
           "kind": ("real returned usage (earlier paid evidence)" if files
                    else "no calibration evidence supplied"),
           "models": {}}
    for m, v in per.items():
        n = v["calls"] or 1
        out["models"][m] = {
            "calls": v["calls"],
            "total_input_tokens": v["input_tokens"],
            "total_cost_usd": format(v["cost_usd"], "f"),
            "mean_input_tokens_per_target": round(v["input_tokens"] / n, 1),
            "mean_cost_usd_per_target": format(v["cost_usd"] / n, "f"),
        }
    out["total_cost_usd"] = format(sum(v["cost_usd"] for v in per.values()), "f")
    return out


def chapter_estimate(cdir: Path, prices: dict, hist: dict, fast: bool) -> dict:
    """Heuristic for chapters whose inputs exist; historical scaling otherwise."""
    chapter = judge.load_chapter(cdir)
    targets = [s["id"] for s in chapter["segments"]
               if judge.exclusion_reason(s, chapter["blocked"]) is None]
    per_model, basis = {}, "historical-scaling (inputs absent)"
    if not fast:
        basis = "heuristic payload tokens x live price"
        for model in judge.MODELS:
            tokens = [judge.estimate_tokens(judge.build_payload(chapter, t, model))
                      for t in targets]
            per_model[model] = {"tokens": sum(tokens), "calls": len(tokens)}
    else:
        ratio = len(targets)
        for model in judge.MODELS:
            mean = float(hist["models"].get(model, {}).get("mean_input_tokens_per_target") or 0)
            per_model[model] = {"tokens": int(mean * ratio), "calls": ratio}
    usd = Decimal(0)
    detail = {}
    for model, v in per_model.items():
        prompt = Decimal(str((prices.get(model) or {}).get("prompt_per_token") or "0"))
        cost = Decimal(v["tokens"]) * prompt
        detail[model] = {"calls": v["calls"], "tokens": v["tokens"],
                         "estimated_usd": format(cost, "f")}
        usd += cost
    return {"targets": len(targets), "basis": basis,
            "estimated_usd": format(usd, "f"), "models": detail}


def allocate(estimates: dict[int, Decimal], allowance: Decimal) -> tuple[dict[int, Decimal], list]:
    """Non-overlapping per-chapter caps; the sum never exceeds the allowance."""
    warnings = []
    raw = {}
    for n, est in estimates.items():
        raw[n] = max(est * MARGIN, FLOOR_USD)
    total = sum(raw.values(), Decimal(0))
    scale = Decimal(1)
    if total > allowance:
        scale = allowance / total
        warnings.append(
            f"estimates + margin ({total}) exceed the {allowance} allowance; "
            f"caps scaled by {scale:.4f} - chapters may stop on budget and report gaps")
    caps = {}
    running = Decimal(0)
    for n in sorted(raw):
        cap = (raw[n] * scale).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        if running + cap > allowance:
            cap = (allowance - running).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        if cap <= 0:
            warnings.append(f"chapter {n:02d} got a zero cap")
        caps[n] = cap
        running += cap
    return caps, warnings


# ------------------------------------------------------------------ plan
def build_plan(root: Path, chapters: list[int], allowance: Decimal, fast: bool,
               reuse_chapters: list[int] | None = None,
               calibration: Path | None = None,
               authority_html: Path | None = None) -> tuple[dict, int]:
    reuse_chapters = list(reuse_chapters or [])
    checks = check_all(root, chapters)
    pricing, live_meta = live_pricing()
    hist = historical_usage(calibration)
    plan = {
        "schema": "snowmoon-fullbook-screening-plan/1",
        "generated_at": now(),
        "mode": "dry-run (no decisions request sent)",
        "endpoint": judge.ENDPOINT,
        "root": str(root),
        "scope": {"chapters": chapters, "count": len(chapters),
                  "reused_chapters": reuse_chapters},
        "models": judge.MODELS,
        "concurrency": {"chapter_workers": MAX_WORKERS, "channels_per_chapter": 3,
                        "max_inflight": INFLIGHT},
        "budget": {
            "run_ceiling_usd": str(RUN_CEILING),
            "initial_screening_allowance_usd": str(allowance),
            "recheck_allowance_usd": str(RECHECK_ALLOWANCE),
            "image_judgment_allowance_usd": str(IMAGE_ALLOWANCE),
            "double_reserve_note": "chapter caps are disjoint; the recheck pool is not "
                                   "reserved here and image judgments are not paid here",
        },
        "live": live_meta,
        "historical_usage_real": hist,
        "chapters": {},
        "reused_evidence": {},
        "warnings": [],
    }
    refusing = False
    if live_meta["problems"]:
        plan["warnings"].append(
            "refused to budget: live pricing/identity unavailable for "
            + ", ".join(sorted(live_meta["problems"])))
        plan["status"] = "refused_missing_pricing"
        refusing = True

    estimates: dict[int, Decimal] = {}
    for n, chk in checks.items():
        entry = {"check": chk}
        cdir = chdir_of(root, n)
        if not chk["ok"]:
            entry["state"] = "input_invalid"
            refusing = True
        else:
            est = chapter_estimate(cdir, pricing["models"], hist, fast)
            entry["estimate"] = est
            estimates[n] = Decimal(est["estimated_usd"])
            entry["state"] = "ready"
        plan["chapters"][f"{n:02d}"] = entry

    for n in reuse_chapters:
        path = (calibration or (root / "screening")) / f"chapter-{n:02d}.json"
        entry = {"source": path.name, "kind": "reused earlier screening (not recharged)",
                 "present": path.is_file()}
        if path.is_file():
            data = read_json(path)
            cov = data.get("coverage") or {}
            complete = (bool(data.get("all_checks_ok"))
                        and all((cov.get(m) or {}).get("ok") == (cov.get(m) or {}).get("expected")
                                for m in judge.MODELS))
            entry.update({"complete": complete, "actual_usd": (data.get("cost") or {}).get("actual_usd"),
                          "targets_judged": len(data.get("targets") or [])})
            if not complete:
                plan["warnings"].append(f"reused evidence for chapter {n:02d} is incomplete")
        plan["reused_evidence"][f"{n:02d}"] = entry

    caps, warnings = allocate(estimates, allowance) if estimates else ({}, [])
    plan["warnings"].extend(warnings)
    total_est = sum(estimates.values(), Decimal(0))
    total_cap = sum(caps.values(), Decimal(0))
    for key, cap in caps.items():
        plan["chapters"][f"{key:02d}"]["cap_usd"] = str(cap)
    plan["allocation"] = {
        "estimated_total_usd": format(total_est, "f"),
        "allocated_caps_total_usd": format(total_cap, "f"),
        "within_allowance": total_cap <= allowance,
        "allowance_usd": str(allowance),
        "per_chapter_disjoint": True,
        "estimate_basis": ("heuristic payload tokens x live prompt price (conservative "
                           "chars/2 + 200, not tokenizer-exact) plus the supplied calibration "
                           "evidence") if not fast else
                          "measured mean input tokens per target from the supplied "
                          "calibration evidence, scaled by target count",
        "calibration_real": ({m: hist["models"][m]["mean_cost_usd_per_target"]
                              for m in judge.MODELS} if hist["models"] else {}),
    }
    if any(c["state"] != "ready" for c in plan["chapters"].values()):
        proj = projected_targets(sorted(n for n in chapters
                                        if plan["chapters"][f"{n:02d}"]["state"] != "ready"),
                                 authority_html)
        if hist["models"]:
            per_target_real = sum(Decimal(hist["models"][m]["mean_cost_usd_per_target"])
                                  for m in judge.MODELS)
            proj["cost_usd_est_from_calibration_per_target"] = format(
                Decimal(str(proj["targets_total_est"])) * per_target_real, "f")
            proj["cost_basis"] = ("measured usage.cost / judged target from the supplied "
                                  "calibration run, NOT a charge")
        else:
            proj["cost_basis"] = "no calibration supplied: no cost projection"
        plan["projection"] = proj
        plan["warnings"].append(
            "chapter inputs are incomplete, so no per-chapter cap can be allocated yet; "
            "the projection is heuristic and must be re-planned when inputs land")
    plan["structural_only_all"] = {f"{n:02d}": checks[n].get("structural_only", [])
                                   for n in checks if checks[n].get("structural_only")}
    plan["missing_translation"] = {f"{n:02d}": checks[n].get("empty_translation", [])
                                   for n in checks if checks[n].get("empty_translation")}
    if plan["missing_translation"]:
        plan["warnings"].append(
            "empty translations are reported, not silently retained: "
            + json.dumps(plan["missing_translation"], ensure_ascii=False))
    plan["status"] = ("refused_missing_pricing" if refusing and live_meta["problems"]
                      else "refused_invalid_inputs" if refusing else "ready")
    code = 4 if live_meta["problems"] else (2 if refusing else 0)
    return plan, code


# ------------------------------------------------------------------ evidence verification
def load_records(path: Path) -> list[dict]:
    jsonl = path.with_suffix(".records.jsonl")
    if not jsonl.is_file():
        return []
    return [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def merged_matrix(root: Path, n: int) -> dict:
    """All paid evidence for one chapter: base result + every retry file beside it."""
    screen = root / "screening"
    files = [screen / f"chapter-{n:02d}.json"] + sorted(
        screen.glob(f"chapter-{n:02d}.retry-*.json"))
    cells: dict[tuple[str, str], dict] = {}
    responses: list[str] = []
    sources = []
    for path in files:
        if not path.is_file():
            continue
        sources.append(str(path))
        data = read_json(path)
        for rec in load_records(path) + (data.get("records") or []):
            if rec.get("status") != "ok":
                continue
            key = (rec.get("target_id"), rec.get("requested_model"))
            if key in cells:
                continue
            if not (rec.get("identity") or {}).get("model_matched_exactly") or \
                    not (rec.get("identity") or {}).get("provider_matched"):
                continue
            cells[key] = rec
            if rec.get("response_id"):
                responses.append(rec["response_id"])
    chk = check_chapter(root, n)
    expected = []
    if chk.get("ok"):
        chapter = judge.load_chapter(chdir_of(root, n))
        expected = [s["id"] for s in chapter["segments"]
                    if judge.exclusion_reason(s, chapter["blocked"]) is None]
    missing = [{"target_id": t, "model": m} for t in expected for m in judge.MODELS
               if (t, m) not in cells]
    dupes = len(responses) - len(set(responses))
    return {"chapter": n, "sources": sources, "input_ok": bool(chk.get("ok")),
            "input_errors": chk.get("errors") or [],
            "expected_cells": len(expected) * len(judge.MODELS),
            "ok_cells": len(cells), "missing": missing, "duplicate_response_ids": dupes,
            "complete": bool(chk.get("ok")) and bool(expected) and not missing and dupes == 0,
            "targets": len(expected)}


def verify(root: Path, chapters: list[int], out_path: Path) -> int:
    rows = {n: merged_matrix(root, n) for n in chapters}
    gaps = []
    for n, row in rows.items():
        for miss in row["missing"]:
            gaps.append({"chapter": n, **miss})
    result = {
        "schema": "snowmoon-fullbook-coverage/1",
        "generated_at": now(),
        "scope": chapters,
        "chapters": {f"{n:02d}": {k: v for k, v in row.items() if k != "missing"}
                     for n, row in rows.items()},
        "missing_cells": len(gaps),
        "gaps": gaps,
        "complete": all(row["complete"] for row in rows.values()),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps({"chapters": len(rows),
                      "complete": result["complete"],
                      "missing_cells": len(gaps),
                      "per_chapter": {f"{n:02d}": [row["ok_cells"], row["expected_cells"]]
                                      for n, row in rows.items()}}, ensure_ascii=False))
    return 0 if result["complete"] else 1


# ------------------------------------------------------------------ execute
def run_judge(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    tail = (proc.stdout or "")[-1200:] + (proc.stderr or "")[-600:]
    return proc.returncode, tail


def chapter_complete(result_path: Path, cap: str) -> dict:
    """Completion is read from the parsed result file, never inferred from logs."""
    if not result_path.is_file() or result_path.stat().st_size == 0:
        return {"complete": False, "reason": "no result file written"}
    data = read_json(result_path)
    cov = data.get("coverage") or {}
    missing = []
    for model in judge.MODELS:
        entry = cov.get(model) or {}
        for tid in entry.get("missing") or []:
            missing.append({"target_id": tid, "model": model})
        if not entry:
            missing.append({"model": model, "target_id": None,
                            "reason": "model absent from coverage"})
    complete = bool(data.get("all_checks_ok")) and not missing
    return {"complete": complete, "actual_usd": (data.get("cost") or {}).get("actual_usd"),
            "unknown_cost_calls": data.get("cost", {}).get("cost_unknown_calls"),
            "missing": missing, "cap_usd": cap,
            "reason": "" if complete else "incomplete coverage or failed checks"}


def execute(root: Path, plan: dict, chapters: list[int], workers: int) -> int:
    caps = {n: Decimal(plan["chapters"][f"{n:02d}"]["cap_usd"]) for n in chapters}
    if sum(caps.values(), Decimal(0)) > INITIAL_ALLOWANCE:
        print(json.dumps({"refused": "chapter caps exceed the initial screening allowance"}))
        return 3
    if workers > MAX_WORKERS:
        print(json.dumps({"refused": f"workers {workers} > {MAX_WORKERS} (9 in-flight max)"}))
        return 3
    screen = root / "screening"
    screen.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    outcomes: dict[int, dict] = {}

    def one(n: int) -> None:
        out = screen / f"chapter-{n:02d}.json"
        if out.is_file() and out.stat().st_size > 0:
            state = chapter_complete(out, str(caps[n]))
            state["action"] = "existing_evidence_kept (never re-requested)"
            with lock:
                outcomes[n] = state
            return
        cmd = [sys.executable, str(HERE / "judge.py"),
               "--chapter-dir", str(chdir_of(root, n)),
               "--output", str(out), "--execute",
               "--budget-usd", str(caps[n])]
        code, tail = run_judge(cmd)
        state = chapter_complete(out, str(caps[n]))
        state["action"] = "judge.py executed"
        state["judge_exit"] = code
        state["log_tail"] = tail
        with lock:
            outcomes[n] = state

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, chapters))

    gaps = []
    for n in chapters:
        for miss in outcomes[n].get("missing") or []:
            gaps.append({"chapter": n, **miss})
    summary = {
        "schema": "snowmoon-fullbook-screening-run/1",
        "finished_at": now(),
        "chapters": {f"{n:02d}": outcomes[n] for n in chapters},
        "caps_total_usd": format(sum(caps.values(), Decimal(0)), "f"),
        "actual_usd": format(sum((Decimal(str(outcomes[n].get("actual_usd") or "0"))
                                  for n in chapters), Decimal(0)), "f"),
        "complete_chapters": sum(1 for n in chapters if outcomes[n].get("complete")),
        "incomplete_chapters": [n for n in chapters if not outcomes[n].get("complete")],
        "gaps": gaps,
    }
    (screen / "run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if gaps:
        gap_path = screen / "gaps.json"
        if gap_path.is_file() and gap_path.stat().st_size > 0:
            gap_path = screen / f"gaps-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        gap_path.write_text(json.dumps({
            "schema": "snowmoon-fullbook-gaps/1", "generated_at": now(),
            "missing": gaps,
            "retry_command": f"{sys.executable} {HERE / 'screen_all.py'} --retry {gap_path}",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["gaps_file"] = str(gap_path)
    print(json.dumps({k: summary[k] for k in
                      ("complete_chapters", "incomplete_chapters", "actual_usd",
                       "caps_total_usd")}, ensure_ascii=False))
    print(f"gap cells: {len(gaps)}" + (f" -> {summary.get('gaps_file')}" if gaps else ""))
    return 0 if not gaps and len(summary["incomplete_chapters"]) == 0 else 1


def retry(root: Path, gaps_path: Path, workers: int) -> int:
    """Fill only the cells still missing, one new evidence file per (chapter, model).

    The gaps file is a request, not a cache: cells merged_matrix already covers are not
    re-requested, every model writes to its own file (no shared output, no break after
    the first model), and the exit code is decided by the gaps left after the run, read
    back from the evidence, never by a child exit code.
    """
    data = read_json(gaps_path)
    requested: dict[int, dict[str, list[str]]] = {}
    for cell in data.get("missing") or []:
        if not cell.get("target_id"):
            continue
        requested.setdefault(int(cell["chapter"]), {}).setdefault(
            cell["model"], []).append(cell["target_id"])
    screen = root / "screening"
    screen.mkdir(parents=True, exist_ok=True)
    outcomes: dict[int, dict] = {}
    lock = threading.Lock()

    def one(n: int) -> None:
        still_missing = {(m["target_id"], m["model"])
                         for m in merged_matrix(root, n)["missing"]}
        calls = []
        for model, targets in requested[n].items():
            wanted = [t for t in dict.fromkeys(targets) if (t, model) in still_missing]
            if not wanted:
                continue
            # Paid evidence is immutable: pick the first sequence number whose result and
            # records files do not exist yet, so an earlier retry is never overwritten.
            seq, slug = 1, model.replace("/", "_")
            out = screen / f"chapter-{n:02d}.retry-{seq}.{slug}.json"
            while out.exists() or out.with_suffix(".records.jsonl").exists():
                seq += 1
                out = screen / f"chapter-{n:02d}.retry-{seq}.{slug}.json"
            cmd = [sys.executable, str(HERE / "judge.py"),
                   "--chapter-dir", str(chdir_of(root, n)), "--output", str(out),
                   "--execute", "--only-model", model,
                   "--target-ids", ",".join(wanted), "--budget-usd",
                   str(min(Decimal("0.05") * max(1, len(wanted)), Decimal("0.30")))]
            code, tail = run_judge(cmd)
            calls.append({"model": model, "targets": len(wanted), "path": str(out),
                          "exit": code, "log_tail": tail})
        with lock:
            outcomes[n] = {"action": "retried" if calls else "no_gap_to_retry",
                           "calls": calls}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, sorted(requested)))

    rows = {n: merged_matrix(root, n) for n in sorted(requested)}
    gaps = [{"chapter": n, **miss} for n, row in rows.items() for miss in row["missing"]]
    complete = all(row["complete"] for row in rows.values())
    print(json.dumps({"complete": complete, "remaining_gaps": len(gaps),
                      "chapters": {n: o["action"] for n, o in outcomes.items()},
                      "gaps": gaps}, ensure_ascii=False))
    return 0 if complete else 1


# ------------------------------------------------------------------ CLI
def parse_chapters(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    bad = [n for n in out if not 1 <= n <= 32]
    if bad:
        raise SystemExit(f"chapter numbers out of range: {bad}")
    return sorted(set(out))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT,
                    help="run root holding chapters/NN and screening/ (default: repo work/)")
    ap.add_argument("--chapters", default="", help="e.g. 2-32 or 2,4,10-13")
    ap.add_argument("--reuse-chapters", default="",
                    help="already-screened chapters whose evidence is reused, never re-charged")
    ap.add_argument("--calibration-screening", type=Path,
                    help="directory of earlier *.records.jsonl paid evidence used only to "
                         "calibrate the estimate")
    ap.add_argument("--authority-html", type=Path,
                    help="frozen English HTML snapshot used only for the planning projection")
    ap.add_argument("--exclude", default="", help="chapters to drop from the default scope")
    ap.add_argument("--allowance-usd", default=str(INITIAL_ALLOWANCE))
    ap.add_argument("--workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--fast-estimate", action="store_true",
                    help="scale supplied calibration usage instead of building every payload")
    ap.add_argument("--plan", type=Path, help="where to write the plan JSON")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--retry", type=Path)
    args = ap.parse_args(argv)

    reuse = parse_chapters(args.reuse_chapters) if args.reuse_chapters else []

    if args.retry:
        return retry(args.root, args.retry, args.workers)
    if args.execute:
        if args.workers > MAX_WORKERS:
            print(json.dumps({"refused": f"workers {args.workers} > {MAX_WORKERS}"}))
            return 3
        plan_path = args.plan or (args.root / "screening-plan.json")
        plan, code = build_plan(args.root, parse_chapters(args.chapters) if args.chapters
                                else [n for n in range(1, 33) if n not in reuse],
                                Decimal(args.allowance_usd), args.fast_estimate,
                                reuse, args.calibration_screening, args.authority_html)
        if code != 0:
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
            print(json.dumps({"refused": plan["status"], "warnings": plan["warnings"]},
                             ensure_ascii=False))
            return code
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        chapters = [int(k) for k in plan["chapters"]]
        return execute(args.root, plan, chapters, args.workers)

    chapters = (parse_chapters(args.chapters) if args.chapters
                else [n for n in range(1, 33)
                      if n not in reuse and n not in parse_chapters(args.exclude)])
    if args.verify:
        out = args.plan or (args.root / "screening" / "coverage-verified.json")
        return verify(args.root, chapters, out)
    plan, code = build_plan(args.root, chapters, Decimal(args.allowance_usd),
                            args.fast_estimate, reuse, args.calibration_screening,
                            args.authority_html)
    plan_path = args.plan or (args.root / "screening-plan.json")
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    print(json.dumps({
        "status": plan["status"], "chapters": len(chapters),
        "ready": sum(1 for c in plan["chapters"].values() if c["state"] == "ready"),
        "invalid": [k for k, c in plan["chapters"].items() if c["state"] != "ready"],
        "allocation": plan["allocation"],
        "warnings": plan["warnings"][:6],
        "plan": str(plan_path),
    }, ensure_ascii=False, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
