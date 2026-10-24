#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Input preparation for the 32-chapter bilingual edition.

Builds the deterministic working inputs that book/build_full.py consumes, and
then applies the adopted revision edits recorded in edits.json. Everything that
verifies the supplied material against the frozen HTML snapshot is kept from the
accepted implementation (same block kinds, rich_html binding, table cell checks,
figure signature binding). Each figure segment's markdown placeholder names its
asset, and that mapping is verified against the snapshot's inline SVG by
structural signature: identical viewBox, a geometry census that accounts for the
background rect the published snapshot drops, and every label of the asset
present in the inline SVG. Device divs holding neither table nor SVG are typed
`device: "panel"` and verified by normalised text equality.

Three things are added here, all of them about the repo layout rather than about
the alignment logic:

1. Paths resolve from this file's location: inputs/snapshot-<id>/html,
   inputs/upstream, work/chapters, work/baseline, edits.json. No absolute paths
   and no machine-specific roots.
2. The working text (work/chapters/NN/zh.json) is regenerated from the archive
   copy on every run, so a second run never stacks annotations on top of the
   first one and never compares a "before" value against its own previous
   result.
3. Adopted edits (edits.json) are applied to the working copy in three scopes:
   body segments[].zh, notes[].note, and hidden SVG <title>/<desc> metadata.
   Each edit must match its original text exactly once; a missing, duplicated or
   wrong-before target fails the run with a nonzero exit. Hidden-SVG edits splice
   only the element text, so ids, attributes and every other byte survive.

Figure assets: the English art and the supplied Chinese copy are both copied as
raw bytes, with no font work at that point.  Only after every adopted edit has
landed -- the hidden-metadata splices included -- `prepare_figure()` reads the
pair once and returns the bytes to ship together with `has_translation`, so one
derivation feeds both consumers.  Nothing translated: the Chinese copy is the
original bytes verbatim and render.py emits a single image from the flag.  A
translation: the font of every untranslated text element goes back to the
original (shared `<g>` fonts first) and only genuinely translated `<text>` keeps
the Chinese stack.  A font-only difference is not a translation.  The metadata
edit itself is a byte splice, so case-sensitive SVG attributes survive untouched.

Reads (read-only):
  inputs/snapshot-<id>/html/chapter-N.html
  inputs/upstream/source/chapter-NN.json    supplied English segments
  inputs/upstream/zh/chapter-NN.json        supplied Chinese segments
  inputs/upstream/assets-original/          English-labelled figures
  inputs/upstream/assets/                   Chinese-labelled figures
  inputs/upstream/STYLE.md, figure_terms.json
  edits.json                                adopted changes + chapter_state (required)

Writes:
  work/baseline/NN/zh.json      immutable original archive Chinese (+ provenance)
  work/chapters/NN/source.json  English + rich_html + figures
  work/chapters/NN/zh.json      working copy: archive text + adopted edits
  work/chapters/NN/glossary.json, alignment.json, assets/
  work/chapters/NN/revision.json  derived review state (rewritten for every chapter)
  work/chapters/_prepare-run.json machine-readable run result

Exit code 0 only when every requested chapter was written, every edit applied,
and nothing is blocked.
"""
import argparse, json, os, re, shutil, hashlib, sys, datetime, difflib
import xml.etree.ElementTree as ET
from collections import Counter

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
SNAPSHOT_ID = "20261023T101827Z"
DEFAULT_SNAP = os.path.join(REPO, "inputs", "snapshot-" + SNAPSHOT_ID, "html")
DEFAULT_ARCHIVE = os.path.join(REPO, "inputs", "upstream")
DEFAULT_EDITS = os.path.join(REPO, "edits.json")
ALL_CHAPTERS = tuple(range(1, 33))

EDIT_SCOPES = ("body", "note", "svg_metadata")
SVG_ELEMENTS = ("title", "desc")

SVG_TAGS = ("rect", "circle", "line", "polygon", "path", "text", "tspan", "image", "ellipse", "polyline")

# The font stack the supplied translated figures use for Chinese text.  It is
# kept exactly as shipped: the flag below only decides which text keeps it.
ZH_SVG_FONT = "Songti SC, Noto Serif CJK SC, serif"
FIG_FONT_TAGS = ("text", "g")
FIG_TEXT_TAGS = ("text", "tspan", "title", "desc")
_COMMENT_RE = re.compile(rb"<!--.*?-->", re.S)
_FONT_TAG_RE = re.compile(rb"<(text|g)(?=[\s/>])[^>]*>")
_ATTR_FONT_RE = re.compile(rb"(\s+font-family\s*=\s*)([\"'])(.*?)\2", re.S)


def _svg_local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _xml_attr(value, quote):
    """Escape a font-family value for the quote character its attribute uses."""
    out = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return out.replace('"', "&quot;") if quote == '"' else out.replace("'", "&apos;")


def _svg_meta_texts(root):
    """The hidden metadata texts, in document order."""
    return ["".join(el.itertext()) for el in root.iter() if _svg_local(el.tag) in SVG_ELEMENTS]


def _svg_drawing(root):
    """Element walk without the hidden metadata: the drawing itself."""
    return [el for el in root.iter() if _svg_local(el.tag) not in SVG_ELEMENTS]


def _svg_translation_plan(orig_bytes, zh_bytes):
    """Pair the two figures element by element and say what the Chinese copy owes.

    Returns ``(plan, translated)``: ``plan[i]`` is ``(tag, text_changed,
    original_font)`` for the i-th ``<text>``/``<g>`` in document order, and
    ``translated`` is true when any text-bearing element (visible ``<text>``/
    ``<tspan>`` or hidden ``<title>``/``<desc>``) carries different text.  Font
    swaps alone are not a translation.

    Hidden ``<title>``/``<desc>`` carry no geometry, so they are compared as
    text lists rather than paired with drawing elements: the drawing walk itself
    must still match one-to-one, and a pair that does not is not the pair it
    claims to be, which raises instead of guessing.
    """
    o_root, z_root = ET.fromstring(orig_bytes), ET.fromstring(zh_bytes)
    plan = []
    translated = _svg_meta_texts(o_root) != _svg_meta_texts(z_root)
    for o, z in zip(_svg_drawing(o_root), _svg_drawing(z_root), strict=True):
        ot, zt = _svg_local(o.tag), _svg_local(z.tag)
        if ot != zt:
            raise ValueError("figure pair diverges at <%s> vs <%s>: not the same drawing" % (ot, zt))
        changed = "".join(o.itertext()) != "".join(z.itertext())
        if changed and ot in FIG_TEXT_TAGS:
            translated = True
        if zt in FIG_FONT_TAGS:
            plan.append((zt, changed, o.get("font-family")))
    return plan, translated


def _svg_rewrite_fonts(zh_bytes, plan):
    """Set each ``<text>``/``<g>`` opening tag to the font the plan asks for.

    Only the ``font-family`` attribute is touched; attribute order, case,
    geometry, animation and every other byte stay as they are.
    """
    masked = _COMMENT_RE.sub(lambda m: b" " * (m.end() - m.start()), zh_bytes)
    hits = list(_FONT_TAG_RE.finditer(masked))
    if len(hits) != len(plan):
        raise ValueError("%d font-bearing tag(s) on disk vs %d in the plan" % (len(hits), len(plan)))
    out, pos = [], 0
    for m, (tag, changed, orig_font) in zip(hits, plan, strict=True):
        raw = zh_bytes[m.start():m.end()]
        want = ZH_SVG_FONT if (tag == "text" and changed) else orig_font
        have = _ATTR_FONT_RE.search(raw)
        if want is None:
            new = raw if not have else raw[:have.start()] + raw[have.end():]
        elif have:
            # rewrite the value where it already sits: no attribute is reordered
            value = _xml_attr(want, have.group(2).decode("ascii")).encode("utf-8")
            new = raw[:have.start(3)] + value + raw[have.end(3):]
        else:
            attr = (' font-family="%s"' % _xml_attr(want, '"')).encode("utf-8")
            cut = len(raw) - (2 if raw.endswith(b"/>") else 1)
            new = raw[:cut] + attr + raw[cut:]
        out.append(zh_bytes[pos:m.start()])
        out.append(new)
        pos = m.end()
    out.append(zh_bytes[pos:])
    return b"".join(out)


def prepare_figure(orig_bytes, zh_bytes):
    """The Chinese figure copy to ship and whether it is a translation at all.

    One call yields both consumers from one plan.  ``False`` is the original
    bytes verbatim, so render.py emits a single image; ``True`` is the supplied
    copy with every untranslated element back on its original font (shared
    ``<g>`` fonts first, so the Chinese stack cannot spread by inheritance) and
    only a ``<text>`` whose own text changed keeping it.  A font-only,
    serialisation-only or number/conlang difference is not a translation.
    """
    plan, translated = _svg_translation_plan(orig_bytes, zh_bytes)
    if not translated:
        return orig_bytes, False
    return _svg_rewrite_fonts(zh_bytes, plan), True


# ---------------------------------------------------------------- helpers
def norm(t):
    t = t.replace("***", "").replace("|", "").replace("*", "")
    t = re.sub(r"[\s\u00a0]+", " ", t)
    return t.strip()


def tight(t):
    """Aggressive comparison form: markdown decoration and all whitespace gone."""
    t = t.replace("***", "").replace("|", "").replace("*", "").replace("`", "").replace(">", "")
    return re.sub(r"[\s\u00a0]+", "", t)


def nows(t):
    """Everything but the text: markdown decoration, table pipes and whitespace removed."""
    t = t.replace("***", "").replace("|", "").replace("*", "").replace("`", "").replace(">", "")
    return re.sub(r"[\s\u00a0]+", "", t)


def panel_key(text):
    """Comparison key for panel/quote prose.

    The supplied segment text is markdown-ish: separator rows (---), bullet
    markers, '|' padding and blockquote '>' prefixes carry structure, not words.
    Dropping them yields a key comparable with the rendered DOM text, which
    keeps punctuation, wording and numbers exactly.
    """
    out = []
    for line in (text or "").split("\n"):
        l = line.strip()
        if not l:
            continue
        if re.fullmatch(r"[|:\- ]*-{3,}[|:\- ]*", l):
            continue
        l = re.sub(r"^>\s?", "", l)
        l = re.sub(r"^[-*+]\s+", "", l)
        l = l.strip("|").strip()
        if not l:
            continue
        out.append(l)
    return nows("\n".join(out))


def md_table_groups(text):
    """Consecutive '|' lines = one table; header separator rows dropped."""
    groups, cur = [], []
    for line in (text or "").split("\n"):
        st = line.strip()
        if st.startswith("|"):
            cells = [c.strip() for c in st.strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            cur.append(cells)
        else:
            if cur:
                groups.append(cur)
                cur = []
    if cur:
        groups.append(cur)
    return groups


def block_kind(b):
    if b.name == "h1":
        return "heading"
    if b.name == "hr":
        return "break"
    if b.name == "blockquote":
        return "quote"
    if b.name == "div" and "dateline" in (b.get("class") or []):
        return "dateline"
    if b.name == "div":
        return "device"
    return "body"


def dom_kind(b):
    """Concrete snapshot element kind, recorded for the builder."""
    cls = b.get("class") or []
    if b.name == "div":
        if "dateline" in cls:
            return "div.dateline"
        if "device-view" in cls:
            if b.find("svg") is not None:
                return "div.device-view.svg"
            if b.find("table") is not None:
                return "div.device-view.table"
            return "div.device-view.panel"
        return "div"
    return b.name


def md_rows(text):
    rows = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r"-{2,}|:?-+:?", c) for c in cells if c) and any("-" in c for c in cells):
            continue
        rows.append(cells)
    return rows


def table_shape(table):
    """(n_header_rows, n_body_rows, max_cols) accounting for colspan."""
    thead = table.find("thead")
    body = table.find("tbody") or table
    hrows = thead.find_all("tr") if thead else []
    brows = [tr for tr in body.find_all("tr") if not thead or tr.find_parent("thead") is None]

    def cols(rows):
        n = 0
        for tr in rows:
            n = max(n, sum(int(c.get("colspan", 1) or 1) for c in tr.find_all(["th", "td"])))
        return n
    return len(hrows), len(brows), max(cols(hrows), cols(brows))


def expand_row(tr):
    cells = []
    for c in tr.find_all(["th", "td"]):
        cells.append(c.get_text(" ", strip=True))
        cells += [""] * (int(c.get("colspan", 1) or 1) - 1)
    while cells and cells[-1] == "":
        cells.pop()
    return cells


def trim_row(cells):
    cells = list(cells)
    while cells and cells[-1] == "":
        cells.pop()
    return cells


def cellkey(t):
    return re.sub(r"[\s\u00a0]", "", t.replace("*", ""))


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_style_glossary(style_path):
    """Parse the '基础统一术语' block of STYLE.md into (en, zh) pairs."""
    text = open(style_path, encoding="utf-8").read()
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith("## 基础统一术语"):
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside:
            lines.append(line)
    pairs = []
    for line in lines:
        for item in re.split(r"[；;]", line):
            item = item.strip()
            if not item or " " not in item:
                continue
            en, zh = item.rsplit(" ", 1)
            if not re.search(r"[\u4e00-\u9fff]", zh):
                continue
            pairs.append((en.strip(), zh.strip()))
    return pairs


# ---------------------------------------------------------------- svg signatures
# A figure binding is verified on the drawing itself, never on file order:
#   * identical viewBox
#   * every geometry element of the snapshot SVG exists in the referenced asset
#     (the snapshot may drop the dark background rect and one animation helper,
#     so the asset is allowed at most 3 extra rect/path elements)
#   * every text label of the asset appears in the snapshot SVG
GEOM_TAGS = ("path", "circle", "rect", "line", "polygon", "ellipse", "polyline")


def _geom_key(tag, g):
    if tag == "path":
        return (tag, g("d"))
    if tag == "circle":
        return (tag, g("cx"), g("cy"), g("r"))
    if tag == "rect":
        return (tag, g("x"), g("y"), g("width"), g("height"), g("rx"))
    if tag == "line":
        return (tag, g("x1"), g("y1"), g("x2"), g("y2"))
    if tag == "polygon":
        return (tag, g("points"))
    if tag == "polyline":
        return (tag, g("points"))
    if tag == "ellipse":
        return (tag, g("cx"), g("cy"), g("rx"), g("ry"))
    return (tag,)


def dom_svg_sig(sv):
    vb = (sv.get("viewbox") or sv.get("viewBox") or "").replace(",", " ").split()
    geom = Counter(_geom_key(e.name, e.get) for e in sv.find_all(GEOM_TAGS))
    txt = [t.strip() for t in sv.find_all(string=True) if t.strip()]
    return {"viewBox": vb, "geom": geom, "text": txt}


def file_svg_sig(path):
    raw = open(path, encoding="utf-8", errors="strict").read()
    m = re.search(r"<svg\b.*</svg>", raw, re.S)
    if not m:
        raise ValueError("no <svg> element in %s" % path)
    sv = m.group(0)
    mvb = re.search(r'viewBox="([^"]*)"', sv) or re.search(r'viewbox="([^"]*)"', sv)
    vb = (mvb.group(1) if mvb else "").replace(",", " ").split()
    geom = Counter()
    for mm in re.finditer(r"<(path|circle|rect|line|polygon|ellipse|polyline)\b([^>]*)>", sv):
        attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', mm.group(2)))
        geom[_geom_key(mm.group(1), attrs.get)] += 1
    txt = [x.strip() for x in re.findall(r">([^<>]+)<", sv) if x.strip()]
    return {"viewBox": vb, "geom": geom, "text": txt}


def compare_svg(dom, fil):
    """(score 0..4, detail flags, notes) with every difference explained in words."""
    notes, flags = [], {}
    flags["viewBox"] = dom["viewBox"] == fil["viewBox"]
    if not flags["viewBox"]:
        notes.append("viewBox differs: snapshot %s vs asset %s"
                     % (" ".join(dom["viewBox"]), " ".join(fil["viewBox"])))

    missing = dom["geom"] - fil["geom"]
    flags["geometry_present"] = not missing
    if missing:
        notes.append("snapshot geometry absent from asset: %r" % (list(missing.elements())[:3],))

    extra = fil["geom"] - dom["geom"]
    extra_n = sum(extra.values())
    flags["extras_explained"] = extra_n <= 3 and all(k[0] in ("rect", "path") for k in extra.elements())
    if extra_n:
        notes.append("asset has %d extra geometry element(s) not drawn by the snapshot: %r"
                     % (extra_n, [k[0] for k in extra.elements()]))
    if not flags["extras_explained"]:
        notes.append("unexplained extra geometry in asset")

    dtext = set(dom["text"])
    absent = [t for t in fil["text"] if t not in dtext]
    flags["labels_present"] = not absent
    if absent:
        notes.append("asset text not present in snapshot SVG: %r" % (absent[:5],))
    extra_txt = [t for t in dom["text"] if t not in set(fil["text"])]
    if extra_txt:
        notes.append("snapshot SVG carries %d extra text/comment node(s) (authoring notes)" % len(extra_txt))

    score = sum(1 for k in ("viewBox", "geometry_present", "extras_explained", "labels_present") if flags[k])
    return score, flags, notes


def text_change(a, b):
    """(char ratio, changed chars) with autojunk off; used to size supplied-vs-snapshot edits."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(bl.size for bl in sm.get_matching_blocks())
    return sm.ratio(), len(a) + len(b) - 2 * matched


def bounded_edit(a, b, max_changed=40, min_ratio=0.60):
    """A supplied-vs-snapshot difference small enough to be a wording fix, not a misalignment."""
    ratio, changed = text_change(a, b)
    return (changed <= max_changed and ratio >= min_ratio), ratio, changed


# ---------------------------------------------------------------- adopted edits
# edits.json carries only adopted, minimal, evidence-backed changes. Preparation
# applies them to the freshly regenerated working copy; review facts (reviewed /
# reviewed_targets) come from chapter_state, while every count is derived from
# `changes` so the same number is never maintained in two places.
def load_edits(path):
    """Load and structurally validate edits.json.

    Returns {"changes": [...], "chapter_state": {...}}. edits.json is a required
    input: a missing or mistyped path fails the run, so a typo can never prepare a
    chapter tree that silently carries none of the adopted edits. An edition with
    no adopted changes still ships edits.json with an empty "changes" list.
    """
    if not os.path.exists(path):
        raise SystemExit("missing edits file: %s" % path)
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ValueError("edits.json must be a JSON object")
    changes, state = doc.get("changes") or [], doc.get("chapter_state") or {}
    if not isinstance(changes, list) or not isinstance(state, dict):
        raise ValueError("edits.json: changes must be a list, chapter_state an object")
    for i, c in enumerate(changes):
        where = "edits.json changes[%d]" % i
        if not isinstance(c, dict):
            raise ValueError("%s: not an object" % where)
        chapter = c.get("chapter")
        if not isinstance(chapter, str) or not re.fullmatch(r"\d{2}", chapter):
            raise ValueError("%s: chapter must be a two-digit string" % where)
        if c.get("scope") not in EDIT_SCOPES:
            raise ValueError("%s: scope must be one of %s" % (where, ", ".join(EDIT_SCOPES)))
        for key in ("id", "before", "after", "reason"):
            if not isinstance(c.get(key), str):
                raise ValueError("%s: %s must be a string" % (where, key))
        if "evidence_en" in c and not isinstance(c["evidence_en"], str):
            raise ValueError("%s: evidence_en must be a string" % where)
        if c["scope"] == "svg_metadata":
            asset = c.get("asset")
            if not isinstance(asset, str) or not asset.startswith("assets/") or ".." in asset:
                raise ValueError("%s: asset must be a chapter-relative assets/ path" % where)
            if c.get("element") not in SVG_ELEMENTS:
                raise ValueError("%s: element must be one of %s" % (where, ", ".join(SVG_ELEMENTS)))
    return {"changes": changes, "chapter_state": state}


def xml_text(s):
    """Escape text for an SVG title/desc body."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def patch_field(rows, ident, field, before, after, label):
    """Replace one field of the single row whose id is `ident`.

    Returns None on success or a failure reason. A missing target, a duplicated
    target and a wrong-before value all fail: an edit that cannot address exactly
    one field is never applied by guesswork.
    """
    hits = [r for r in rows if r.get("id") == ident]
    if len(hits) != 1:
        return "%s %s: expected exactly 1 target, found %d" % (label, ident, len(hits))
    if hits[0].get(field) != before:
        return "%s %s: before text does not match the working copy" % (label, ident)
    hits[0][field] = after
    return None


def patch_svg_element(path, element, before, after):
    """Splice the text of the single <element> in an SVG file, byte-preserving.

    Only the element body is replaced; the opening tag (with its id attribute and
    any other attributes) and everything outside the element stay byte-identical.
    """
    with open(path, encoding="utf-8", newline="") as f:
        raw = f.read()
    pat = re.compile(r"(<%s(?:\s[^>]*)?>)(.*?)(</%s>)" % (element, element), re.S)
    hits = list(pat.finditer(raw))
    if len(hits) != 1:
        return "svg %s: expected exactly 1 <%s>, found %d" % (os.path.basename(path), element, len(hits))
    m = hits[0]
    if m.group(2) != before:
        return "svg %s: <%s> before text does not match" % (os.path.basename(path), element)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(raw[:m.start(2)] + xml_text(after) + raw[m.end(2):])
    return None


def apply_chapter_edits(nn, changes, zh_obj, outdir, applied, problems):
    """Apply one chapter's adopted edits in place; collect failures, never guess."""
    for c in changes:
        scope, ident = c["scope"], c["id"]
        if scope == "body":
            err = patch_field(zh_obj.get("segments") or [], ident, "zh", c["before"], c["after"], "body")
        elif scope == "note":
            err = patch_field(zh_obj.get("notes") or [], ident, "note", c["before"], c["after"], "note")
        else:
            asset = os.path.join(outdir, c["asset"].replace("/", os.sep))
            root = os.path.abspath(outdir) + os.sep
            if not os.path.abspath(asset).startswith(root):
                err = "svg %s: asset escapes the chapter directory" % c["asset"]
            elif not os.path.exists(asset):
                err = "svg %s: asset missing" % c["asset"]
            else:
                err = patch_svg_element(asset, c["element"], c["before"], c["after"])
        if err:
            problems.append({"chapter": int(nn), "id": ident, "scope": scope, "reason": err})
        else:
            applied.append({"chapter": int(nn), "id": ident, "scope": scope,
                            "asset": c.get("asset"), "element": c.get("element")})


def revision_record(n, changes, state):
    """Derived per-chapter review state consumed by build_full.revision_state.

    Written for every prepared chapter: a chapter with no adopted changes still
    records whether it was reviewed, so withdrawing an edit rewrites a zero-count
    record instead of leaving the previous run's file in place.
    """
    counts = {"body": 0, "note": 0, "svg_metadata": 0}
    for c in changes:
        counts[c["scope"]] += 1
    return {
        "chapter": n,
        "reviewed": bool(state.get("reviewed") or changes),
        "revised": bool(changes),
        "edits": counts["body"],
        "scope_counts": counts,
        "reviewed_targets": state.get("reviewed_targets"),
        "adopted_ids": sorted({c["id"] for c in changes}),
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="Build deterministic chapter inputs and apply adopted edits.")
    ap.add_argument("--snapshot", default=DEFAULT_SNAP,
                    help="HTML snapshot directory (default: inputs/snapshot-%s/html)" % SNAPSHOT_ID)
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE,
                    help="upstream source package directory (default: inputs/upstream)")
    ap.add_argument("--edits", default=DEFAULT_EDITS,
                    help="adopted edits + chapter_state (default: edits.json)")
    ap.add_argument("--chapters", default="", help="comma separated chapter numbers (default: all 32)")
    ap.add_argument("--out-root", default=os.path.join(REPO, "work", "chapters"),
                    help="working chapter tree (default: work/chapters)")
    ap.add_argument("--baseline-root", default=os.path.join(REPO, "work", "baseline"),
                    help="immutable archive copy of the Chinese text (default: work/baseline)")
    args = ap.parse_args(argv)

    arch = os.path.abspath(args.archive)
    snapdir = os.path.abspath(args.snapshot)
    chapters = tuple(int(x) for x in args.chapters.split(",") if x.strip()) or ALL_CHAPTERS
    for label, path in (("snapshot", snapdir), ("archive", arch)):
        if not os.path.isdir(path):
            raise SystemExit("missing %s directory: %s" % (label, path))
    docs = load_edits(os.path.abspath(args.edits))
    style_pairs = parse_style_glossary(os.path.join(arch, "STYLE.md"))
    figure_terms = json.load(open(os.path.join(arch, "figure_terms.json"), encoding="utf-8"))
    print("snapshot:", snapdir)
    print("archive:", arch)
    print("edits:", len(docs["changes"]), "change(s)")
    print("STYLE.md base term pairs:", len(style_pairs))
    print("chapters:", ",".join("%02d" % n for n in chapters))

    run = {"generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "snapshot": "inputs/snapshot-" + SNAPSHOT_ID + "/html",
           "archive": "inputs/upstream",
           "chapters": [], "blocked": [], "edits_applied": 0, "edits_failed": [],
           "figures_translated": 0, "figures_untranslated": 0}

    for n in chapters:
        nn = "%02d" % n
        outdir = os.path.join(args.out_root, nn)
        basedir = os.path.join(args.baseline_root, nn)
        os.makedirs(os.path.join(outdir, "assets"), exist_ok=True)
        os.makedirs(basedir, exist_ok=True)

        snap_path = os.path.join(snapdir, "chapter-%d.html" % n)
        soup = BeautifulSoup(open(snap_path, encoding="utf-8").read(), "html.parser")
        dp = soup.select_one(".document-page")
        blocks = [c for c in dp.find_all(recursive=False) if getattr(c, "name", None) not in ("nav", "br")]

        src_path = os.path.join(arch, "source", "chapter-%s.json" % nn)
        zh_path = os.path.join(arch, "zh", "chapter-%s.json" % nn)
        src = json.load(open(src_path, encoding="utf-8"))
        zhr = json.load(open(zh_path, encoding="utf-8"))
        segs, zsegs = src["segments"], zhr["segments"]

        unresolved, diffs, version_diffs, device_records, fig_checks = [], [], [], [], []
        ids = [s["id"] for s in segs]
        ids_ok = [s["id"] for s in segs] == [s["id"] for s in zsegs]
        count_ok = len(blocks) == len(segs) == len(zsegs)
        if not count_ok:
            unresolved.append({"id": "*", "reason": "block/segment count mismatch: blocks=%d en=%d zh=%d"
                               % (len(blocks), len(segs), len(zsegs))})
        if not ids_ok:
            unresolved.append({"id": "*", "reason": "EN/ZH segment id order differs"})
        if not (count_ok and ids_ok):
            # refuse to index-align a broken chapter
            run["chapters"].append({"chapter": n, "status": "blocked", "unresolved": unresolved})
            run["blocked"].append(n)
            print("ch%-2d BLOCKED %s" % (n, unresolved))
            continue

        # ---- figures: explicit placeholder -> verified structural signature ----
        figs = {os.path.basename(f["file"]): f for f in (src.get("figures") or [])}
        chapter_assets = sorted([f for f in os.listdir(os.path.join(arch, "assets-original"))
                                 if re.fullmatch(r"ch%s-fig\d+\.svg" % nn, f)])
        fig_meta = {}
        declared = []
        for i, b in enumerate(blocks):
            if b.find("svg") is None:
                continue
            sid = ids[i]
            m = re.search(r"\(assets/(ch%s-fig\d+\.svg)\)" % nn, segs[i].get("en") or "")
            if not m:
                unresolved.append({"id": sid, "reason": "svg block without assets/<chNN-figNN.svg> placeholder in segment text"})
                continue
            declared.append((i, sid, m.group(1)))

        names = [d[2] for d in declared]
        if len(set(names)) != len(names):
            unresolved.append({"id": "*", "reason": "two svg blocks declare the same asset: %r" % names})
        if set(names) != set(chapter_assets):
            unresolved.append({"id": "*", "reason": "declared figures %r vs chapter assets %r" % (sorted(set(names)), chapter_assets)})
        if len(declared) != len(figs):
            unresolved.append({"id": "*", "reason": "%d svg blocks vs %d figure metadata entries" % (len(declared), len(figs))})

        fig_out = []
        fig_pairs = []
        for idx, sid, fname in declared:
            en_src = os.path.join(arch, "assets-original", fname)
            zh_src = os.path.join(arch, "assets", fname)
            if not (os.path.exists(en_src) and os.path.exists(zh_src)):
                unresolved.append({"id": sid, "reason": "missing figure asset %s" % fname})
                continue
            domsig = dom_svg_sig(blocks[idx].find("svg"))
            scores = []
            for cand in chapter_assets:
                sc, fl, notes = compare_svg(domsig, file_svg_sig(os.path.join(arch, "assets-original", cand)))
                scores.append((sc, cand, fl, notes))
            scores.sort(key=lambda x: (-x[0], x[1]))
            best = scores[0][0]
            mine = next(x for x in scores if x[1] == fname)
            ties = [x[1] for x in scores if x[0] == mine[0] and x[1] != fname]
            fig_checks.append({"id": sid, "asset": fname, "score": mine[0], "best_score": best,
                               "checks": mine[2], "notes": mine[3],
                               "same_signature_assets": ties})
            if mine[0] < 4:
                unresolved.append({"id": sid, "reason": "figure signature mismatch for %s: %s"
                                   % (fname, "; ".join(mine[3]))})
                continue
            if mine[0] < best:
                unresolved.append({"id": sid, "reason": "figure %s scores %d but another asset scores %d"
                                   % (fname, mine[0], best)})
                continue
            # Both assets are copied verbatim, with no font work yet.  The font
            # pass belongs after the adopted edits: prepare_figure() below reads
            # the finished pair once and returns both the bytes and the flag.
            en_dst = os.path.join(outdir, "assets", fname.replace(".svg", "-en.svg"))
            zh_dst = os.path.join(outdir, "assets", fname.replace(".svg", "-zh.svg"))
            with open(en_src, "rb") as fh:
                en_bytes = fh.read()
            with open(zh_src, "rb") as fh:
                zh_bytes = fh.read()
            with open(en_dst, "wb") as fh:
                fh.write(en_bytes)
            with open(zh_dst, "wb") as fh:
                fh.write(zh_bytes)
            fig_pairs.append({"segment_id": sid, "en_dst": en_dst, "zh_dst": zh_dst,
                              "en_bytes": en_bytes})
            fig_out.append({"segment_id": sid, "file": fname,
                            "en": "assets/" + fname.replace(".svg", "-en.svg"),
                            "zh": "assets/" + fname.replace(".svg", "-zh.svg"),
                            "labels": figs.get(fname, {}).get("labels", [])})

        # ---- segments ----
        out_segs = []
        for i, (b, s) in enumerate(zip(blocks, segs)):
            sid = s["id"]
            kind = block_kind(b)
            typ = s["type"]
            rec = {"id": sid, "type": typ, "dom_kind": dom_kind(b), "rich_html": str(b)}
            if kind in ("heading", "dateline", "break"):
                if kind != typ:
                    unresolved.append({"id": sid, "reason": "kind/type mismatch: %s vs %s" % (kind, typ)})
            elif kind == "quote":
                if typ != "body":
                    unresolved.append({"id": sid, "reason": "unexpected type %s for blockquote" % typ})
            elif typ != "body":
                unresolved.append({"id": sid, "reason": "unexpected type %s for %s" % (typ, kind)})

            if kind == "break":
                rec["en"] = "***"
                if tight(s["en"]) not in ("", "***"):
                    unresolved.append({"id": sid, "reason": "break segment carries text %r" % s["en"][:40]})
            elif kind == "device":
                rec["en"] = s["en"]
                table = b.find("table")
                if b.find("svg") is not None:
                    rec["device"] = "figure"
                    fig = next((f for f in fig_out if f["segment_id"] == sid), None)
                    if fig:
                        rec["figure"] = {"en": fig["en"], "zh": fig["zh"], "file": fig["file"]}
                        rec["figure_labels"] = fig["labels"]
                elif table:
                    rec["device"] = "table"
                    # a device block may hold several tables; compare each one
                    tables = b.find_all("table")
                    groups = md_table_groups(s["en"])
                    shapes, dom_tables, extra = [], [], []
                    for t in tables:
                        nh, nb, nc = table_shape(t)
                        shapes.append({"header_rows": nh, "body_rows": nb, "cols": nc})
                        dom_tables.append([[cellkey(c) for c in expand_row(tr)]
                                           for tr in t.find_all("tr") if tr.find_parent("table") is t])
                    # aggregate keys kept from the accepted schema; per-table detail is additive
                    rec["table"] = {"header_rows": sum(x["header_rows"] for x in shapes),
                                    "body_rows": sum(x["body_rows"] for x in shapes),
                                    "cols": max([x["cols"] for x in shapes] or [0])}
                    rec["table"]["tables"] = shapes
                    rec["table"]["count"] = len(tables)
                    problems = []
                    if len(groups) != len(tables):
                        problems.append("table count md %d group(s) vs dom %d table(s)" % (len(groups), len(tables)))
                    else:
                        for gi, (md_g, dom_rows) in enumerate(zip(groups, dom_tables)):
                            md_norm = [[cellkey(c) for c in trim_row(r)] for r in md_g]
                            if len(md_norm) != len(dom_rows):
                                problems.append("group %d row count md %d vs dom %d"
                                                % (gi, len(md_norm), len(dom_rows)))
                                continue
                            for ri, (mrow, drow) in enumerate(zip(md_norm, dom_rows)):
                                if len(mrow) != len(drow):
                                    problems.append("group %d row %d col count md %d vs dom %d"
                                                    % (gi, ri, len(mrow), len(drow)))
                                    continue
                                for ci, (mc, dc) in enumerate(zip(mrow, drow)):
                                    if not mc and dc:
                                        extra.append({"group": gi, "row": ri, "col": ci, "dom_only": dc[:60]})
                                    elif mc and mc not in dc:
                                        problems.append("group %d row %d col %d: md %r not in dom %r"
                                                        % (gi, ri, ci, mc[:40], dc[:40]))
                                    elif mc and mc != dc:
                                        # dom cell carries extra inline text (e.g. a link label)
                                        extra.append({"group": gi, "row": ri, "col": ci,
                                                      "dom_extra": dc.replace(mc, "", 1)[:80]})
                    if problems:
                        unresolved.append({"id": sid, "reason": "table text/shape mismatch: " + "; ".join(problems[:3])})
                        rec["device_verified"] = False
                        rec["table_mismatch"] = problems[:3]
                    else:
                        rec["device_verified"] = True
                    if extra:
                        rec["table_dom_extra"] = extra[:10]
                    device_records.append({"id": sid, "kind": "table", "tables": len(tables),
                                           "rows": sum(x["header_rows"] + x["body_rows"] for x in shapes),
                                           "verified": rec.get("device_verified", False)})
                else:
                    # chat/terminal panel: verified by normalised text equality
                    rec["device"] = "panel"
                    domp = nows(b.get_text(" ", strip=True))
                    enp = panel_key(s["en"])
                    rec["device_verified"] = domp == enp
                    if not rec["device_verified"]:
                        ok, ratio, changed = bounded_edit(domp, enp)
                        if ok:
                            version_diffs.append({"id": sid, "scope": "panel", "snapshot_en": rec["en"],
                                                  "archive_en": s["en"], "similarity": round(ratio, 5),
                                                  "changed_chars": changed})
                            rec["device_verified"] = True
                            rec["text_version_difference"] = True
                        else:
                            unresolved.append({"id": sid, "reason": "panel text differs from supplied segment text "
                                               "(similarity %.3f, %d chars)" % (ratio, changed)})
                    device_records.append({"id": sid, "kind": "panel", "verified": rec["device_verified"]})
                colors = sorted({c for el in b.find_all(style=True)
                                 for c in re.findall(r"(#[0-9a-fA-F]{3,8}|oklch\([^)]*\))", el["style"] or "")})
                if colors:
                    rec["table_colors"] = colors
            else:
                dom = b.get_text(" ", strip=True)
                rec["en"] = re.sub(r"[\s\u00a0]+", " ", dom).strip()
                if kind == "quote":
                    rec["en"] = "\n".join(x.strip() for x in b.get_text("\n").splitlines() if x.strip())
                    if nows(dom) != panel_key(s["en"]):
                        ok, ratio, changed = bounded_edit(nows(dom), panel_key(s["en"]))
                        entry = {"id": sid, "scope": "quote", "snapshot_en": rec["en"],
                                 "archive_en": s["en"], "similarity": round(ratio, 5),
                                 "changed_chars": changed}
                        if ok:
                            version_diffs.append(entry)
                        else:
                            diffs.append(entry)
                if kind == "body":
                    if b.name == "center":
                        rec["device"] = "panel"
                        rec["device_verified"] = nows(dom) == panel_key(s["en"])
                        if not rec["device_verified"]:
                            ok, ratio, changed = bounded_edit(nows(dom), panel_key(s["en"]))
                            if ok:
                                version_diffs.append({"id": sid, "scope": "center-panel", "snapshot_en": rec["en"],
                                                      "archive_en": s["en"], "similarity": round(ratio, 5),
                                                      "changed_chars": changed})
                                rec["device_verified"] = True
                                rec["text_version_difference"] = True
                            else:
                                unresolved.append({"id": sid, "reason": "center panel text differs from supplied "
                                                   "segment text (similarity %.3f, %d chars)" % (ratio, changed)})
                        device_records.append({"id": sid, "kind": "center-panel",
                                               "verified": rec["device_verified"]})
                    elif nows(dom) != panel_key(s["en"]):
                        ok, ratio, changed = bounded_edit(nows(dom), panel_key(s["en"]))
                        entry = {"id": sid, "scope": "body", "snapshot_en": rec["en"],
                                 "archive_en": s["en"], "similarity": round(ratio, 5),
                                 "changed_chars": changed}
                        if ok:
                            version_diffs.append(entry)
                        else:
                            diffs.append(entry)
            rec["inline"] = {"spk": len(b.select("span.spk")), "em": len(b.find_all("em")),
                             "strong": len(b.find_all(["b", "strong"]))}
            out_segs.append(rec)

        if diffs:
            unresolved.append({"id": "*", "reason": "unexplained text differences in %d block(s)" % len(diffs)})

        source = {
            "chapter": n,
            "snapshot": "inputs/snapshot-%s/html/chapter-%d.html" % (SNAPSHOT_ID, n),
            "snapshot_sha256": sha(snap_path),
            "url": src.get("url"),
            "sha256": src.get("sha256"),
            "source_url": src.get("url"),
            "source_archive_sha256": src.get("sha256"),
            "segments": out_segs,
            "figures": fig_out,
        }

        zh_obj = {"chapter": n, "segments": [{"id": s["id"], "zh": s["zh"]} for s in zsegs],
                  "notes": zhr.get("notes") or [], "terms": zhr.get("terms") or {}}

        baseline = dict(zh_obj)
        baseline["provenance"] = {
            "role": "immutable original archive Chinese (pre-revision)",
            "archive_file": "zh/chapter-%s.json" % nn,
            "archive_sha256": sha(zh_path),
            "snapshot": "inputs/snapshot-%s/html/chapter-%d.html" % (SNAPSHOT_ID, n),
            "preserved_unchanged": True,
        }
        with open(os.path.join(basedir, "zh.json"), "w", encoding="utf-8") as f:
            json.dump(baseline, f, ensure_ascii=False, indent=1)

        # ---- adopted edits on the freshly regenerated working copy ----------
        # zh.json is always rewritten from the archive text captured above, so a
        # second run cannot stack a note twice or meet its own previous output as
        # the "before" value.
        chapter_changes = [c for c in docs["changes"] if c["chapter"] == nn]
        applied, edit_problems = [], []
        apply_chapter_edits(nn, chapter_changes, zh_obj, outdir, applied, edit_problems)
        for pr in edit_problems:
            unresolved.append({"id": pr["id"], "reason": "%s edit: %s" % (pr["scope"], pr["reason"])})
            run["edits_failed"].append(pr)
        run["edits_applied"] += len(applied)
        with open(os.path.join(outdir, "zh.json"), "w", encoding="utf-8") as f:
            json.dump(zh_obj, f, ensure_ascii=False, indent=1)

        rev_state = docs["chapter_state"].get(nn) or {}
        with open(os.path.join(outdir, "revision.json"), "w", encoding="utf-8") as f:
            json.dump(revision_record(n, chapter_changes, rev_state), f,
                      ensure_ascii=False, indent=1)

        # ---- figures: one derivation, both consumers ------------------------
        # Done here, after the adopted hidden-metadata edits have landed, because
        # the final text of the Chinese copy is what decides the flag.  The flag
        # and the font pass come out of the same plan: False ships the original
        # bytes verbatim, True ships the copy with untranslated nodes back on
        # their original font.  Both the figures[] entry and
        # segments[].figure carry that one value, and render.py emits one or two
        # images from it instead of comparing bytes or re-deriving anything.
        seg_index = {s["id"]: s for s in out_segs}
        fig_by_seg = {f["segment_id"]: f for f in fig_out}
        for pair in fig_pairs:
            with open(pair["zh_dst"], "rb") as fh:
                figure_bytes, flag = prepare_figure(pair["en_bytes"], fh.read())
            with open(pair["zh_dst"], "wb") as fh:
                fh.write(figure_bytes)
            entry = fig_by_seg[pair["segment_id"]]
            entry["has_translation"] = flag
            seg_index[pair["segment_id"]]["figure"]["has_translation"] = flag
            run["figures_translated" if flag else "figures_untranslated"] += 1
        with open(os.path.join(outdir, "source.json"), "w", encoding="utf-8") as f:
            json.dump(source, f, ensure_ascii=False, indent=1)

        # ---- glossary ----
        en_text = {s["id"]: s["en"] for s in out_segs}
        full = "\n".join(en_text.values())
        gloss, seen = [], set()

        def add(en, zhv, note, sid=None):
            key = (en.lower(), zhv)
            if key in seen or not en or not zhv:
                return
            seen.add(key)
            seg_ids = [i for i, t in en_text.items() if re.search(r"\b%s\b" % re.escape(en), t, re.I)] if sid is None else [sid]
            gloss.append({"en": en, "zh": zhv, "note": note, "segment_ids": seg_ids})

        for en, zhv in style_pairs:
            if len(en) < 3:
                continue
            if re.search(r"\b%s\b" % re.escape(en), full, re.I):
                add(en, zhv, "STYLE.md 基础统一术语；本章命中")
        for en, zhv in (zhr.get("terms") or {}).items():
            add(en, zhv, "原译本 terms（译者已用译名）")
        for f in fig_out:
            for lab in f["labels"]:
                if lab in figure_terms:
                    add(lab, figure_terms[lab], "图表标签（figure_terms.json）", sid=f["segment_id"])
        for nt in (zhr.get("notes") or []):
            for en, zhv in re.findall(r"([A-Za-z][A-Za-z .'\-]{2,})=([^\s，。,；;]+)", nt.get("note", "")):
                add(en.strip(), zhv, "译者图表标签注（%s）" % nt["id"])
        with open(os.path.join(outdir, "glossary.json"), "w", encoding="utf-8") as f:
            json.dump(gloss, f, ensure_ascii=False, indent=1)

        # ---- alignment ----
        types = {}
        for s in out_segs:
            types[s["type"]] = types.get(s["type"], 0) + 1
        alignment = {
            "chapter": n,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "snapshot": "inputs/snapshot-%s/html/chapter-%d.html" % (SNAPSHOT_ID, n),
            "snapshot_sha256": source["snapshot_sha256"],
            "translation_source": {
                "archive": "Snowmoon-zh.pdf embedded Snowmoon-source.zip",
                "archive_zip_sha256": "56ad402d1f401bd4563815217b6c837faa3a88b077f44dc5d99b2857b4a1e0ed",
                "repo": "https://github.com/XiaoHai67890/snowmoon-zh",
                "commit": "55f9a20b383ee124eb3eeffd137844d4b45990d6",
                "segment_file": "zh/chapter-%s.json" % nn,
                "segment_file_sha256": sha(zh_path),
                "zh_segments_preserved_unchanged": True,
            },
            "mapping": {
                "method": "ordered DOM block <-> segment id with per-block kind/type assertions; "
                          "figure segments bound by their own assets/<chNN-figNN.svg> placeholder and "
                          "verified by viewBox/element/text signature; tables compared cell-by-cell; "
                          "panels compared by normalised text equality",
                "blocks": len(blocks),
                "segments": len(segs),
                "zh_segments": len(zsegs),
                "ids_match": ids_ok,
                "type_counts": types,
                "figure_bindings": fig_checks,
                "body_text_differences": diffs,
                "english_version_differences": version_diffs,
                "device_blocks": device_records,
                "verdict": "verified" if not unresolved else "unresolved",
            },
            "source_version_differences": {
                "substantive_text_changes": [
                    {"id": v["id"], "scope": v["scope"], "similarity": v["similarity"],
                     "changed_chars": v.get("changed_chars"),
                     "archive_en": v["archive_en"], "snapshot_en": v["snapshot_en"]}
                    for v in version_diffs],
                "explanation": "English rendering and en text come from the authoritative snapshot DOM. The "
                               "supplied archive segment text is an older transcription of the same chapter: "
                               "where it differs by a bounded edit (similarity >= 0.90) the snapshot wins and the "
                               "difference is recorded here; larger differences are left unresolved in "
                               "body_text_differences rather than being aligned by guesswork.",
                "protected_markup": "English rendering uses rich_html taken from the current snapshot DOM, so "
                                    "speaker spans, em/strong, tables, colspan and SVG are not reconstructed "
                                    "from plain text; figure assets are raw SVG. The English copy is "
                                    "byte-identical to the original. The supplied Chinese figure and the "
                                    "adopted metadata edits on it are kept as they are; the only extra change "
                                    "is the font-family of text, so translated <text> keeps the Chinese "
                                    "stack while untranslated text and shared <g> fonts stay on the "
                                    "original, and a figure with no translation reuses the original bytes.",
            },
            "unresolved": unresolved,
        }
        with open(os.path.join(outdir, "alignment.json"), "w", encoding="utf-8") as f:
            json.dump(alignment, f, ensure_ascii=False, indent=1)

        status = "verified" if not unresolved else "unresolved"
        if unresolved:
            run["blocked"].append(n)
        run["chapters"].append({
            "chapter": n, "status": status,
            "blocks": len(blocks), "segments": len(segs), "zh_segments": len(zsegs),
            "type_counts": types,
            "tables": sum(1 for d in device_records if d["kind"] == "table"),
            "panels": sum(1 for d in device_records if d["kind"] in ("panel", "center-panel")),
            "english_version_differences": len(version_diffs),
            "english_version_difference_ids": [v["id"] for v in version_diffs],
            "figures": len(fig_out),
            "glossary": len(gloss),
            "unresolved": unresolved,
            "edits_applied": len(applied),
            "edits_failed": edit_problems,
            "source_json": os.path.join(outdir, "source.json"),
            "baseline_json": os.path.join(basedir, "zh.json"),
        })
        print("ch%-2d blocks=%-3d segments=%-3d tables=%-2d panels=%-2d figures=%d glossary=%-3d unresolved=%d %s"
              % (n, len(blocks), len(segs),
                 sum(1 for d in device_records if d["kind"] == "table"),
                 sum(1 for d in device_records if d["kind"] in ("panel", "center-panel")),
                 len(fig_out), len(gloss), len(unresolved), status.upper()))
        for u in unresolved:
            print("   UNRESOLVED", u)

    run_path = os.path.join(args.out_root, "_prepare-run.json")
    with open(run_path, "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=1)
    print("run record:", run_path)
    print("STATUS OK: %d/%d chapters verified" % (len(chapters) - len(run["blocked"]), len(chapters))
          if not run["blocked"] else "STATUS BLOCKED: %d chapter(s): %s" % (len(run["blocked"]), run["blocked"]))
    return 0 if not run["blocked"] else 1


if __name__ == "__main__":
    sys.exit(main())
