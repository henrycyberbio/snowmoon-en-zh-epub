# -*- coding: utf-8 -*-
"""Structural / content gates for the two block kinds the pilot renderer
handled lossily: multi-table device blocks and device panels.

Every assertion runs against the REAL chapter inputs under work/chapters/NN, not
against hand-written fixtures.  The expectation for the Chinese side is derived
independently of the renderer: from the source rich_html (cell texts, row order,
colspan, inline colour) and from the markdown conventions of the working
translation (pipes, blank lines, `>`, `- `, ``` fences, `***`).

Run from the full-book root:

    uv run --no-project --with beautifulsoup4==4.15.0 \
        python -m unittest discover -s book/tests -v
"""
import collections
import glob
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "book"))
WORK = os.path.join(ROOT, "work", "chapters")
NEEDS_FULL_BOOK = (os.path.isdir(WORK) and
                   all(os.path.exists(os.path.join(WORK, "%02d" % n, "source.json"))
                       for n in range(1, 33)))
NEEDS_WORK = unittest.skipUnless(NEEDS_FULL_BOOK, "full 32-chapter work/chapters not prepared")

import render as R                                    # noqa: E402
from bs4 import BeautifulSoup, Tag                    # noqa: E402

BLOCK_TAGS = {"div", "table", "p", "ul", "ol", "li", "blockquote", "pre",
              "section", "figure", "center", "h1", "h2", "h3", "h4", "hr"}
MD_NOISE = set("*>`~_|")            # markdown markup characters, never content
MD_RULE = re.compile(r"-{2,}|:?-+:?")


# --------------------------------------------------------------------- inputs
def chapter_numbers():
    return sorted(os.path.basename(os.path.dirname(p))
                  for p in glob.glob(os.path.join(WORK, "*", "source.json")))


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_chapter(ch):
    src = load_json(os.path.join(WORK, ch, "source.json"))
    zh = load_json(os.path.join(WORK, ch, "zh.json"))
    zmap = {z["id"]: (z.get("zh") or "") for z in zh["segments"]}
    return src, zmap


def all_segments():
    for ch in chapter_numbers():
        src, zmap = load_chapter(ch)
        for seg in src["segments"]:
            yield ch, seg, zmap.get(seg["id"], "")


def render(seg, zht):
    rep = {"fallbacks": [], "unresolved": []}
    return R.render_segment(seg, zht, R.Palette(), rep), rep


# ------------------------------------------------------------------ utilities
def nows(s):
    return re.sub(r"\s+", "", s or "")


def md_plain(text):
    """Markdown -> plain text: blank lines, fences, `***`, `>` prefixes, `- `
    bullets and table separator rows are markup; every other character is
    content."""
    out = []
    for line in (text or "").split("\n"):
        s = line.strip()
        if not s or s == "***" or s.startswith("```"):
            continue
        s = re.sub(r"^>\s?", "", s)
        s = re.sub(r"^[-*]\s+", "", s)
        cells = [c.strip() for c in s.strip("|").split("|")] if s.startswith("|") else [s]
        if cells and all(c == "" or MD_RULE.fullmatch(c) for c in cells):
            continue
        out.append(s)
    return nows("".join(out))


def md_tables(text):
    """[[rows, texts]] -- one entry per markdown table (blank line closes it)."""
    groups, rows, pending = [], [], []
    for line in (text or "").split("\n"):
        s = line.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(MD_RULE.fullmatch(c) for c in cells if c) and any("-" in c for c in cells):
                continue
            rows.append(cells)
        elif not s or s == "***":
            if rows:
                groups.append([rows, list(pending)])
                rows, pending = [], []
        else:
            pending.append(s)
    if rows:
        groups.append([rows, list(pending)])
        pending = []
    if pending:
        if groups:
            groups[-1][1].extend(pending)
        else:
            groups.append([[], list(pending)])
    return groups


def zh_cells(text):
    return [c for grp in md_tables(text) for row in grp[0] for c in row]


def elem_text(soup, **attrs):
    el = soup.find(**attrs)
    return "" if el is None else el.get_text("", strip=False)


def colour_of(style):
    m = re.search(r"color\s*:\s*([^;]+)", style or "")
    return m.group(1).strip().lower() if m else None


def span_of(cell):
    try:
        return int(cell.get("colspan", 1) or 1)
    except ValueError:
        return 1


def source_root(seg):
    return BeautifulSoup(seg["rich_html"], "html.parser").find()


ZH_CLASSES = ("zh-line", "zh", "zh-body", "zh-list", "zh-quote", "zh-pre", "dv-zh", "dv-zh-prose")


def lang_text(html, drop):
    """Text of one language side: the other side's elements are removed first."""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(class_=list(drop)):
        el.decompose()
    return nows(soup.get_text("", strip=False))


def en_text(html):
    return lang_text(html, ZH_CLASSES)


def visible_counters(html):
    return collections.Counter(c for c in nows(BeautifulSoup(html, "html.parser").get_text("", strip=False))
                               if c not in MD_NOISE)


# ------------------------------------------------------------------ the tests
@NEEDS_WORK
class MultiTableBlocks(unittest.TestCase):
    """A device block may hold several <table>s; each one must survive with its
    own Chinese cells, in order, with colspan and inline colour intact."""

    def table_segments(self):
        for ch, seg, zht in all_segments():
            if seg.get("device") == "table":
                yield ch, seg, zht

    def test_one_output_table_per_source_table(self):
        bad = []
        for ch, seg, zht in self.table_segments():
            html, _ = render(seg, zht)
            soup = BeautifulSoup(html, "html.parser")
            src = source_root(seg)
            content = [t for t in soup.find_all("table") if "scale" not in (t.get("class") or [])]
            if src.find("input"):       # vote panel: rebuilt as title/slider/action rows
                if not soup.find(class_="dv-title") or not soup.find("table", class_="scale"):
                    bad.append((seg["id"], "vote panel markup missing"))
                continue
            if len(content) != len(src.find_all("table")):
                bad.append((seg["id"], "source tables=%d, rendered=%d"
                            % (len(src.find_all("table")), len(content))))
        self.assertEqual(bad, [], "table count mismatch")

    def test_every_english_cell_text_survives(self):
        bad = []
        for ch, seg, zht in self.table_segments():
            html, _ = render(seg, zht)
            got = en_text(html)
            for cell in source_root(seg).find_all(["td", "th"]):
                txt = nows(cell.get_text("", strip=True))
                if txt and txt not in got:
                    bad.append((seg["id"], txt[:40]))
        self.assertEqual(bad, [], "English cell text lost")

    def test_every_chinese_character_survives(self):
        bad = []
        for ch, seg, zht in self.table_segments():
            if not zht:
                continue
            html, _ = render(seg, zht)
            got = visible_counters(html)
            want = collections.Counter(c for c in md_plain(zht) if c not in MD_NOISE)
            diff = {k: (want[k], got[k]) for k in want if want[k] > got[k]}
            if diff:
                bad.append((seg["id"], sorted(diff.items())[:6]))
        self.assertEqual(bad, [], "Chinese table text lost")

    def test_each_chinese_table_stays_in_its_own_table(self):
        bad = []
        for ch, seg, zht in self.table_segments():
            src_tables = source_root(seg).find_all("table")
            if len(src_tables) < 2:
                continue
            groups = [g for g in md_tables(zht) if g[0]]
            html, _ = render(seg, zht)
            soup = BeautifulSoup(html, "html.parser")
            tables = [t for t in soup.find_all("table") if "scale" not in (t.get("class") or [])]
            if len(groups) != len(tables) or len(tables) != len(src_tables):
                bad.append((seg["id"], "%d zh groups / %d tables / %d source tables"
                            % (len(groups), len(tables), len(src_tables))))
                continue
            for k, (group, table) in enumerate(zip(groups, tables)):
                own = lang_text(str(table), ("en-line",))
                for row in group[0]:
                    for cell in row:
                        t = nows(cell)
                        if t and not MD_RULE.fullmatch(t) and t not in own:
                            bad.append((seg["id"], "table %d missing zh %r" % (k + 1, t[:30])))
                if k == 0:
                    later = [nows(c) for grp in groups[1:] for row in grp[0] for c in row]
                    for t in later:
                        if len(t) > 1 and not MD_RULE.fullmatch(t) and t in own:
                            bad.append((seg["id"], "table 1 swallowed zh %r" % t[:30]))
        self.assertEqual(bad, [], "Chinese table content crossed tables")

    def test_colspan_and_colour_kept(self):
        bad = []
        for ch, seg, zht in self.table_segments():
            html, _ = render(seg, zht)
            soup = BeautifulSoup(html, "html.parser")
            cells = soup.find_all(["td", "th"])
            for cell in source_root(seg).find_all(["td", "th"]):
                txt = nows(cell.get_text("", strip=True))
                span = span_of(cell)
                col = colour_of(cell.get("style"))
                match = [c for c in cells if nows(c.get_text("", strip=True)).startswith(txt)] if txt else []
                if span != 1 and not any(span_of(c) == span for c in match):
                    bad.append((seg["id"], "colspan %d lost for %r" % (span, txt[:30])))
                if col and not any(colour_of(c.get("style")) == col for c in match):
                    bad.append((seg["id"], "colour %s lost for %r" % (col, txt[:30])))
        self.assertEqual(bad, [], "colspan / inline colour lost")

    def test_no_raw_markdown_dump_and_nothing_unresolved(self):
        bad = []
        for ch, seg, zht in self.table_segments():
            html, rep = render(seg, zht)
            if 'class="dv-zh"' in html or 'class="dv-fallback"' in html:
                bad.append((seg["id"], "raw ZH markdown dump / fallback"))
            if rep["unresolved"] or rep["fallbacks"]:
                bad.append((seg["id"], rep["unresolved"] or rep["fallbacks"]))
        self.assertEqual(bad, [], "unparsed table fell back to raw text")


@NEEDS_WORK
class DevicePanels(unittest.TestCase):
    """device=panel / <center> blocks are containers: they must not be wrapped
    in a <p>, and their text, emphasis and speaker colours must survive."""

    def panels(self):
        for ch, seg, zht in all_segments():
            if seg.get("device") in ("panel", "center"):
                yield ch, seg, zht

    def test_panel_count_is_27(self):
        self.assertEqual(len(list(self.panels())), 27)

    def test_no_block_element_inside_a_paragraph(self):
        bad = []
        for ch, seg, zht in self.panels():
            html, _ = render(seg, zht)
            soup = BeautifulSoup(html, "html.parser")
            for p in soup.find_all("p"):
                inner = [c.name for c in p.find_all(recursive=True) if c.name in BLOCK_TAGS]
                if inner:
                    bad.append((seg["id"], sorted(set(inner))))
        self.assertEqual(bad, [], "block element nested inside <p>")

    def test_source_container_survives(self):
        bad = []
        for ch, seg, zht in self.panels():
            html, _ = render(seg, zht)
            soup = BeautifulSoup(html, "html.parser")
            src = source_root(seg)
            if src.name == "center":
                if not soup.find(class_="dv-center-block"):
                    bad.append((seg["id"], "centre container dropped"))
                continue
            for cls in src.get("class") or []:
                if cls != "device-view" and not soup.find(class_=cls):
                    bad.append((seg["id"], "container class %s dropped" % cls))
            for attr in ("style",):
                if src.get(attr) and not soup.find(attrs={attr: src.get(attr)}):
                    bad.append((seg["id"], "container %s dropped" % attr))
        self.assertEqual(bad, [], "panel container lost")

    def test_all_english_text_survives(self):
        bad = []
        for ch, seg, zht in self.panels():
            html, _ = render(seg, zht)
            soup = BeautifulSoup(html, "html.parser")
            got = nows(elem_text(soup, id="en-%s" % seg["id"]))
            want = nows(source_root(seg).get_text("", strip=False))
            if got != want:
                bad.append((seg["id"], "EN chars %d != %d" % (len(got), len(want))))
        self.assertEqual(bad, [], "English panel text changed")

    def test_emphasis_and_inline_colour_survive(self):
        bad = []
        for ch, seg, zht in self.panels():
            html, _ = render(seg, zht)
            soup = BeautifulSoup(html, "html.parser")
            src = source_root(seg)
            for name in ("em", "b", "strong", "pre", "hr"):
                if len(soup.find_all(name)) < len(src.find_all(name)):
                    bad.append((seg["id"], "%s lost" % name))
            for el in src.find_all(style=True):
                col = colour_of(el.get("style"))
                if col and not [x for x in soup.find_all(style=True)
                                if colour_of(x.get("style")) == col]:
                    bad.append((seg["id"], "inline colour %s lost" % col))
        self.assertEqual(bad, [], "panel inline markup lost")

    def test_all_chinese_characters_survive(self):
        bad = []
        for ch, seg, zht in self.panels():
            if not zht:
                continue
            html, _ = render(seg, zht)
            soup = BeautifulSoup(html, "html.parser")
            got = collections.Counter(
                c for c in nows(elem_text(soup, id="zh-%s" % seg["id"])) if c not in MD_NOISE)
            want = collections.Counter(c for c in md_plain(zht) if c not in MD_NOISE)
            diff = {k: (want[k], got[k]) for k in set(want) | set(got) if want[k] != got[k]}
            if diff:
                bad.append((seg["id"], sorted(diff.items())[:6]))
        self.assertEqual(bad, [], "Chinese panel characters lost")


@NEEDS_WORK
class WholeBook(unittest.TestCase):
    def test_counts(self):
        segs = list(all_segments())
        self.assertEqual(len({ch for ch, _, _ in segs}), 32)
        self.assertEqual(len(segs), 4310)
        self.assertEqual(len([s for _, s, _ in segs if s.get("device") == "table"]), 65)
        self.assertEqual(len([s for _, s, _ in segs if s.get("device") in ("panel", "center")]), 27)

    def test_no_paragraph_contains_block_content_anywhere(self):
        bad = []
        for ch, seg, zht in all_segments():
            html, _ = render(seg, zht)
            for p in BeautifulSoup(html, "html.parser").find_all("p"):
                inner = [c.name for c in p.find_all(recursive=True) if c.name in BLOCK_TAGS]
                if inner:
                    bad.append((seg["id"], seg.get("device"), sorted(set(inner))))
        self.assertEqual(bad, [], "illegal <p> nesting in the book")

    def test_ordinary_paragraphs_still_use_the_pair_p_markup(self):
        """The 4000+ normal paragraphs must keep p.en/p.zh (no visual drift)."""
        n = 0
        for ch, seg, zht in all_segments():
            if seg["type"] != "body" or seg.get("device") in ("panel", "center", "table", "figure"):
                continue
            b = source_root(seg)
            if b is None or b.name != "p":
                continue
            html, _ = render(seg, zht)
            if "en-line" in zht or "zh-line" in zht:
                continue
            if re.search(r"\n\s*\n", zht or "") or re.match(r"^\s*[-*>]", zht or ""):
                continue
            n += 1
            if '<p class="en" lang="en"' not in html or '<p class="zh" lang="zh-Hans"' not in html:
                self.fail("%s lost the plain paragraph pair markup" % seg["id"])
        self.assertGreater(n, 3000)


# ------------------------------------------------------- figure inscriptions
FIG_DIR = os.path.join(ROOT, "tests", "fixtures", "ch19-033")
MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def source_siblings(seg):
    """Text the author placed beside the artwork: the device-view children other
    than the drawing itself."""
    out = []
    for ch in source_root(seg).children:
        if getattr(ch, "name", None) in ("svg", "script", "style"):
            continue
        t = ch.get_text(" ", strip=True) if hasattr(ch, "get_text") else str(ch).strip()
        if t:
            out.append((getattr(ch, "name", "text"), t))
    return out


def figure_segments():
    for ch, seg, zht in all_segments():
        if seg.get("device") == "figure":
            yield ch, seg, zht


@NEEDS_WORK
class FigureInscriptions(unittest.TestCase):
    """19-033 keeps the author's conlang inscription after the envelope drawing.
    render_figure() emitted only the two <img>s, so both the English and the
    Chinese edition silently lost those two lines.  The fixture is a frozen copy
    of the authoritative source/zh so a sibling's concurrent translation edit
    cannot fake a regression here."""

    def fixture(self, name):
        with open(os.path.join(FIG_DIR, name), encoding="utf-8") as fh:
            return json.load(fh)

    def fixture_segment(self):
        src = self.fixture("source.json")
        zmap = {z["id"]: (z.get("zh") or "") for z in self.fixture("zh.json")["segments"]}
        seg = [s for s in src["segments"] if s["id"] == "19-033"][0]
        return seg, zmap.get("19-033", "")

    def test_fixture_is_the_authoritative_source(self):
        live = load_json(os.path.join(WORK, "19", "source.json"))
        fix = self.fixture("source.json")
        self.assertEqual(fix["sha256"], live["sha256"])
        self.assertEqual(fix["segments"], live["segments"])

    def test_only_one_figure_carries_outside_text(self):
        hits = [seg["id"] for _, seg, _ in figure_segments() if source_siblings(seg)]
        self.assertEqual(hits, ["19-033"])

    def test_inscription_appears_for_both_languages(self):
        seg, zht = self.fixture_segment()
        html, _ = render(seg, zht)
        soup = BeautifulSoup(html, "html.parser")
        wrap = soup.find("figure", class_="dv-fig-wrap")
        self.assertIsNotNone(wrap)
        imgs = wrap.find_all("img")
        self.assertEqual(len(imgs), 2)
        self.assertEqual(len(soup.find_all("img")), 2)     # no duplicated placeholder
        self.assertIsNone(soup.find("svg"))                # the drawing is not re-inlined
        want_en = nows(source_root(seg).get_text("", strip=False))
        want_zh = nows(MD_IMAGE.sub("", zht))
        caps = wrap.find_all(class_="dv-fig-cap")
        self.assertEqual(len(caps), 2)
        self.assertEqual(nows(caps[0].get_text("", strip=False)), want_en)
        self.assertEqual(nows(caps[1].get_text("", strip=False)), want_zh)
        for img, cap in ((imgs[0], caps[0]), (imgs[1], caps[1])):
            self.assertIs(img.find_next_sibling(), cap)   # caption stays with its image
        self.assertEqual(len(caps[0].find_all("br")), 1)   # author's line break kept
        self.assertEqual(len(caps[1].find_all("br")), 1)
        self.assertEqual(len(caps[0].find_all("b")), 1)    # author's emphasis kept
        self.assertIn('class="dv-fig-cap dv-fig-cap-en"', html)
        self.assertIn('class="dv-fig-cap dv-fig-cap-zh"', html)

    def test_plain_figures_gain_no_empty_caption(self):
        bad = []
        for _, seg, zht in figure_segments():
            html, _ = render(seg, zht)
            if source_siblings(seg):
                continue
            if "dv-fig-cap" in html:
                bad.append(seg["id"])
        self.assertEqual(bad, [], "caption block added to a plain figure")

    def test_book_still_has_112_figure_images(self):
        n = sum(len(BeautifulSoup(render(seg, zht)[0], "html.parser").find_all("img"))
                for _, seg, zht in figure_segments())
        self.assertEqual(n, 112)


if __name__ == "__main__":
    unittest.main()
