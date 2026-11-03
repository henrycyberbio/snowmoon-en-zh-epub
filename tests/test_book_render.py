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
import build_full as B                                # noqa: E402
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
        # prepare_inputs found no translation in this figure, so only the
        # English art ships; the author's lines still appear in both languages.
        fig = seg["figure"]
        want_imgs = [fig["en"]] + ([fig["zh"]] if fig.get("has_translation", True) else [])
        imgs = wrap.find_all("img")
        self.assertEqual([i.get("src") for i in imgs], want_imgs)
        self.assertEqual(len(soup.find_all("img")), len(want_imgs))  # no duplicated placeholder
        self.assertIsNone(soup.find("svg"))                # the drawing is not re-inlined
        want_en = nows(source_root(seg).get_text("", strip=False))
        want_zh = nows(MD_IMAGE.sub("", zht))
        caps = wrap.find_all(class_="dv-fig-cap")
        self.assertEqual(len(caps), 2)
        self.assertEqual(nows(caps[0].get_text("", strip=False)), want_en)
        self.assertEqual(nows(caps[1].get_text("", strip=False)), want_zh)
        paired = [caps[0]] + ([caps[1]] if len(want_imgs) == 2 else [])
        for img, cap in zip(imgs, paired):
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

    def test_each_figure_shows_the_images_its_translation_state_calls_for(self):
        """prepare_inputs derives has_translation per figure: no translation means
        the original bytes ship once, a translation means the pair.  Two images
        per figure was never the rule, so the count is re-derived here instead of
        pinned to a total."""
        figures, translated, images = 0, 0, 0
        for _, seg, zht in figure_segments():
            fig = seg.get("figure") or {}
            self.assertIn("has_translation", fig,
                          "%s: prepared figure must state has_translation" % seg["id"])
            self.assertIsInstance(fig["has_translation"], bool,
                                  "%s: has_translation must be a bool" % seg["id"])
            imgs = BeautifulSoup(render(seg, zht)[0], "html.parser").find_all("img")
            want = [fig["en"]] + ([fig["zh"]] if fig["has_translation"] else [])
            self.assertEqual([i.get("src") for i in imgs], want,
                             "%s: has_translation=%r" % (seg["id"], fig["has_translation"]))
            figures += 1
            translated += 1 if fig["has_translation"] else 0
            images += len(imgs)
        self.assertGreater(figures, 0, "no figure rendered")
        self.assertGreater(translated, 0, "no translated figure: pair branch unproven")
        self.assertGreater(figures - translated, 0,
                           "no untranslated figure: single-image branch unproven")
        self.assertEqual(images, 2 * translated + (figures - translated))


# ------------------------------------------------- Apple Books theme contract
CSS_PATH = os.path.join(ROOT, "book", "style_base.css")
PANEL_CHAPTERS = [n for n in ("01", "02")
                  if os.path.exists(os.path.join(WORK, n, "source.json"))]
# regions that carry a surface+ink pair of their own (dark device chrome, card,
# lyric block): a reader repainting text would leave them unreadable
PAIRED_SURFACE_CLASSES = {"device", "dz-card", "lyric-pair"}
PAGE_RULES = ("html, body", "html,body", "body", "html")


def css_flat_rules(css):
    """{selector: [declaration blocks]} for the sheet's flat rules."""
    out = {}
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)          # comments are not selectors
    for sel, decls in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        sel = " ".join(sel.split()).split(";")[-1].strip()   # drop a leading @charset
        if sel.startswith("@") or not sel:
            continue
        out.setdefault(sel, []).append(decls)
    return out


@unittest.skipUnless(PANEL_CHAPTERS, "no prepared chapter to render")
class AppleBooksThemeContract(unittest.TestCase):
    """Apple Books' dark theme repaints text (Asset Guide, "Custom Text Colors
    with Dark Themes"): white text everywhere, and the documented opt-out is
    class="ibooks-dark-theme-use-custom-text-color" on the region itself.
    So we must not paint the page surface, and every region that keeps its own
    colours must carry that class."""

    def test_page_surface_belongs_to_the_reader(self):
        with open(CSS_PATH, encoding="utf-8") as fh:
            rules = css_flat_rules(fh.read())
        for sel in PAGE_RULES:
            for decl in rules.get(sel, []):
                self.assertNotRegex(
                    decl, r"(?<![-\w])(background|background-color)\s*:",
                    "%s must leave the page surface to the reader: %r" % (sel, decl))

    def test_paired_surfaces_carry_the_apple_contract(self):
        panels = 0
        for ch in PANEL_CHAPTERS:
            src, zmap = load_chapter(ch)
            for seg in src["segments"]:
                html, _ = render(seg, zmap.get(seg["id"], ""))
                for tag in BeautifulSoup(html, "html.parser").find_all(True):
                    classes = set(tag.get("class") or [])
                    if not classes & PAIRED_SURFACE_CLASSES:
                        continue
                    panels += 1
                    self.assertIn("ibooks-dark-theme-use-custom-text-color", classes,
                                  "%s: %s panel not opted out of reader recolouring"
                                  % (seg["id"], sorted(classes & PAIRED_SURFACE_CLASSES)))
        self.assertGreater(panels, 0, "no paired-surface panel rendered in %s" % PANEL_CHAPTERS)


# ------------------------------------------- source data kept, not decoration
class CellStyleWhitelist(unittest.TestCase):
    """A table cell's own style can carry data: the status mark in a device
    panel is written 300% and yellow.  Only the declarations with meaning may
    travel with the cell; layout stays in the stylesheet."""

    def cell(self, style):
        seg = {"id": "02-900", "type": "body", "device": "table",
               "rich_html": ('<div class="device-view narrow-device-view"><table>'
                             '<thead><tr><th style="%s">X</th></tr></thead></table></div>'
                             % style)}
        html, rep = render(seg, "| X |\n| --- |")
        self.assertEqual(rep["unresolved"], [])
        return BeautifulSoup(html, "html.parser").find("th")

    def test_author_size_and_colour_survive(self):
        th = self.cell("font-size:300%; color: #ff8")
        style = th.get("style") or ""
        self.assertIn("font-size:300%", nows(style))
        self.assertEqual(colour_of(style), "#ff8")

    def test_layout_only_declarations_are_still_dropped(self):
        th = self.cell("width:6%; text-align:center; padding:2px")
        self.assertIsNone(th.get("style"), "layout-only style leaked into the cell")

    def test_every_source_font_size_reaches_the_rendered_cell(self):
        checked = 0
        for ch in PANEL_CHAPTERS:
            src, zmap = load_chapter(ch)
            for seg in src["segments"]:
                for cell in source_root(seg).find_all(["td", "th"]):
                    size = re.search(r"font-size\s*:\s*([^;\"']+)", cell.get("style") or "")
                    if not size:
                        continue
                    checked += 1
                    html, _ = render(seg, zmap.get(seg["id"], ""))
                    want = nows(cell.get_text("", strip=True))
                    got = [c for c in BeautifulSoup(html, "html.parser").find_all(["td", "th"])
                           if nows(c.get_text("", strip=True)).startswith(want)]
                    self.assertTrue(
                        got and all(size.group(1).strip() in (c.get("style") or "") for c in got),
                        "%s: source font-size %r lost from the cell"
                        % (seg["id"], size.group(1).strip()))
        if checked == 0:
            self.skipTest("prepared chapters contain no cell with font-size")


class SpeakerPalette(unittest.TestCase):
    """The source stores a light AND a dark colour per speaker.  The sheet must
    publish both from those saved tuples -- nothing is re-tinted downstream."""

    LIGHT = "oklch(0.692 0.150 26.7)"
    DARK = "oklch(0.851 0.150 26.7)"

    def palette(self):
        pal = R.Palette()
        pal.idx("--spk-lt:%s;--spk-dk:%s" % (self.LIGHT, self.DARK))
        return pal

    def test_light_and_dark_rules_carry_the_saved_pair(self):
        pal = self.palette()
        lt = R.oklch_to_hex(0.692, 0.150, 26.7)
        dk = R.oklch_to_hex(0.851, 0.150, 26.7)
        self.assertNotEqual(lt, dk)
        self.assertIn(".s1 { color:%s; }" % lt, pal.css())
        self.assertIn(".s1 { color:%s; }" % dk, pal.dark_css())

    def test_dark_rules_are_scoped_to_the_dark_scheme(self):
        dark = self.palette().dark_css()
        self.assertTrue(dark.startswith("@media (prefers-color-scheme: dark)"), dark)
        self.assertNotIn("only", dark)

    def test_a_speaker_without_a_dark_value_keeps_its_light_one(self):
        pal = R.Palette()
        pal.idx("--spk-lt:%s" % self.LIGHT)
        lt = R.oklch_to_hex(0.692, 0.150, 26.7)
        self.assertIn(".s1 { color:%s; }" % lt, pal.dark_css())


class ThemeContractScope(unittest.TestCase):
    """The marker is a local opt-out on the region that owns a colour: the
    speaker span itself, never the paragraph or the page around it."""

    def soup(self, html):
        return BeautifulSoup(html, "html.parser")

    def test_a_speaker_span_is_marked_but_its_paragraph_is_not(self):
        html = R.theme_contract('<p class="en" lang="en"><span class="s2">t</span></p>')
        soup = self.soup(html)
        self.assertIn(R.THEME_CONTRACT, soup.find("span").get("class"))
        self.assertNotIn(R.THEME_CONTRACT, soup.find("p").get("class") or [])

    def test_a_folded_speaker_class_is_still_recognised(self):
        # Calibre may collapse `.s1` to `.s`; the marker must survive that shape
        html = R.theme_contract('<p class="en"><span class="s">t</span></p>')
        self.assertIn(R.THEME_CONTRACT, self.soup(html).find("span").get("class"))

    def test_ordinary_markup_stays_unmarked(self):
        html = R.theme_contract('<div class="page"><p class="zh" lang="zh-Hans">'
                                'a <span class="sep">\u00b7</span></p></div>')
        self.assertEqual(html.count(R.THEME_CONTRACT), 0)

    def test_a_document_without_markers_matches_zero(self):
        """The packaging gate compares counts; 0 == 0 is a real pass, not a
        skipped check, so a book with no such region still validates."""
        text = '<html><body><p class="zh">plain</p></body></html>'
        self.assertEqual(R.theme_contract(text), text)
        self.assertEqual(R.theme_contract(text).count(R.THEME_CONTRACT), 0)


class PaletteAuditContexts(unittest.TestCase):
    """Light and dark rules give one class two colours: that is the author's
    data, not a duplicate.  Two colours for one class inside the SAME context
    still fail, so the audit keeps checking."""

    def setUp(self):
        self.pal = R.Palette()
        self.pal.idx("--spk-lt:%s;--spk-dk:%s" % (SpeakerPalette.LIGHT, SpeakerPalette.DARK))
        self.docs = ['<p><span class="s1">a</span></p>']

    def audit(self, css, dark=None):
        return B.palette_audit(css, self.docs, self.pal.dark_css() if dark is None else dark)

    def test_light_and_dark_are_not_a_duplicate(self):
        res = self.audit(self.pal.css())
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["duplicate_class_with_different_colour"], {})

    def test_a_dark_block_inside_the_sheet_is_read_as_the_dark_context(self):
        res = self.audit(self.pal.css() + "\n" + self.pal.dark_css(), dark="")
        self.assertTrue(res["ok"], res)

    def test_two_colours_for_one_class_in_one_context_still_fail(self):
        css = ".s1 { color:#101010; }\n.s1 { color:#202020; }"
        self.assertFalse(self.audit(css)["ok"])

    def test_a_palette_without_dark_rules_is_reported(self):
        res = self.audit(self.pal.css(), dark="")
        self.assertFalse(res["ok"])
        self.assertEqual(res["dark_classes_missing"], ["s1"])

    def test_an_undefined_class_is_still_caught(self):
        res = self.audit(".s2 { color:#123456; }", dark=".s2 { color:#654321; }")
        self.assertFalse(res["ok"])
        self.assertEqual(res["classes_used_without_definition"], ["s1"])


class AppleBooksDarkScheme(unittest.TestCase):
    """The official example declares both schemes on :root and puts the dark
    colours in a media query.  The declaration is a statement of intent: it
    neither locks the page nor rescues a background the reader owns."""

    def css(self):
        with open(CSS_PATH, encoding="utf-8") as fh:
            return fh.read()

    def test_root_declares_both_schemes(self):
        decls = " ".join(css_flat_rules(self.css()).get(":root", []))
        self.assertRegex(decls, r"color-scheme\s*:\s*light\s+dark")
        self.assertNotIn("only", decls)

    def test_a_source_coloured_cell_gives_both_lines_one_colour(self):
        rules = css_flat_rules(self.css())
        guard = [s for s in rules if "zh-line" in s and "[style" in s and "inherit" in " ".join(rules[s])]
        self.assertTrue(guard, "no cell-scoped inherit rule for a source colour")
        self.assertIn("color:#9ec2f5", " ".join(rules.get(".device .zh-line", [])),
                      "ordinary device Chinese lost its own ink")


class ConvertedThemeWiring(unittest.TestCase):
    """The dark half of each dialogue colour has to reach the converted package.

    Calibre rewrites the class names (measured in this round's probe: source
    `.s1` -> `.s`, `.s2` -> `.s1`) and flattens a media block that is already in
    the sheet, so neither the source class names nor a pre-conversion media
    query can carry the author's second colour.  The only stable handle is the
    resolved light colour in the converted stylesheet.  Fixture below is shaped
    like the converter's own output, not like our source sheet.
    """

    PAIRS = [("#c0392b", "#ff6b6b"), ("#2e86de", "#74b9ff")]

    CONVERTED_CSS = """
.calibre1 { color:#1b1b22; display:block; }
.calibre5 { background-color:#1a2a4a; background-image:linear-gradient(#1a2a4a, #1a2a4a);
  color:#ff8; font-size:300%; }
.device { background-color:#0a0e27; background-image:linear-gradient(#0a0e27, #0a0e27);
  color:#b8d8ff; }
.s { color:#c0392b; }
.s1 { color:#2e86de; }
"""

    DOC = ('<html><head><title>t</title></head><body>'
           '<p class="en"><span class="s">alpha</span> and <span class="s1">beta</span></p>'
           '<div class="device"><table><tr><th class="calibre5">\u2713</th>'
           '<td><span class="zh-line">t</span></td></tr></table></div>'
           '</body></html>')

    def test_selectors_come_from_the_converted_colours_not_the_source_names(self):
        sel, ambiguous = B.speaker_selector_map(self.CONVERTED_CSS, self.PAIRS)
        self.assertEqual(sorted(sel), ["s", "s1"])
        self.assertEqual(sel["s"], ("#c0392b", "#ff6b6b"))
        self.assertEqual(sel["s1"], ("#2e86de", "#74b9ff"))
        self.assertEqual(ambiguous, {}, "a generated class was taken for a speaker")
        self.assertNotIn("calibre5", sel, "a 300% status cell is not a dialogue colour")

    def test_two_classes_for_one_dialogue_colour_is_reported(self):
        css = ".s { color:#c0392b; }\n.s1 { color:#c0392b; }\n.s2 { color:#2e86de; }"
        sel, ambiguous = B.speaker_selector_map(css, self.PAIRS)
        self.assertEqual(ambiguous.get("#c0392b"), ["s", "s1"])

    def test_dark_block_names_the_converted_classes(self):
        sel, _ = B.speaker_selector_map(self.CONVERTED_CSS, self.PAIRS)
        block = B.dark_theme_style(sel)
        self.assertIn("@media (prefers-color-scheme: dark)", block)
        self.assertIn(".s { color:#ff6b6b; }", block)
        self.assertIn(".s1 { color:#74b9ff; }", block)
        self.assertNotIn("only", block.replace("prefers-color-scheme", ""))

    def test_dark_block_also_restores_the_two_scheme_root_declaration(self):
        """Calibre drops `:root{color-scheme:light dark}` from our sheet, so the
        package never tells the reader that both themes are authored.  The block
        we inject after conversion is the only place left to state it, and it has
        to sit beside the media query -- inside it would apply to dark only."""
        sel, _ = B.speaker_selector_map(self.CONVERTED_CSS, self.PAIRS)
        block = B.dark_theme_style(sel)
        root = re.search(r":root\s*\{[^}]*color-scheme\s*:\s*light\s+dark\s*;?[^}]*\}", block)
        media = "@media (prefers-color-scheme: dark)"
        self.assertIsNotNone(root, "injected block does not declare both schemes on :root")
        self.assertLess(block.index(root.group(0)), block.index(media),
                        ":root declaration must precede the media query, not live inside it")
        self.assertNotIn(":root", block[block.index("{", block.index(media)):],
                         ":root declaration must sit outside the media query body")
        self.assertNotIn("only", block, "the declaration must not lock one scheme")

    def test_one_pass_marks_the_spans_and_adds_the_dark_rules_once(self):
        out, info = B.wire_theme(self.DOC, self.CONVERTED_CSS, self.PAIRS, R)
        soup = BeautifulSoup(out, "html.parser")
        spans = soup.find_all("span")
        self.assertEqual([R.THEME_CONTRACT in (s.get("class") or []) for s in spans],
                         [True, True, False], "speaker spans only, never the zh-line")
        self.assertEqual(out.count("prefers-color-scheme: dark"), 1)
        self.assertEqual(out.count("</style>"), 1)
        again, _ = B.wire_theme(out, self.CONVERTED_CSS, self.PAIRS, R)
        self.assertEqual(again.count("prefers-color-scheme: dark"), 1, "wiring is not idempotent")
        self.assertEqual(again.count(R.THEME_CONTRACT), out.count(R.THEME_CONTRACT),
                         "a second pass must not add markers")
        self.assertEqual(info["selectors"], ["s", "s1"])

    def test_wiring_changes_no_visible_text(self):
        out, _ = B.wire_theme(self.DOC, self.CONVERTED_CSS, self.PAIRS, R)
        self.assertEqual(B.text_of(out), B.text_of(self.DOC))

    def test_a_document_without_speakers_gets_no_dark_block(self):
        doc = ('<html><head></head><body><p class="zh">plain</p></body></html>')
        out, _ = B.wire_theme(doc, self.CONVERTED_CSS, self.PAIRS, R)
        self.assertEqual(out, doc)


class PanelGradientGate(unittest.TestCase):
    """The read-back gate must prove the gradient, not the colour.

    Reproduced by the parent reviewer: delete every `background-image` from the
    package and the old check still passed, because it only asked whether the
    hex appeared somewhere in the sheet -- and the same hex is the
    `background-color` of the very surface whose gradient was removed.
    """

    WITH_GRADIENT = (".device { background-color:#1a2a4a; "
                     "background-image:linear-gradient(#1a2a4a, #1a2a4a); }")
    WITHOUT_GRADIENT = (".device { background-color:#1a2a4a; }\n"
                        ".calibre5 { background-color:#1a2a4a; color:#ff8; font-size:300%; }")

    def test_a_same_colour_gradient_counts_only_when_the_declaration_exists(self):
        self.assertIn(("#1a2a4a", "#1a2a4a"), B.gradient_endpoints(self.WITH_GRADIENT))

    def test_the_bare_colour_never_stands_in_for_a_deleted_gradient(self):
        self.assertEqual(set(), B.gradient_endpoints(self.WITHOUT_GRADIENT),
                         "a background-color hex was accepted as a gradient")

    def test_an_unrelated_gradient_does_not_cover_the_wanted_colour(self):
        css = ".device { background-image:linear-gradient(#0a0e27, #0a0e27); }"
        self.assertNotIn(("#1a2a4a", "#1a2a4a"), B.gradient_endpoints(css))


PANEL_SURFACES = (
    # surfaces that carry text and are meant to be opaque in the source; the
    # probe shows background-color + a same-colour gradient survive conversion
    ".device",
    ".device th",
    ".device td",
    ".device tr:nth-child(even) td",
    ".device .dv-title",
    ".device .dv-body",
    ".pair .dv-center-block .dz-card",
    ".pair pre[style*=\"color:#fff\"]",
    ".lyric-pair",
)


class PanelSurfaceBackground(unittest.TestCase):
    """Apple's dark stylesheet clears `background-color` on everything; it does
    not touch `background-image` (measured: a same-colour linear-gradient stays
    bound to the panel).  The pair must therefore be written as two longhands on
    the surfaces that carry text -- a `background:` shorthand would reset one of
    them.  This is a candidate awaiting device acceptance, not a verified fix."""

    def rules(self):
        with open(CSS_PATH, encoding="utf-8") as fh:
            return css_flat_rules(fh.read())

    def test_every_text_surface_pairs_its_colour_with_a_gradient(self):
        rules = self.rules()
        for sel in PANEL_SURFACES:
            self.assertIn(sel, rules, "panel surface %r missing from the sheet" % sel)
            decl = " ".join(rules[sel])
            colour = re.search(r"background-color\s*:\s*(#[0-9a-fA-F]{3,6})", decl)
            grad = re.search(r"background-image\s*:\s*linear-gradient\(\s*(#[0-9a-fA-F]{3,6})\s*,"
                             r"\s*(#[0-9a-fA-F]{3,6})\s*\)", decl)
            self.assertIsNotNone(colour, "%s has no explicit background-color" % sel)
            self.assertIsNotNone(grad, "%s has no linear-gradient background-image" % sel)
            self.assertEqual(colour.group(1).lower(), grad.group(1).lower(), sel)
            self.assertEqual(grad.group(1).lower(), grad.group(2).lower(), sel)

    def test_no_panel_surface_uses_the_background_shorthand(self):
        for sel in PANEL_SURFACES:
            for decl in self.rules().get(sel, []):
                self.assertNotRegex(decl, r"(?<![-a-z])background\s*:",
                                    "%s uses the shorthand that resets the pair" % sel)

    def test_ordinary_page_and_body_are_still_left_to_the_reader(self):
        rules = self.rules()
        for sel in ("html", "body", "html, body", ".page"):
            for decl in rules.get(sel, []):
                self.assertNotRegex(decl, r"(?<![-a-z])(background|background-image|background-color)\s*:",
                                    "%s must stay the reader's surface: %r" % (sel, decl))


# Ordinary text (body, headings, dateline, bilingual paragraphs, back matter)
# must carry no foreground of its own: the reader paints the page and its text
# together, so a fixed ink from a light theme is what goes unreadable on a dark
# page.  Only regions that bring a complete surface with them keep their ink.
ORDINARY_TEXT_SELECTORS = (
    "body",
    "h1.ch-title",
    "h1.ch-title .t-zh",
    ".dateline .txt",
    ".dateline .sep",
    ".dateline .date",
    ".dateline .zh-line",
    ".pair p.en",
    ".pair p.zh",
    ".scene-break",
    ".backmatter",
    ".backmatter .bm-title",
    ".backmatter a",
)
LOCAL_SURFACE_SELECTORS = (".device", ".msgtext", ".lyric", ".lyric-zh",
                           ".device .zh-line", ".pair .dv-center-block .dz-card")


class OrdinaryTextInkOwnership(unittest.TestCase):
    """A reader that repaints text for its dark theme cannot rescue an ink we
    locked in the sheet: it only sees its own page.  So ordinary prose declares
    no `color` at all and inherits whatever the reader is painting, while the
    panels, cards and lyric block that draw their own surface keep theirs."""

    def rules(self):
        with open(CSS_PATH, encoding="utf-8") as fh:
            return css_flat_rules(fh.read())

    def test_ordinary_text_declares_no_fixed_foreground(self):
        rules = self.rules()
        offenders = []
        for sel in ORDINARY_TEXT_SELECTORS:
            for decl in rules.get(sel, []):
                if re.search(r"(?<![-a-z])color\s*:", decl):
                    offenders.append((sel, decl.strip()))
        self.assertEqual(offenders, [],
                         "ordinary text must inherit the reader's ink: %r" % (offenders,))

    def test_regions_with_their_own_surface_keep_their_ink(self):
        rules = self.rules()
        for sel in LOCAL_SURFACE_SELECTORS:
            self.assertIn(sel, rules, "%r disappeared from the sheet" % sel)
            self.assertRegex(" ".join(rules[sel]), r"(?<![-a-z])color\s*:",
                             "%s lost the ink of its own surface" % sel)


if __name__ == "__main__":
    unittest.main()
