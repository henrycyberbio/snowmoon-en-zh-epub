#!/usr/bin/env python3
"""Compact-JSON revision packets (the input handed to the revision subagents).

One packet per chapter carries exactly what the revision pass was given: the
disputed segment, +/-2 context, the scoped glossary, the protected spans and the
per-model probability signals, serialised as a compact native JSON object.  There
is no TOON codec in this path and no second encoder.

Figure targets take their English/Chinese text from the real SVG <text>/<tspan>
labels (same extractor as judge.py), never from the extractor's Chinese alt
placeholder.  Figures with no text labels are listed separately as
``structural_only`` and are not presented as text-judged items.

  python workflow/packet.py --chapter-dir work/chapters/02 \
      --judgments work/screening/chapter-02.json --output work/packets/chapter-02.json
  python workflow/packet.py --verify work/revisions-02.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import judge  # noqa: E402  (shared SVG-label extractor + exclusion rules)

REPO_ROOT = HERE.parent
PACKET_VERSION = "snowmoon-revision-packet/3-json"
MAX_ITEM_BYTES = 64 * 1024
# Hard: structure a translator must never paraphrase away (markup, ids, formulas,
# asset paths).  Soft: numbers and plain ASCII words - a real mistranslation fix may
# legitimately change them, so they warn and need a stated reason instead of failing.
HARD_PATTERNS = (
    r"</?[A-Za-z][^>]*>",                       # inline markup tags
    r"[A-Za-z0-9_]+\.(?:svg|png|jpg|html)",      # asset / file references
    r"https?://\S+",                            # URLs
    r"\d{2,}-\d{2,}",                            # chapter-segment ids
    r"\b[A-Za-z]+-[A-Za-z0-9]+\b",               # hyphenated identifiers
)
SOFT_PATTERNS = (
    r"\d+(?:[.,]\d+)*%?",                        # numbers, decimals, percentages
    r"[A-Za-z][A-Za-z0-9_.'\-]*",                 # names, terms, Latin words
)
SUSPECT = float(judge.BAND_SUSPECT)
RETAIN = float(judge.BAND_RETAIN)


# ---------------------------------------------------------------- protected spans
def spans(text: str, patterns: tuple[str, ...]) -> list[str]:
    seen: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            if match not in seen:
                seen.append(match)
    return seen


def find_protected(text: str) -> tuple[list[str], list[str]]:
    hard = spans(text, HARD_PATTERNS)
    # drop soft tokens that are only pieces of a protected span (e.g. the 'b' of '<b>')
    soft = [s for s in spans(text, SOFT_PATTERNS)
            if s not in hard and not any(s in h and s != h for h in hard)]
    return hard, soft


# ---------------------------------------------------------------- chapter / signals
def load_packet_chapter(chapter_dir: Path) -> dict:
    src = json.loads((chapter_dir / "source.json").read_text(encoding="utf-8"))
    zh = json.loads((chapter_dir / "zh.json").read_text(encoding="utf-8"))
    glossary_path = chapter_dir / "glossary.json"
    glossary = (json.loads(glossary_path.read_text(encoding="utf-8"))
                if glossary_path.is_file() else [])
    zh_by_id = {seg["id"]: seg.get("zh") or "" for seg in zh.get("segments", [])}
    figures = {f.get("segment_id"): f for f in (src.get("figures") or [])}
    segments = []
    for s in src.get("segments", []):
        item = {"id": s["id"], "type": s.get("type"),
                "en": s.get("en") or "", "zh": zh_by_id.get(s["id"], "")}
        if s.get("device") == "figure":
            fig = figures.get(s["id"]) or {}
            item["kind"] = "figure"
            item["figure"] = {"en_asset": fig.get("en"), "zh_asset": fig.get("zh")}
        elif s.get("device") == "table":
            item["kind"] = "table"
        segments.append(item)
    return {"chapter": src.get("chapter") or zh.get("chapter"),
            "segments": segments, "glossary": glossary, "notes": zh.get("notes") or []}


def route_of(scores: list[float]) -> str:
    if not scores:
        return "unjudged"
    if any(s >= SUSPECT for s in scores):
        return "suspected_issue"
    if all(s < RETAIN for s in scores):
        return "retain"
    return "uncertain"


def load_records(judgments_path: Path, judgments: dict) -> list[dict]:
    """judge.py keeps per-attempt evidence in a sibling jsonl; prefer it, fall back to
    an inline records list."""
    inline = judgments.get("records")
    if inline:
        return inline
    raw = judgments.get("records_jsonl")
    if not raw:
        return []
    path = Path(raw)
    if not path.is_absolute():
        candidate = judgments_path.parent / path.name
        path = candidate if candidate.is_file() else path
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def collect_signals(records: list[dict]) -> dict[str, list[dict]]:
    signals: dict[str, list[dict]] = {}
    for record in records:
        if "probability" not in record or record.get("status") != "ok":
            continue
        signals.setdefault(record["target_id"], []).append({
            "model": record.get("requested_model"),
            "probability": float(record["probability"]),
        })
    return signals


def build_packet(chapter_dir: Path, judgments_path: Path,
                 include_retain: bool = False) -> dict:
    chapter = load_packet_chapter(chapter_dir)
    judgments = json.loads(judgments_path.read_text(encoding="utf-8"))
    signals = collect_signals(load_records(judgments_path, judgments))
    segs = chapter["segments"]
    index = {s["id"]: i for i, s in enumerate(segs)}
    items = []
    for tid, scores in signals.items():
        route = route_of([s["probability"] for s in scores])
        if route == "retain" and not include_retain:
            continue
        if tid not in index:
            continue
        i = index[tid]
        seg = segs[i]
        before = [{"id": segs[j]["id"], "en": segs[j]["en"], "zh": segs[j]["zh"]}
                  for j in range(max(0, i - 2), i)]
        after = [{"id": segs[j]["id"], "en": segs[j]["en"], "zh": segs[j]["zh"]}
                 for j in range(i + 1, min(len(segs), i + 3))]
        glossary = [{"en": g.get("en"), "zh": g.get("zh"), "note": g.get("note") or ""}
                    for g in chapter["glossary"]
                    if tid in (g.get("segment_ids") or [])
                    or (g.get("en") and g["en"].lower() in seg["en"].lower())]
        hard, soft = find_protected(seg["zh"])
        item = {
            "id": tid,
            "route": route,
            "type": seg.get("type") or "",
            "kind": seg.get("kind") or "text",
            "en": seg["en"],
            "zh_current": seg["zh"],
            "must_keep": hard,
            "numbers_and_latin": soft,
            "judges": scores,
            "glossary": glossary,
            "before": before,
            "after": after,
        }
        if seg.get("figure"):
            item["figure"] = seg["figure"]
        items.append(item)
    return {
        "packet": PACKET_VERSION,
        "chapter": chapter["chapter"],
        "source": {"chapter_dir": str(chapter_dir), "judgments": str(judgments_path)},
        "judges_coverage": {m: {"expected": c.get("expected"), "ok": c.get("ok")}
                            for m, c in (judgments.get("coverage") or {}).items()},
        "routing_counts": (judgments.get("routing") or {}).get("counts"),
        "rules": {
            "task": "Return id + revised_chinese + concise_reason for each disputed item.",
            "scope": "Fix only substantive semantic errors; keep author/translator voice.",
            "preserve": ("Keep Chinese punctuation, inline formatting and every must_keep span. "
                         "A numeric or Latin-script change inside numbers_and_latin is allowed "
                         "only when the reason names the corrected token and the evidence."),
            "context": "before/after blocks are evidence only; do not revise them.",
            "signals": ("judge probabilities are routing signals, not ground truth; do not "
                        "edit an item just because a probability is high."),
        },
        "items": items,
    }


def load_figures(chapter_dir: Path, segments: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """Real label text per figure target + the figures that carry no text at all."""
    src = json.loads((chapter_dir / "source.json").read_text(encoding="utf-8"))
    figures = src.get("figures") or []
    labels: dict[str, dict] = {}
    structural: list[dict] = []
    for seg in src.get("segments", []):
        if seg.get("device") != "figure":
            continue
        info = judge.figure_segment(seg, chapter_dir, figures)
        if info.get("no_text_labels"):
            structural.append({
                "id": seg["id"],
                "reason": "figure has no <text>/<tspan> labels; needs the separate "
                          "structural image audit, not a text-model judgment",
                "assets": info.get("assets"),
            })
            continue
        if info.get("label_count_mismatch"):
            structural.append({
                "id": seg["id"],
                "reason": f"EN/ZH label counts differ: {info['label_count_mismatch']}; "
                          f"unresolved, not judged",
                "assets": info.get("assets"),
            })
            continue
        labels[seg["id"]] = info
    return labels, structural


def build(chapter_dir: Path, judgments_path: Path, include_retain: bool = False) -> dict:
    packet = build_packet(chapter_dir, judgments_path, include_retain)
    packet["encoding"] = {
        "kind": "compact-json",
        "note": "native JSON object, ensure_ascii=False, separators=(',',':'); "
                "state representations for decisions remain native JSON",
    }
    labels, structural = load_figures(chapter_dir, packet["items"])
    for item in packet["items"]:
        info = labels.get(item["id"])
        if not info:
            continue
        item["en"] = info["en"]
        item["zh_current"] = info["zh"]
        item["labels"] = info["labels"]
        item["kind"] = "figure_label"
        item["scope"] = "svg_label"
        item["asset"] = (info.get("assets") or {}).get("zh")
        item["zh_alt_placeholder"] = info.get("zh_alt") or ""
        item["must_keep"], item["numbers_and_latin"] = find_protected(
            " | ".join(l["zh"] for l in info["labels"]))
    packet["structural_only"] = structural
    packet["rules"]["figures"] = (
        "For scope=svg_label items return revised labels in the same order as "
        "`labels` (index, en, zh) and keep the base segment id; never return the "
        "alt placeholder as the translation. structural_only figures cannot be "
        "resolved by text models.")
    return packet


def dumps(packet: dict) -> str:
    return json.dumps(packet, ensure_ascii=False, separators=(",", ":"))


def roundtrip(document: str, packet: dict) -> dict:
    """Strict same-object round-trip plus literal preservation checks.

    Any lossy escape (``\\uXXXX``), dropped whitespace run or non-identical string
    means the packet would not carry the translator's exact source, so we refuse
    instead of shipping it.
    """
    text = document[:-1] if document.endswith("\n") else document
    back = json.loads(text)
    if back != packet:
        raise ValueError("compact JSON does not round-trip to the same object")
    if json.dumps(back, ensure_ascii=False, separators=(",", ":")) != text:
        raise ValueError("compact JSON is not byte-stable")
    if "\\u" in text:
        raise ValueError("non-ASCII was escaped; literal Chinese would be mangled")
    missing = []
    for item in packet["items"]:
        for field in ("en", "zh_current"):
            value = item.get(field) or ""
            if not value:
                continue
            # the literal must survive JSON escaping unchanged: quotes and backslashes
            # inside the source text are escaped, never normalised or re-quoted
            escaped = json.dumps(value, ensure_ascii=False)[1:-1]
            if escaped not in text:
                missing.append(f"{item['id']}.{field}")
    if missing:
        raise ValueError(f"literal text not preserved verbatim: {missing[:5]}")
    return back


def verify_revision(path: Path) -> int:
    """Validate a revision JSON against its packet: literal source, must_keep spans.

    A lost must_keep span or an unchanged "revision" is a problem; a numeric/Latin
    change is a warning that must be named in the reason.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    packet = json.loads(Path(data["packet_path"]).read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in packet["items"]}
    problems: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for rev in data.get("revisions", []):
        tid = rev.get("id")
        if not isinstance(tid, str) or tid not in by_id:
            problems.append(f"{tid}: unknown packet id")
            continue
        if tid in seen:
            problems.append(f"{tid}: duplicate revision id")
            continue
        seen.add(tid)
        new = rev.get("revised_chinese")
        if not isinstance(new, str) or not new.strip():
            problems.append(f"{tid}: empty revised_chinese")
            continue
        item = by_id[tid]
        original = item["zh_current"]
        reason = (rev.get("reason") or "").strip()
        if new == original:
            problems.append(f"{tid}: unchanged text marked as a revision")
        if not reason:
            problems.append(f"{tid}: missing reason")
        if item.get("scope") == "svg_label":
            labels = rev.get("revised_labels")
            if not isinstance(labels, list) or len(labels) != len(item.get("labels") or []):
                problems.append(f"{tid}: svg_label revision needs revised_labels "
                                f"with {len(item.get('labels') or [])} entries")
        for token in item.get("must_keep", []):
            if new.count(token) < original.count(token):
                problems.append(f"{tid}: must_keep span {token!r} lost")
        for token in item.get("numbers_and_latin", []):
            if new.count(token) < original.count(token):
                warnings.append(f"{tid}: numeric/Latin change {token!r}"
                                + (" [explained]" if token in reason
                                   else " [reason does not name the token]"))
    print(json.dumps({"checked": len(seen), "problems": problems,
                      "warnings": warnings}, ensure_ascii=False, indent=2))
    return 0 if not problems else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chapter-dir", type=Path)
    ap.add_argument("--judgments", type=Path)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--include-retain", action="store_true")
    ap.add_argument("--verify", type=Path)
    args = ap.parse_args(argv)

    if args.verify:
        return verify_revision(args.verify)
    if not (args.chapter_dir and args.judgments and args.output):
        ap.error("--chapter-dir, --judgments and --output are required")

    if args.output.is_file() and args.output.stat().st_size > 0:
        print(f"refused: {args.output} already has content (packets are not overwritten)")
        return 3
    packet = build(args.chapter_dir, args.judgments, args.include_retain)
    document = dumps(packet)
    try:
        roundtrip(document, packet)
    except ValueError as exc:
        print(json.dumps({"refused": str(exc)}, ensure_ascii=False))
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(document + "\n", encoding="utf-8")
    print(json.dumps({
        "chapter": packet["chapter"], "items": len(packet["items"]),
        "structural_only": [s["id"] for s in packet["structural_only"]],
        "bytes": len(document.encode("utf-8")),
        "roundtrip_exact": True,
        "routes": sorted({i["route"] for i in packet["items"]}),
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
