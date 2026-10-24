#!/usr/bin/env python3
"""决策环节 (decision step): typed Noul judgments from three pinned decision models.

One Noul per target segment, identical state/questions for all judges; only the
documented transport identity (model + provider pin) differs.  Decisions API only
(``POST https://openrouter.ai/api/alpha/decisions``), never chat completions.

The ``request`` field written to the evidence records is the actual wire body sent to
the endpoint, including the instructions/criteria and the constructed state; those
strings are the real prompts, not a separate system prompt.

Modes
-----
  --preflight   metadata/credit/key-cap discovery + request-cost estimate (no inference)
  (default)     build payloads, verify cross-model identity, write a plan (no inference)
  --execute     perform the paid calls, one immutable record per HTTP attempt

Exit codes
----------
  0   execute: every expected (target, model) call succeeded, parsed, and passed
      live model/provider identity verification; dry run: identity check ok
  1   execute: incomplete / failed / unverified -> partial evidence saved
  2   dry run identity divergence
  3   refused: output or records file already non-empty (never overwrite paid evidence)
  4   refused: live metadata/pricing does not allow budgeting the run

Usage
-----
  python workflow/judge.py --preflight --output work/preflight.json [--estimate-root work]
  python workflow/judge.py --chapter-dir work/chapters/01 --output work/plan-01.json
  python workflow/judge.py --chapter-dir work/chapters/01 --output work/judgments-01.json \
      --execute --budget-usd 1

Credential: OPENROUTER_API_KEY, else the file named by OPENROUTER_KEY_FILE, else the
repo-local, git-ignored ``.env`` (never printed, never written to project files).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
API = "https://openrouter.ai/api/v1"
MODELS = {
    # exact requested id -> documented provider pin (provider_name in endpoint metadata)
    "typesafe/jev-1.14": "TypeSafe",
    "openai/gpt-6-luna-decisions": "OpenAI",
    "perplexity/pplx-decider-flash-v1.2": "Perplexity",
}
QUESTION_ID = "substantive_error"
BAND_SUSPECT = Decimal("0.7")
BAND_RETAIN = Decimal("0.3")
WINDOW = 2
MAX_TRANSPORT_RETRIES = 2
# Approved external-decision budget gates for the published run.  They are ceilings for
# reservation/refusal, never a claim about what was spent.
RUN_CEILING = Decimal("4.9")
INITIAL_ALLOWANCE = Decimal("3.6")
RECHECK_ALLOWANCE = Decimal("1.1")
IMAGE_ALLOWANCE = Decimal("0.2")

INSTRUCTIONS = (
    "Evaluate segment `{tid}` only (the block whose role is target). "
    "Question: does the target Chinese translation contain a substantive semantic "
    "error that requires correction relative to the English source and the supplied "
    "context? Count omission or addition of meaning, wrong referent or actor, "
    "condition/negation/causality/comparison errors, numeric/unit/formula meaning, "
    "and key terminology. Do not count stylistic preference, formatting-only "
    "differences, or documented source-version updates. Context blocks are evidence "
    "only; do not judge them."
)
FIGURE_INSTRUCTIONS = (
    " Target `{tid}` is a figure block: its judged content is the figure's label set, "
    "given as ordered English/Chinese label pairs. Judge whether any Chinese label "
    "mislabels, mistranslates or drops an English label (names, units, numbers); "
    "identical numeric labels are not errors."
)
CRITERIA = {
    "true": (
        "Segment `{tid}` contains at least one substantive semantic error: the Chinese "
        "says something the English does not say, omits meaning the English carries, or "
        "misstates a referent, actor, condition, negation, causal link, comparison, "
        "number or key term."
    ),
    "false": (
        "Segment `{tid}` preserves the English meaning. Only wording, tone, rhythm or "
        "formatting differ, or the difference follows a documented source-version update."
    ),
}


# ---------------------------------------------------------------- credentials
def load_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    candidates = []
    if os.environ.get("OPENROUTER_KEY_FILE"):
        candidates.append(Path(os.environ["OPENROUTER_KEY_FILE"]))
    candidates.append(REPO_ROOT / ".env")
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("OPENROUTER_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    raise SystemExit(
        "No OpenRouter credential: set OPENROUTER_API_KEY or OPENROUTER_KEY_FILE, or "
        f"put OPENROUTER_API_KEY=... in {REPO_ROOT / '.env'}"
    )


def http(method: str, url: str, key: str | None = None, body: dict | None = None,
         timeout: int = 120) -> tuple[int, str]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        return 0, f'{{"transport_error": {json.dumps(str(e.reason))}}}'
    except (TimeoutError, OSError) as e:
        return 0, f'{{"transport_error": {json.dumps(str(e))}}}'


# ---------------------------------------------------------------- chapter data
def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def svg_labels(path: Path) -> list[str]:
    """Ordered <text>/<tspan> label strings.  tspan children replace their parent text
    so a label is neither dropped nor counted twice."""
    if not path.is_file():
        return []
    out: list[str] = []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    for el in root.iter():
        if el.tag.split("}")[-1] != "text":
            continue
        tspans = [t for t in el.iter()
                  if t.tag.split("}")[-1] == "tspan" and (t.text or "").strip()]
        if tspans:
            out.extend(" ".join("".join(t.itertext()).split()) for t in tspans)
        else:
            joined = " ".join("".join(el.itertext()).split())
            if joined:
                out.append(joined)
    return [s for s in out if s]


def figure_segment(seg: dict, chapter_dir: Path, figures: list[dict]) -> dict:
    """A figure is a semantic block only through its text labels, never through the
    Chinese markdown alt placeholder that the extractor left in `en`."""
    entry = next((f for f in figures if f.get("segment_id") == seg.get("id")), {})
    en_rel, zh_rel = entry.get("en"), entry.get("zh")
    en_labels = svg_labels(chapter_dir / en_rel) if en_rel else []
    zh_labels = svg_labels(chapter_dir / zh_rel) if zh_rel else []
    base = {
        "id": seg.get("id"),
        "type": seg.get("type"),
        "kind": "figure",
        "device": "figure",
        "en_alt": seg.get("en") or "",
        "assets": {"en": en_rel, "zh": zh_rel},
    }
    if not en_labels and not zh_labels:
        base.update({"en": "", "zh": "", "labels": [], "no_text_labels": True})
        return base
    if len(en_labels) != len(zh_labels):
        base.update({"en": "", "zh": "", "labels": [],
                     "label_count_mismatch": {"en": len(en_labels), "zh": len(zh_labels)}})
        return base
    pairs = [{"index": i, "en": e, "zh": z}
             for i, (e, z) in enumerate(zip(en_labels, zh_labels))]
    base.update({
        "labels": pairs,
        "en": "figure label texts (reading order): " + " | ".join(en_labels),
        "zh": "图中文字标签（阅读顺序）：" + " | ".join(zh_labels),
    })
    return base


def load_chapter(chapter_dir: Path) -> dict:
    src = read_json(chapter_dir / "source.json")
    zh = read_json(chapter_dir / "zh.json")
    glossary_path = chapter_dir / "glossary.json"
    glossary = read_json(glossary_path) if glossary_path.is_file() else []
    alignment_path = chapter_dir / "alignment.json"
    alignment = read_json(alignment_path) if alignment_path.is_file() else {}
    zh_by_id = {seg["id"]: seg.get("zh") or "" for seg in zh.get("segments", [])}
    figures = src.get("figures") or []
    segments = []
    for seg in src.get("segments", []):
        sid = seg.get("id")
        if seg.get("device") == "figure":
            item = figure_segment(seg, chapter_dir, figures)
            item["zh_alt"] = zh_by_id.get(sid, "")
            item["zh"] = item.get("zh") or ""
            segments.append(item)
            continue
        segments.append({
            "id": sid,
            "type": seg.get("type"),
            "kind": "table" if seg.get("device") == "table" else "text",
            "en": seg.get("en") or "",
            "zh": zh_by_id.get(sid, ""),
        })
    blocked = collect_blocked(alignment)
    return {
        "chapter": src.get("chapter") or zh.get("chapter"),
        "segments": segments,
        "glossary": glossary,
        "notes": zh.get("notes") or [],
        "terms": zh.get("terms") or {},
        "blocked": blocked,
        "alignment": alignment,
    }


def collect_blocked(alignment: dict) -> dict[str, str]:
    """Unresolved mapping blocks stop affected judgments; never judge around them."""
    out: dict[str, str] = {}
    entries = alignment.get("unresolved_blocks") or alignment.get("unresolved") or []
    if isinstance(entries, dict):
        entries = [entries]
    for entry in entries:
        if isinstance(entry, str):
            out[entry] = "unresolved alignment block"
            continue
        ids = entry.get("segment_ids") or entry.get("ids") or []
        if isinstance(ids, str):
            ids = [ids]
        reason = entry.get("reason") or "unresolved alignment block"
        for sid in ids:
            out[sid] = reason
    return out


def exclusion_reason(seg: dict, blocked: dict) -> str | None:
    """Why a segment cannot be judged.  Never silently skipped, never a pass."""
    if seg["id"] in blocked:
        return "unresolved alignment block: " + blocked[seg["id"]]
    if seg.get("no_text_labels"):
        return ("figure has no text labels: no semantic label judgment; "
                "asset needs a separate structural check")
    if seg.get("label_count_mismatch"):
        return f"figure label counts differ: {seg['label_count_mismatch']}"
    if not seg["en"].strip():
        return "no English text"
    if not seg["zh"].strip():
        return "empty translation: missing Chinese text, not a pass"
    if not re.search(r"\w", seg["en"] + seg["zh"]):
        return "no word characters"
    return None


def glossary_for(chapter: dict, sid: str, en: str, zh: str) -> list[dict]:
    out = []
    for entry in chapter["glossary"]:
        ids = entry.get("segment_ids") or []
        en_term = (entry.get("en") or "").strip()
        hit = sid in ids if ids else bool(en_term) and (en_term.lower() in en.lower()
                                                       or (entry.get("zh") or "") in zh)
        if hit:
            out.append({k: entry[k] for k in ("en", "zh", "note") if k in entry})
    return out


def note_scope(note: dict) -> list[str]:
    """Notes are scoped either by a list (`segment_ids`/`ids`) or the single `id`
    field used by the supplied translation JSON."""
    scoped = note.get("segment_ids") or note.get("ids")
    if not scoped and isinstance(note.get("id"), str):
        scoped = [note["id"]]
    if isinstance(scoped, str):
        scoped = [scoped]
    return [s for s in (scoped or []) if isinstance(s, str) and s]


def notes_for(chapter: dict, ids: list[str]) -> list[dict]:
    out = []
    scope_set = set(ids)
    for note in chapter["notes"]:
        if not isinstance(note, dict):
            continue
        scoped = note_scope(note)
        if not scoped or scope_set & set(scoped):
            out.append({k: v for k, v in note.items()
                        if k in ("id", "segment_ids", "note", "kind")})
    return out


# ---------------------------------------------------------------- payload
def window(chapter: dict, tid: str) -> list[dict]:
    segs = chapter["segments"]
    index = {seg["id"]: i for i, seg in enumerate(segs)}
    if tid not in index:
        raise SystemExit(f"target {tid} is not a segment of this chapter")
    i = index[tid]
    lo, hi = max(0, i - WINDOW), min(len(segs), i + WINDOW + 1)
    blocks = []
    for j in range(lo, hi):
        seg = segs[j]
        block = {
            "id": seg["id"],
            "role": "target" if j == i else ("before" if j < i else "after"),
            "type": seg.get("type"),
            "kind": seg.get("kind") or "text",
            "en": seg["en"],
            "zh": seg["zh"],
        }
        if seg.get("labels"):
            block["labels"] = seg["labels"]
        if seg.get("no_text_labels"):
            block["no_text_labels"] = True
        blocks.append(block)
    return blocks


def build_payload(chapter: dict, tid: str, model: str) -> dict:
    blocks = window(chapter, tid)
    target = next(b for b in blocks if b["role"] == "target")
    ids = [b["id"] for b in blocks]
    instructions = INSTRUCTIONS.replace("{tid}", tid)
    if target["kind"] == "figure":
        instructions += FIGURE_INSTRUCTIONS.replace("{tid}", tid)
    return {
        "model": model,
        "provider": {"only": [MODELS[model]], "allow_fallbacks": False},
        "state": {
            "task": "chapter translation review",
            "target_id": tid,
            "blocks": blocks,
            "glossary": glossary_for(chapter, tid, target["en"], target["zh"]),
            "notes": notes_for(chapter, ids),
        },
        "questions": {
            QUESTION_ID: {
                "type": "noul",
                "instructions": instructions,
                "criteria": {k: v.replace("{tid}", tid) for k, v in CRITERIA.items()},
            }
        },
    }


def fingerprint(payload: dict) -> str:
    """sha256 of state+questions only: proves judges saw the same judgment state."""
    blob = json.dumps({"state": payload["state"], "questions": payload["questions"]},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def estimate_tokens(payload: dict) -> int:
    """Deliberately conservative chars->tokens heuristic, not a tokenizer claim."""
    state = payload["state"]
    chars = len(json.dumps(state, ensure_ascii=False)) + len(
        json.dumps(payload["questions"], ensure_ascii=False))
    return int(chars / 2.0) + 200


# ---------------------------------------------------------------- live identity
def live_identity(key: str) -> dict:
    """Exact alias/canonical map from live metadata.  A returned model string must be
    one of these exact strings; prefixes are not accepted."""
    st, raw = http("GET", f"{API}/models?output_modalities=decisions", key)
    catalog = json.loads(raw).get("data", []) if st == 200 else []
    cat_by_id = {m.get("id"): m for m in catalog}
    out = {"catalog_http_status": st, "catalog_count": len(catalog), "models": {}}
    for model, pin in MODELS.items():
        author, slug = model.split("/", 1)
        st, body = http("GET", f"{API}/models/{author}/{slug}/endpoints", key)
        if st != 200:
            out["models"][model] = {"ok": False, "http_status": st,
                                    "error": body[:300]}
            continue
        data = json.loads(body).get("data", {})
        eps = [e for e in data.get("endpoints", []) if e.get("provider_name") == pin]
        if len(eps) != 1:
            out["models"][model] = {"ok": False, "http_status": st,
                                    "provider_pin": pin,
                                    "error": f"{len(eps)} endpoints for pin {pin}"}
            continue
        ep = eps[0]
        aliases = {model, ep.get("model_id") or "", data.get("id") or "",
                   ep.get("name") or ""}
        name = ep.get("name") or ""
        if "|" in name:
            aliases.add(name.split("|", 1)[1].strip())
        cat = cat_by_id.get(model) or {}
        aliases |= {cat.get("canonical_slug") or "", cat.get("id") or ""}
        aliases = sorted(a for a in aliases if a)
        out["models"][model] = {
            "ok": True, "provider_pin": pin, "returned_aliases": aliases,
            "canonical_model_id": data.get("id") or model,
            "endpoint_name": ep.get("name"),
            "pricing": ep.get("pricing") or {},
            "context_length": ep.get("context_length"),
        }
    return out


def identity_ok(model: str, parsed: dict, ident: dict) -> tuple[bool, dict]:
    entry = ident["models"].get(model, {})
    returned_model = parsed.get("returned_model")
    provider = parsed.get("provider")
    prov = provider if isinstance(provider, str) else (provider or {}).get("name")
    model_ok = isinstance(returned_model, str) and returned_model in entry.get(
        "returned_aliases", [])
    prov_ok = prov == entry.get("provider_pin")
    return bool(model_ok and prov_ok), {
        "returned_model": returned_model,
        "returned_provider": prov,
        "expected_pin": entry.get("provider_pin"),
        "model_matched_exactly": model_ok,
        "provider_matched": prov_ok,
    }


def price_of(ident: dict, model: str) -> tuple[Decimal, Decimal] | None:
    pricing = (ident["models"].get(model) or {}).get("pricing") or {}
    try:
        prompt = Decimal(str(pricing.get("prompt")))
        completion = Decimal(str(pricing.get("completion") or "0"))
    except Exception:
        return None
    return prompt, completion


def estimated_cost(ident: dict, model: str, usage: dict, payload: dict) -> Decimal | None:
    """Pricing-based estimate for reservation only; never reported as a charge."""
    price = price_of(ident, model)
    if price is None:
        return None
    prompt, completion = price
    it = usage.get("input_tokens")
    ot = usage.get("output_tokens")
    it = int(it) if isinstance(it, (int, float)) else estimate_tokens(payload)
    ot = int(ot) if isinstance(ot, (int, float)) else 100
    return (Decimal(it) * prompt + Decimal(ot) * completion)


# ---------------------------------------------------------------- preflight
def preflight(estimate_root: Path | None) -> dict:
    key = load_key()
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoint": ENDPOINT,
        "documentation": {
            "decisions_guide": "https://openrouter.ai/docs/guides/community/multimodal-decisions",
            "decisions_api_reference": "https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-questions-and-answers-request",
            "python_sdk": "https://openrouter.ai/docs/client-sdks/python/sdks/decisions/README",
            "noul_primitives": "https://openrouter.ai/docs/guides/community/jev",
            "verified_this_session": "2026-10-23: all four fetched over HTTPS with HTTP 200; "
                                     "required request fields model/state/questions; "
                                     "provider.only and allow_fallbacks are documented "
                                     "ProviderPreferences fields",
        },
        "credential": {"present": True},
        "models": {},
        "catalog": {},
        "budget": {},
        "estimate": {},
        "limitations": [],
    }
    ident = live_identity(key)
    out["catalog"] = {"http_status": ident["catalog_http_status"],
                      "count": ident["catalog_count"],
                      "requested_present": {m: bool(ident["models"].get(m, {}).get("ok"))
                                            for m in MODELS}}
    for model, entry in ident["models"].items():
        author, slug = model.split("/", 1)
        st, _ = http("GET", f"{API}/models/{author}/{slug}/endpoints", key)
        out["models"][model] = {
            "http_status": st,
            "provider_pin": MODELS[model],
            "canonical_model_id": entry.get("canonical_model_id"),
            "endpoint_name": entry.get("endpoint_name"),
            "alias_canonical_map": entry.get("returned_aliases"),
            "pricing_per_token": entry.get("pricing"),
            "context_length": entry.get("context_length"),
            "ok": entry.get("ok"),
        }

    st, body = http("GET", f"{API}/credits", key)
    credits = json.loads(body).get("data", {}) if st == 200 else {}
    st2, body2 = http("GET", f"{API}/key", key)
    keydata = json.loads(body2).get("data", {}) if st2 == 200 else {}
    account = (Decimal(str(credits.get("total_credits", 0)))
               - Decimal(str(credits.get("total_usage", 0)))) if credits else None
    cap = keydata.get("limit_remaining")
    out["budget"] = {
        "credits_http_status": st,
        "account_remaining_usd": str(account) if account is not None else None,
        "key_cap_limit_usd": keydata.get("limit"),
        "key_cap_remaining_usd": str(cap) if cap is not None else None,
        "key_usage_usd": keydata.get("usage"),
        "effective_remaining_usd": str(min([v for v in (account,
                                        Decimal(str(cap)) if cap is not None else None)
                                        if v is not None])) if (account is not None or cap is not None) else None,
        "run_ceiling_usd": str(RUN_CEILING),
        "initial_screening_allowance_usd": str(INITIAL_ALLOWANCE),
        "recheck_allowance_usd": str(RECHECK_ALLOWANCE),
        "image_judgment_allowance_usd": str(IMAGE_ALLOWANCE),
        "note": "account balance and key cap are separate limits; effective = min; the "
                "run ceiling is the approved incremental ceiling for one whole-book pass "
                "(not per lane); initial screening may reserve at most the initial "
                "allowance, the recheck and image pools stay untouched",
    }

    if estimate_root:
        estimate_plan(out, estimate_root)
    else:
        out["limitations"].append("no --estimate-root: request count not estimated")
    out["limitations"].append(
        "catalog/credit metadata does not prove Decisions inference works for these models")
    return out


def estimate_plan(out: dict, estimate_root: Path) -> None:
    chapters = sorted(p for p in (estimate_root / "chapters").glob("*") if p.is_dir()) \
        if (estimate_root / "chapters").is_dir() else []
    if not chapters:
        out["limitations"].append("no chapters/ directories yet: only metadata recorded")
        return
    per_model_tokens: dict[str, list[int]] = {m: [] for m in MODELS}
    targets = 0
    detail: dict[str, dict] = {}
    for chdir in chapters:
        chapter = load_chapter(chdir)
        excluded = {s["id"]: exclusion_reason(s, chapter["blocked"])
                    for s in chapter["segments"]}
        excluded = {k: v for k, v in excluded.items() if v}
        judged = [s["id"] for s in chapter["segments"]
                  if exclusion_reason(s, chapter["blocked"]) is None]
        detail[chdir.name] = {"segments": len(chapter["segments"]),
                              "judged": len(judged), "excluded": excluded}
        targets += len(judged)
        for tid in judged:
            for model in MODELS:
                per_model_tokens[model].append(
                    estimate_tokens(build_payload(chapter, tid, model)))
    recheck_share = Decimal("0.35")
    total = Decimal(0)
    rows = {}
    for model, counts in per_model_tokens.items():
        prompt = Decimal(str(((out["models"].get(model) or {})
                              .get("pricing_per_token") or {}).get("prompt") or "0"))
        per_pass = sum(Decimal(c) for c in counts) * prompt
        recheck = sum(Decimal(c) for c in sorted(counts)[: int(len(counts) * recheck_share)]) * prompt
        rows[model] = {
            "calls_per_pass": len(counts),
            "tokens_per_pass": sum(counts),
            "prompt_usd_per_mtok": str(prompt * Decimal(1000000)),
            "usd_per_pass": str(round(per_pass, 6)),
            "usd_with_rechecks": str(round(per_pass + recheck, 6)),
        }
        total += per_pass + recheck
    out["estimate"] = {
        "chapters": detail,
        "targets": targets,
        "models": rows,
        "recheck_share_assumed": str(recheck_share),
        "usd_total_estimated": str(round(total, 6)),
        "token_heuristic": "len(json(state+questions))/2 + 200; conservative, not tokenizer-exact",
        "ceiling_usd": str(RUN_CEILING),
        "stage_ceiling_usd": str(INITIAL_ALLOWANCE),
    }


# ---------------------------------------------------------------- judge calls
def band(probability: Decimal) -> str:
    if probability >= BAND_SUSPECT:
        return "suspected_issue"
    if probability < BAND_RETAIN:
        return "retain"
    return "uncertain"


def parse_answer(raw: str) -> dict:
    data = json.loads(raw, parse_float=Decimal)
    answers = data.get("answers") or {}
    if set(answers) != {QUESTION_ID}:
        raise ValueError(f"expected exactly one answer {QUESTION_ID!r}, got {sorted(answers)}")
    answer = answers[QUESTION_ID]
    prob = answer.get("noul")
    if (answer.get("type") != "noul" or isinstance(prob, bool)
            or not isinstance(prob, (int, Decimal)) or not Decimal(prob).is_finite()
            or not 0 <= Decimal(prob) <= 1):
        raise ValueError("expected a finite Noul probability in [0,1]")
    usage = data.get("usage") or {}
    cost = usage.get("cost")
    if cost is not None:
        if (isinstance(cost, bool) or not isinstance(cost, (int, Decimal))
                or not Decimal(cost).is_finite() or cost < 0):
            raise ValueError("invalid usage.cost")
        cost = format(Decimal(cost), "f")
    return {
        "probability": format(Decimal(prob), "f"),
        "returned_model": data.get("model"),
        "provider": data.get("provider"),
        "response_id": data.get("id"),
        "usage": {k: (format(v, "f") if isinstance(v, Decimal) else v)
                  for k, v in usage.items()},
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cost_usd": cost,
        "raw_response": raw,
    }


def targets_for(chapter: dict, requested: list[str] | None) -> list[str]:
    ids = [s["id"] for s in chapter["segments"]
           if exclusion_reason(s, chapter["blocked"]) is None]
    if requested:
        known = {s["id"] for s in chapter["segments"]}
        unknown = [t for t in requested if t not in known]
        if unknown:
            raise SystemExit(f"unknown target ids: {unknown}")
        ids = [t for t in requested if t in ids]
    return ids


def dry_run(chapter_dir: Path, output: Path, requested: list[str] | None,
            limit: int | None) -> int:
    chapter = load_chapter(chapter_dir)
    ids = targets_for(chapter, requested)
    if limit:
        ids = ids[:limit]
    excluded = {s["id"]: exclusion_reason(s, chapter["blocked"]) for s in chapter["segments"]}
    excluded = {k: v for k, v in excluded.items() if v}
    plan = {
        "mode": "dry_run",
        "chapter": chapter["chapter"],
        "chapter_dir": str(chapter_dir),
        "endpoint": ENDPOINT,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": MODELS,
        "targets": ids,
        "target_count": len(ids),
        "excluded": excluded,
        "estimates": {"requests": len(ids) * len(MODELS)},
        "sample_requests": [],
    }
    fps: dict[str, set[str]] = {}
    for tid in ids:
        for model in MODELS:
            payload = build_payload(chapter, tid, model)
            fps.setdefault(tid, set()).add(fingerprint(payload))
            if len(plan["sample_requests"]) < 3:
                plan["sample_requests"].append({"target_id": tid, "model": model,
                                                "payload": payload})
    mismatched = {tid: sorted(f) for tid, f in fps.items() if len(f) != 1}
    plan["identity_check"] = {
        "targets_checked": len(fps),
        "targets_with_divergent_state": mismatched,
        "ok": not mismatched,
        "note": "fingerprint covers state+questions only; model/provider excluded by design",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"dry-run plan: {len(ids)} targets x {len(MODELS)} models "
          f"= {len(ids) * len(MODELS)} requests; {len(excluded)} excluded; identity_ok="
          f"{plan['identity_check']['ok']} -> {output}")
    return 0 if plan["identity_check"]["ok"] else 2


class Budget:
    """Actual returned cost plus a pricing-based reservation for unknown-cost calls."""

    def __init__(self, cap: Decimal):
        self.cap = cap
        self.actual = Decimal(0)
        self.reserved = Decimal(0)
        self.unknown_calls = 0
        self.lock = threading.Lock()

    def allow(self, next_est: Decimal | None) -> tuple[bool, str]:
        with self.lock:
            if next_est is None:
                return False, "pricing unavailable: cannot reserve budget"
            if self.actual + self.reserved + next_est > self.cap:
                return False, (f"budget stop: actual {self.actual} + reserved "
                               f"{self.reserved} + next {next_est} > cap {self.cap}")
            # reserve pre-emptively so concurrent channels cannot overshoot
            self.reserved += next_est
            return True, ""

    def settle(self, reserved_est: Decimal | None, cost: str | None,
               actual_est: Decimal | None) -> None:
        with self.lock:
            if reserved_est is not None:
                self.reserved -= reserved_est
            if cost is None:
                self.unknown_calls += 1
                if actual_est is not None:
                    self.reserved += actual_est
            else:
                self.actual += Decimal(cost)


def execute(chapter_dir: Path, output: Path, requested: list[str] | None,
            budget_usd: Decimal, limit: int | None,
            models: list[str] | None = None) -> int:
    jsonl = output.with_suffix(".records.jsonl")
    for path in (output, jsonl):
        if path.is_file() and path.stat().st_size > 0:
            print(f"refused: {path} already has content; choose a new output path "
                  f"or move it aside (paid evidence is never overwritten)")
            return 3
    chapter = load_chapter(chapter_dir)
    ids = targets_for(chapter, requested)
    if limit:
        ids = ids[:limit]
    excluded = {s["id"]: exclusion_reason(s, chapter["blocked"]) for s in chapter["segments"]}
    excluded = {k: v for k, v in excluded.items() if v}
    selected = list(models or MODELS)
    unknown_models = [m for m in selected if m not in MODELS]
    if unknown_models:
        raise SystemExit(f"unknown models requested: {unknown_models}")
    if not selected:
        raise SystemExit("no models selected")

    key = load_key()
    ident = live_identity(key)
    bad = {m: ident["models"].get(m, {}).get("error") or "unavailable" for m in selected
           if not ident["models"].get(m, {}).get("ok")}
    bad.update({m: "pricing unavailable: cannot reserve budget"
                for m in selected if m not in bad and price_of(ident, m) is None})
    if bad:
        print(json.dumps({"refused": "live identity/pin/pricing unavailable",
                          "models": bad}, ensure_ascii=False)[:1200])
        return 4

    output.parent.mkdir(parents=True, exist_ok=True)
    jsonl.touch()
    budget = Budget(budget_usd)
    write_lock = threading.Lock()
    counters = {"calls": 0, "ok": 0, "failed": 0}
    failures: list[dict] = []
    done: dict[tuple[str, str], dict] = {}
    states: dict[str, dict] = {}
    payloads: dict[tuple[str, str], dict] = {}

    for tid in ids:
        fps = set()
        for model in selected:
            payload = build_payload(chapter, tid, model)
            payloads[(tid, model)] = payload
            fps.add(fingerprint(payload))
        states[tid] = {"fingerprints": sorted(fps), "consistent": len(fps) == 1}

    def call_one(tid: str, model: str, attempt: int) -> dict:
        payload = payloads[(tid, model)]
        est = estimated_cost(ident, model, {}, payload)
        ok, why = budget.allow(est)
        record = {
            "target_id": tid,
            "requested_model": model,
            "provider_pin": MODELS[model],
            "attempt": attempt,
            "state_fingerprint": fingerprint(payload),
            "request": payload,
            "attempted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "estimated_cost_usd": str(est) if est is not None else None,
        }
        if not ok:
            # allow() reserved nothing on this path, so nothing to settle
            record.update({"http_status": None, "status": "budget_stop", "error": why})
        else:
            status, raw = http("POST", ENDPOINT, key, payload)
            record.update({"http_status": status, "raw_response": raw})
            if status == 200:
                try:
                    parsed = parse_answer(raw)
                    good, ident_detail = identity_ok(model, parsed, ident)
                    record.update(parsed)
                    record["identity"] = ident_detail
                    if not good:
                        record["status"] = "identity_mismatch"
                    else:
                        record["status"] = "ok"
                        record["routing_band"] = band(Decimal(parsed["probability"]))
                    actual_est = estimated_cost(ident, model, parsed["usage"], payload)
                    record["estimated_cost_usd"] = (str(actual_est)
                                                    if actual_est is not None else None)
                    budget.settle(est, parsed["cost_usd"], actual_est)
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    record["status"] = "unparseable"
                    record["error"] = str(exc)
                    budget.settle(est, None, est)
            else:
                record["status"] = "transport_error" if status == 0 else f"http_{status}"
                record["error"] = raw[:400]
                budget.settle(est, None, est)
        with write_lock:
            counters["calls"] += 1
            if record["status"] == "ok":
                counters["ok"] += 1
                done[(tid, model)] = record
            else:
                counters["failed"] += 1
                failures.append({"target_id": tid, "model": model, "attempt": attempt,
                                 "status": record["status"],
                                 "reason": (record.get("error") or "")[:300]})
            with jsonl.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return record

    def channel(model: str) -> None:
        for tid in ids:
            call_one(tid, model, 1)

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with ThreadPoolExecutor(max_workers=len(selected)) as pool:
        list(pool.map(channel, selected))

    # bounded re-ask, transport failures only: successful responses are never re-sent
    # and parse/identity failures are revalidated offline before any further call.
    for attempt in range(2, 2 + MAX_TRANSPORT_RETRIES):
        missing = [(tid, model) for model in selected for tid in ids
                   if (tid, model) not in done
                   and any(f["target_id"] == tid and f["model"] == model
                           and (f["status"] == "transport_error"
                                or f["status"].startswith("http_5")
                                or f["status"] == "http_429")
                           for f in failures)]
        if not missing:
            break
        with ThreadPoolExecutor(max_workers=len(selected)) as pool:
            list(pool.map(lambda pair: call_one(pair[0], pair[1], attempt), missing))

    coverage = {}
    for model in selected:
        got = sum(1 for tid in ids if (tid, model) in done)
        coverage[model] = {"expected": len(ids), "ok": got,
                           "missing": [tid for tid in ids if (tid, model) not in done]}
    expected_calls = len(ids) * len(selected)
    response_ids = [r.get("response_id") for r in done.values() if r.get("response_id")]
    # Completion is decided by the final per-cell evidence, never by how many attempts
    # failed on the way there: a 503 that the bounded transport retry resolved is audit
    # history, not a defect.  Failed attempts stay in ``failures`` and in the JSONL; the
    # state-consistency and response-id checks still gate the verdict.
    all_ok = (len(done) == expected_calls
              and len(set(response_ids)) == len(response_ids)
              and all(s["consistent"] for s in states.values()))

    routing: dict[str, list[str]] = {"suspected_issue": [], "uncertain": [], "retain": [],
                                     "pending": []}
    targets_out = []
    for tid in ids:
        recs = [done[(tid, m)] for m in selected if (tid, m) in done]
        probs = [Decimal(r["probability"]) for r in recs]
        if len(probs) < len(selected):
            route = "pending"
        elif any(p >= BAND_SUSPECT for p in probs):
            route = "suspected_issue"
        elif all(p < BAND_RETAIN for p in probs):
            route = "retain"
        else:
            route = "uncertain"
        routing[route].append(tid)
        targets_out.append({
            "id": tid,
            "kind": next(s.get("kind") for s in chapter["segments"] if s["id"] == tid),
            "route": route,
            "judges": [{"model": m,
                        "probability": next((r["probability"] for r in recs
                                             if r["requested_model"] == m), None),
                        "band": next((r.get("routing_band") for r in recs
                                      if r["requested_model"] == m), None),
                        "response_id": next((r.get("response_id") for r in recs
                                             if r["requested_model"] == m), None)} for m in selected],
        })

    result = {
        "mode": "execute",
        "chapter": chapter["chapter"],
        "chapter_dir": str(chapter_dir),
        "endpoint": ENDPOINT,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": {m: MODELS[m] for m in selected},
        "question": QUESTION_ID,
        "budget_usd": str(budget_usd),
        "identity_aliases": {m: ident["models"].get(m, {}).get("returned_aliases")
                             for m in selected},
        "cost": {
            "actual_usd": format(budget.actual, "f"),
            "cost_unknown_calls": budget.unknown_calls,
            "reserved_estimate_usd": format(budget.reserved, "f"),
            "note": "actual = sum of returned usage.cost; reserved = pricing-based estimate "
                    "for calls whose cost was not returned; estimates are not charges",
        },
        "calls": counters,
        "expected_calls": expected_calls,
        "coverage": coverage,
        "unique_response_ids": len(set(response_ids)),
        "state_consistency": states,
        "excluded": excluded,
        "routing": {"bounds": {"suspect_at_or_above": str(BAND_SUSPECT),
                               "retain_below": str(BAND_RETAIN)},
                    "counts": {k: len(v) for k, v in routing.items()},
                    "ids": routing},
        "targets": targets_out,
        "failures": failures,
        "all_checks_ok": all_ok,
        "records_jsonl": str(jsonl),
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"chapter": chapter["chapter"], "expected": expected_calls,
                      "ok": counters["ok"], "failed": counters["failed"],
                      "actual_usd": format(budget.actual, "f"),
                      "unknown_cost_calls": budget.unknown_calls,
                      "routing": result["routing"]["counts"],
                      "all_checks_ok": all_ok, "output": str(output)},
                     ensure_ascii=False))
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chapter-dir", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--target-ids", default="", help="comma separated segment ids")
    ap.add_argument("--preflight", action="store_true",
                    help="metadata/credit discovery and cost estimate; no inference")
    ap.add_argument("--estimate-root", type=Path,
                    help="root containing chapters/NN to size the request count")
    ap.add_argument("--execute", action="store_true", help="make paid Decisions calls")
    ap.add_argument("--budget-usd", default="1.0",
                    help="hard cap for this run (USD; the approved run ceiling is 4.90)")
    ap.add_argument("--limit", type=int, help="only the first N targets (preflight use)")
    ap.add_argument("--only-model", default="",
                    help="comma separated subset of the pinned models (gap fill only; "
                         "a normal pass uses all three)")
    args = ap.parse_args(argv)

    if args.preflight:
        data = preflight(args.estimate_root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print(json.dumps({m: {"pricing": v.get("pricing_per_token"),
                              "canonical": v.get("canonical_model_id"),
                              "aliases": v.get("alias_canonical_map")}
                          for m, v in data["models"].items()}, ensure_ascii=False, indent=2))
        print("budget:", json.dumps(data["budget"], ensure_ascii=False))
        if data.get("estimate"):
            print("estimate:", json.dumps({k: data["estimate"].get(k) for k in
                                           ("targets", "usd_total_estimated")},
                                          ensure_ascii=False))
            print("chapters:", json.dumps({k: {"judged": v["judged"],
                                               "excluded": list(v["excluded"])}
                                           for k, v in data["estimate"]["chapters"].items()},
                                          ensure_ascii=False))
        return 0

    if not args.chapter_dir:
        ap.error("--chapter-dir is required unless --preflight")
    requested = [t.strip() for t in args.target_ids.split(",") if t.strip()] or None
    only = [m.strip() for m in args.only_model.split(",") if m.strip()] or None
    if args.execute:
        return execute(args.chapter_dir, args.output, requested,
                       Decimal(args.budget_usd), args.limit, only)
    return dry_run(args.chapter_dir, args.output, requested, args.limit)


if __name__ == "__main__":
    sys.exit(main())
