# -*- coding: utf-8 -*-
"""Bilingual rendering helpers adapted from the accepted
output/chapter-01-html-v2 build.py (original-DOM skeleton + real tables).

The input is one chapter's source.json/zh.json.  English always comes from
rich_html captured from the current snapshot DOM; Chinese is the editable
zh.json string.  Nothing is flattened through whole-book Markdown.
"""
import re, math, html as htmlmod
from bs4 import BeautifulSoup, NavigableString, Tag


def esc(t):
    return htmlmod.escape(t or "", quote=False)


def md_inline(t):
    """ZH string -> xhtml: **bold**, *italic*, quotes preserved."""
    t = esc(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t, flags=re.S)
    t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t, flags=re.S)
    return t


def oklch_to_hex(L, C, H):
    h = math.radians(H)
    a, b = C * math.cos(h), C * math.sin(h)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

    def enc(x):
        x = max(0.0, min(1.0, x))
        return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055

    return "#%02x%02x%02x" % (round(enc(r) * 255), round(enc(g) * 255), round(enc(bl) * 255))


class Palette:
    """Speaker colours in first-appearance order; CSS gets explicit hex only."""

    def __init__(self):
        self.colors = []

    def idx(self, style):
        m_lt = re.search(r"--spk-lt:\s*oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)", style or "")
        m_dk = re.search(r"--spk-dk:\s*oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)", style or "")
        if not m_lt:
            return 1
        lt = oklch_to_hex(*(float(x) for x in m_lt.groups()))
        dk = oklch_to_hex(*(float(x) for x in m_dk.groups())) if m_dk else lt
        pair = (lt, dk)
        if pair not in self.colors:
            self.colors.append(pair)
        return self.colors.index(pair) + 1

    def css(self):
        return "\n".join(".s%d { color:%s; }" % (i + 1, c[0]) for i, c in enumerate(self.colors))


QUOTE = re.compile(r"\u201c([^\u201d]*)\u201d")

# elements that may not sit inside <p> or <span>
BLOCK_NAMES = ("div", "table", "ul", "ol", "li", "p", "blockquote", "pre",
               "section", "figure", "center", "h1", "h2", "h3", "h4", "h5", "h6", "hr")
BLOCK_RE = re.compile(r"<(?:%s)\b" % "|".join(BLOCK_NAMES))


def has_block(html):
    return bool(BLOCK_RE.search(html or ""))


def _attrs(ch):
    out = []
    for k, v in ch.attrs.items():
        if isinstance(v, (list, tuple)):
            v = " ".join(str(x) for x in v)
        out.append(' %s="%s"' % (k, esc(str(v))))
    return "".join(out)


def zh_html(text, pals=None):
    if not text:
        return ""
    if not pals:
        return md_inline(text)
    out, last, qi = [], 0, 0
    for m in QUOTE.finditer(text):
        out.append(md_inline(text[last:m.start()]))
        inner = md_inline(m.group(1))
        cls = pals[qi] if qi < len(pals) else pals[-1]
        out.append("\u201c<span class=\"s%d\">%s</span>\u201d" % (cls, inner))
        last, qi = m.end(), qi + 1
    out.append(md_inline(text[last:]))
    return "".join(out)


def _inner(el, pal):
    """Preserve inline children/attributes; controls become static spans."""
    parts = []
    for ch in el.children:
        if isinstance(ch, NavigableString):
            parts.append(esc(str(ch)))
        elif isinstance(ch, Tag):
            cls = ch.get("class") or []
            if ch.name in ("script", "style"):
                continue
            if "spk" in cls:
                parts.append('<span class="s%d">%s</span>' % (pal.idx(ch.get("style")), _inner(ch, pal)))
            elif ch.name == "input":
                parts.append('<span class="dv-range"></span>')
            elif ch.name == "button":
                parts.append('<span class="dv-btn">%s</span>' % _inner(ch, pal))
            elif ch.name == "br":
                parts.append("<br/>")
            elif ch.name in ("img", "hr"):
                parts.append("<%s%s/>" % (ch.name, _attrs(ch)))
            else:
                parts.append("<%s%s>%s</%s>" % (ch.name, _attrs(ch), _inner(ch, pal), ch.name))
    return "".join(parts)


def en_html(node, pal):
    """EN block inner HTML with speaker colours preserved as classes."""
    return _inner(node, pal)


def en_runs(node, pal):
    out = []
    for el in node.children:
        if isinstance(el, NavigableString):
            t = str(el)
            if t.strip() == "":
                if out:
                    out.append(("plain", esc(t), 0))
                continue
            out.append(("plain", esc(t), 0))
        elif isinstance(el, Tag):
            cls = el.get("class") or []
            if "spk" in cls:
                out.append(("spk", _inner(el, pal), pal.idx(el.get("style"))))
            elif el.name == "br":
                out.append(("plain", "<br/>", 0))
            elif el.name == "em":
                out.append(("em", _inner(el, pal), 0))
            elif el.name in ("b", "strong"):
                out.append(("strong", _inner(el, pal), 0))
            else:
                out.append(("plain", _inner(el, pal), 0))
    return out


def quote_groups(node, pal):
    """[(palette index)] for each contiguous speaker group, in order."""
    res, cur = [], False
    for kind, h, p in en_runs(node, pal):
        if kind == "spk":
            if not cur:
                res.append(p)
            cur = True
        elif kind in ("em", "strong") and cur:
            pass
        elif kind == "plain" and h.strip() == "" and cur:
            pass
        else:
            cur = False
    return res


def bi(en_s, zh_s):
    """EN/ZH pair.  A fragment holding block content (a list, an <hr>, a panel)
    is paired with <div>, never with <span>."""
    if not zh_s:
        return en_s
    tag = "div" if (has_block(en_s) or has_block(zh_s)) else "span"
    return ('<%(t)s class="en-line">%(en)s</%(t)s><%(t)s class="zh-line">%(zh)s</%(t)s>'
            % {"t": tag, "en": en_s, "zh": zh_s})


# ---------------------------------------------------------------- block kinds
def render_heading(seg, zht, pal):
    b = BeautifulSoup(seg["rich_html"], "html.parser").find()
    return ('<h1 class="ch-title" id="blk-%s"><span class="t-en">%s</span>'
            '<span class="t-zh">%s</span></h1>'
            % (seg["id"], esc(b.get_text(" ", strip=True)), md_inline(zht)))


def render_dateline(seg, zht, pal):
    b = BeautifulSoup(seg["rich_html"], "html.parser").find()
    place = b.find(class_="place")
    date = b.find(class_="date")
    if place and date:
        p, d = place.get_text(" ", strip=True), date.get_text(" ", strip=True)
    else:
        txt = b.get_text(" ", strip=True)
        p, _, d = txt.partition("\u00b7")
        p, d = p.strip(), d.strip()
    return ('<div class="dateline" id="blk-%s"><hr class="rule"/>'
            '<div class="txt"><span class="place">%s</span><span class="sep">\u00b7</span>'
            '<span class="date">%s</span></div><div class="zh-line">%s</div></div>'
            % (seg["id"], esc(p), esc(d), md_inline(zht)))


def render_break(seg, zht, pal):
    return '<div class="scene-break" id="blk-%s">\u2733 \u2733 \u2733</div>' % seg["id"]


def render_quote(seg, zht, pal):
    b = BeautifulSoup(seg["rich_html"], "html.parser").find()
    en_lines = []
    for ch in b.children:
        if isinstance(ch, NavigableString) and ch.strip():
            en_lines.append(ch.strip())
        elif isinstance(ch, Tag):
            en_lines.append(ch.get_text(" ", strip=True))
    zh_lines = [re.sub(r"^>\s?", "", x).strip() for x in (zht or "").split("\n")]
    zh_lines = [x for x in zh_lines if x]
    return ('<div class="lyric-pair" id="blk-%s">%s%s</div>'
            % (seg["id"],
               "".join('<p class="lyric en" lang="en">%s</p>' % esc(x) for x in en_lines),
               "".join('<p class="lyric lyric-zh" lang="zh-Hans">%s</p>' % md_inline(x) for x in zh_lines)))


# ------------------------------------------------------------ zh prose blocks
def _strip_quote(raw):
    """'  > text' -> ('text', True); indentation kept for code lines."""
    q = raw.lstrip().startswith(">")
    return (re.sub(r"^\s*>\s?", "", raw).rstrip() if q else raw.strip()), q


def _zh_tokens(text):
    """zh.json prose -> [('code'|'rule'|'quote'|'bullet'|'text'|'blank', payload)]."""
    toks, lines, i = [], (text or "").split("\n"), 0
    while i < len(lines):
        s, quoted = _strip_quote(lines[i])
        if s.strip().startswith("```"):
            body, i = [], i + 1
            while i < len(lines):
                inner = re.sub(r"^\s*>\s?", "", lines[i])
                if inner.strip().startswith("```"):
                    break
                body.append(inner)
                i += 1
            i += 1
            toks.append(("code", "\n".join(body)))
            continue
        s = s.strip()
        if not s:
            toks.append(("blank", ""))
        elif s == "***":
            toks.append(("rule", ""))
        elif re.match(r"^[-*]\s+", s):
            toks.append(("bullet", re.sub(r"^[-*]\s+", "", s)))
        else:
            toks.append(("quote" if quoted else "text", s))
        i += 1
    return toks


def zh_is_blocky(zht):
    """True when the ZH text needs more than one paragraph."""
    toks = _zh_tokens(zht)
    if any(k in ("rule", "code", "bullet", "quote") for k, _ in toks):
        return True
    return sum(1 for k, _ in toks if k == "text") > 1


def zh_blocks(zht, pals=None):
    """Chinese prose as real blocks (paragraphs, lists, quotes, code) so no
    block element ever has to live inside a <p>."""
    if not zht:
        return ""
    toks, out, i = _zh_tokens(zht), [], 0
    while i < len(toks):
        kind, payload = toks[i]
        if kind == "blank":
            i += 1
        elif kind == "rule":
            out.append("<hr/>")
            i += 1
        elif kind == "code":
            out.append('<pre class="zh-pre">%s</pre>' % esc(payload))
            i += 1
        elif kind == "bullet":
            items = []
            while i < len(toks) and toks[i][0] == "bullet":
                items.append(toks[i][1])
                i += 1
            out.append('<ul class="zh-list">%s</ul>'
                       % "".join("<li>%s</li>" % zh_html(x, pals) for x in items))
        elif kind == "quote":
            items = []
            while i < len(toks) and toks[i][0] == "quote":
                items.append(toks[i][1])
                i += 1
            out.append('<blockquote class="zh-quote">%s</blockquote>'
                       % "".join('<p class="zh">%s</p>' % zh_html(x, pals) for x in items))
        else:
            items = []
            while i < len(toks) and toks[i][0] == "text":
                items.append(toks[i][1])
                i += 1
            out.append('<p class="zh" lang="zh-Hans">%s</p>' % zh_html(" ".join(items), pals))
    return "".join(out)


def _container(node, pal):
    """The block's own container: root tag plus its attributes stay."""
    inner = _inner(node, pal)
    if node.name in ("p", "span"):
        return inner, False
    if node.name == "center":
        return '<div class="dv-center-block" style="text-align:center">%s</div>' % inner, True
    return "<%s%s>%s</%s>" % (node.name, _attrs(node), inner, node.name), True


def render_body(seg, zht, pal):
    b = BeautifulSoup(seg["rich_html"], "html.parser").find()
    pals = quote_groups(b, pal)
    zen = zh_html(zht, pals if pals else None)
    sid = seg["id"]
    if b.name in ("p", "span") and not zh_is_blocky(zht):
        return ('<div class="pair" id="blk-%s"><p class="en" lang="en" id="en-%s">%s</p>'
                '<p class="zh" lang="zh-Hans" id="zh-%s">%s</p></div>'
                % (sid, sid, en_html(b, pal), sid, zen))
    en_inner, en_blocky = _container(b, pal)
    en = ('<div class="en" lang="en" id="en-%s">%s</div>' if en_blocky
          else '<p class="en" lang="en" id="en-%s">%s</p>') % (sid, en_inner)
    zh = ('<div class="zh-body" lang="zh-Hans" id="zh-%s">%s</div>'
          % (sid, zh_blocks(zht, pals if pals else None))
          if zh_is_blocky(zht) else '<p class="zh" lang="zh-Hans" id="zh-%s">%s</p>' % (sid, zen))
    return '<div class="pair" id="blk-%s">%s%s</div>' % (sid, en, zh)


FIG_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def figure_inscription(node, pal):
    """Text the author wrote beside a drawing instead of inside it.

    19-033 holds an envelope <svg> plus a <b>fe cau tie dai <br/> li shu</b>
    couplet in the same device block.  The drawing is an asset; the couplet is
    paragraph text that has to stay next to the picture in both editions.
    """
    parts = []
    for ch in node.children:
        if isinstance(ch, NavigableString):
            parts.append(esc(str(ch)))
        elif isinstance(ch, Tag):
            if ch.name in ("svg", "script", "style"):
                continue
            # _inner() gives children only: keep the author's own tag as well
            parts.append("<%s%s>%s</%s>" % (ch.name, _attrs(ch), _inner(ch, pal), ch.name))
    out = "".join(parts).strip()
    return out if BeautifulSoup(out, "html.parser").get_text("", strip=True) else ""


def zh_figure_inscription(zht):
    """The working translation's own lines for that couplet: image markdown and
    blank lines are markup, every remaining line is author text (never machine
    translated here)."""
    lines = [ln.strip() for ln in (zht or "").split("\n")
             if ln.strip() and not FIG_MD_IMAGE.search(ln)]
    return "<br/>".join(md_inline(x) for x in lines)


def render_figure(seg, zht, pal):
    fig = seg.get("figure") or {}
    en_src, zh_src = fig.get("en", ""), fig.get("zh", "")
    labels = " / ".join(seg.get("figure_labels") or [])
    alt = esc(labels) if labels else "figure"
    b = BeautifulSoup(seg["rich_html"], "html.parser").find()
    cap_en = figure_inscription(b, pal) if b is not None else ""
    cap_zh = zh_figure_inscription(zht)
    out = ['<div class="device wide" id="blk-%s"><figure class="dv-fig-wrap">' % seg["id"],
           '<img class="dv-fig" src="%s" alt="%s (English)"/>' % (en_src, alt)]
    if cap_en:
        out.append('<div class="dv-fig-cap dv-fig-cap-en" lang="en">%s</div>' % cap_en)
    out.append('<img class="dv-fig" src="%s" alt="%s (Chinese)"/>' % (zh_src, alt))
    if cap_zh:
        out.append('<div class="dv-fig-cap dv-fig-cap-zh" lang="zh-Hans">%s</div>' % cap_zh)
    out.append("</figure></div>")
    return "".join(out)

# ---------------------------------------------------------------- tables
def md_rows(text):
    """Markdown rows plus the non-table lines, both in document order."""
    rows, others = [], []
    for line in (text or "").split("\n"):
        s = line.strip()
        if not s or s == "***":
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r"-{2,}|:?-+:?", c) for c in cells if c) and any("-" in c for c in cells):
                continue
            rows.append(cells)
        else:
            others.append(s)
    return rows, others


def zh_table_groups(text):
    """One [rows, texts] pair per markdown table, in document order.

    A table ends at the next blank or non-table line, so a block holding two
    source tables carries two markdown tables; texts are the prose/button lines
    around them.
    """
    groups, rows, pending = [], [], []
    for line in (text or "").split("\n"):
        s = line.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r"-{2,}|:?-+:?", c) for c in cells if c) and any("-" in c for c in cells):
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


def row_groups(table):
    groups = []
    thead = table.find("thead")
    tbody = table.find("tbody")
    if thead:
        groups.append(("thead", thead.find_all("tr", recursive=False)))
    if tbody:
        groups.append(("tbody", tbody.find_all("tr", recursive=False)))
    else:
        groups.append(("tbody", [tr for tr in table.find_all("tr") if not thead or tr.parent is table]))
    return groups


def _zh_cells(zrow, cell, zi):
    """Chinese text for a DOM cell, consuming `colspan` markdown cells."""
    n = int(cell.get("colspan", 1) or 1)
    chunk = " ".join(x for x in zrow[zi:zi + n] if x.strip())
    return chunk, zi + n


def _num_class(txt):
    return ' class="num"' if re.fullmatch(r"[\d.,%+\-\u2013\u2014\s]+", txt or "") else ""


def _cell_style(cell):
    """Keep the source cell colour: these tables carry data in colour (air
    thresholds, verified marks).  Layout-only declarations stay in the CSS."""
    m = re.search(r"color\s*:\s*([^;\"']+)", cell.get("style") or "")
    return ' style="color:%s"' % esc(m.group(1).strip()) if m else ""


def table_html(table, rows, pal, extra=""):
    """One source <table> with its own Chinese cells: rows, colspan, colour."""
    out = ['<table class="dv"%s>' % extra]
    row_iter = iter(rows)
    for tag, trs_g in row_groups(table):
        if not trs_g:
            continue
        out.append("<%s>" % tag)
        for tr in trs_g:
            zrow = next(row_iter, [])
            zi = 0
            cells = []
            for c in tr.find_all(["th", "td"], recursive=False):
                zhchunk, zi = _zh_cells(zrow, c, zi)
                name = "th" if c.name == "th" else "td"
                merged = bi(_inner(c, pal), md_inline(zhchunk)) if zhchunk else _inner(c, pal)
                cells.append('<%s%s%s%s>%s</%s>' % (
                    name,
                    ' colspan="%s"' % c.get("colspan") if c.get("colspan") else "",
                    _num_class(c.get_text(" ", strip=True)),
                    _cell_style(c), merged, name))
            # the source sometimes nests a cell inside a cell (a button column);
            # its Chinese cells must still reach the row instead of vanishing
            rest = " ".join(x for x in zrow[zi:] if x.strip())
            if rest and cells:
                cells[-1] = cells[-1].replace("</%s>" % ("th" if cells[-1].startswith("<th") else "td"),
                                              '<div class="zh-line">%s</div></%s>'
                                              % (md_inline(rest), "th" if cells[-1].startswith("<th") else "td"))
            out.append("<tr>%s</tr>" % "".join(cells))
        out.append("</%s>" % tag)
    out.append("</table>")
    return "".join(out)


def _leftovers(root, tables):
    """Children beside the table: notes, prose, buttons (English side)."""
    keep = {id(t) for t in tables}
    out = []
    for ch in root.children:
        if isinstance(ch, Tag):
            if ch.name in ("script", "style", "table") or id(ch) in keep:
                continue
            if ch.find("table") is not None:      # wrapper around the table itself
                continue
            out.append(ch)
        elif isinstance(ch, NavigableString) and str(ch).strip():
            out.append(ch)
    return out


def footer_html(root, tables, texts, pal, skip_title=False):
    """Note / button / prose lines of a table block: EN and ZH in document
    order.  Every Chinese line is used; where the EN side has one block and the
    ZH side several sentences they are joined into that block."""
    kids = [k for k in _leftovers(root, tables)
            if not (isinstance(k, Tag) and skip_title and k.name == "h3")]
    units = []
    for k in kids:
        if isinstance(k, Tag) and k.name == "hr":
            continue
        if isinstance(k, Tag) and k.name == "button":
            units.append(("button", k))
            continue
        inner_tags = [x for x in k.children if isinstance(x, Tag)] if isinstance(k, Tag) else []
        if inner_tags and all(x.name in ("button", "br") for x in inner_tags) and any(
                x.name == "button" for x in inner_tags):
            units.extend(("button", x) for x in inner_tags if x.name == "button")
            continue
        units.append(("block", k))
    texts = [t for t in texts if t.strip()]
    if len(units) == 1 and len(texts) > 1:
        zh_for, rest = [" ".join(texts)], []
    else:
        zh_for = texts[:len(units)] + [""] * max(0, len(units) - len(texts))
        rest = texts[len(units):]
    parts = []
    for (kind, node), zh in zip(units, zh_for):
        if kind == "button":
            parts.append('<div class="dv-actions"><span class="dv-btn">%s</span></div>'
                         % bi(esc(node.get_text(" ", strip=True)), md_inline(zh)))
        elif isinstance(node, NavigableString):
            parts.append('<div class="dv-note">%s</div>' % bi(esc(str(node).strip()), md_inline(zh)))
        else:
            parts.append('<div class="dv-note">%s</div>' % bi(_inner(node, pal), md_inline(zh)))
    for t in rest:
        parts.append('<div class="dv-note dv-zh-prose">%s</div>' % md_inline(t))
    return "".join(parts)


def render_table(seg, zht, pal, report):
    """Return (html, notes).  Every source table and every Chinese cell kept."""
    b = BeautifulSoup(seg["rich_html"], "html.parser").find()
    tables = b.find_all("table")
    table = tables[0] if tables else None
    rows, others = md_rows(zht)
    notes = []
    cls = " ".join(b.get("class") or [])
    wide = "wide" if "wide" in cls else "narrow"
    sid = seg["id"]
    if table is None:
        return ('<div class="device %s" id="blk-%s"><div class="dv-zh">%s</div></div>'
                % (wide, sid, _inner(b, pal))), notes

    # -- block holding several tables: one ZH table each, same order ------
    if len(tables) > 1:
        groups = zh_table_groups(zht)
        if len(groups) != len(tables) or not all(g[0] for g in groups):
            report["unresolved"].append({
                "id": sid, "reason": "block has %d source tables but %d usable ZH tables"
                % (len(tables), sum(1 for g in groups if g[0]))})
            out = ['<div class="device %s" id="blk-%s">' % (wide, sid)]
            for i, t in enumerate(tables):
                out.append(table_html(t, [], pal, ' style="margin-top:10px"' if i else ""))
            out.append('<div class="dv-zh">%s</div>' % md_inline(zht))
            out.append("</div>")
            return "".join(out), notes
        out = ['<div class="device %s" id="blk-%s">' % (wide, sid)]
        texts = []
        for i, (t, grec) in enumerate(zip(tables, groups)):
            if len(grec[0]) != len(t.find_all("tr")):
                report["unresolved"].append({
                    "id": sid, "reason": "table %d: ZH rows %d != DOM rows %d"
                    % (i + 1, len(grec[0]), len(t.find_all("tr")))})
            out.append(table_html(t, grec[0], pal, ' style="margin-top:10px"' if i else ""))
            texts.extend(grec[1])
        out.append(footer_html(b, tables, texts, pal))
        out.append("</div>")
        return "".join(out), notes

    thead = table.find("thead")
    ncols = max((sum(int(c.get("colspan", 1) or 1) for c in tr.find_all(["th", "td"]))
                 for tr in table.find_all("tr")), default=0)

    # -- vote panel: single-column table with an input ------------------
    if table.find("input") and thead and ncols == 1:
        zn = [(r[0] if r else "") for r in rows]
        tds = table.find_all("td")
        th = table.find("th")
        btn = table.find("button")
        has_sum = bool(tds) and tds[0].find("input") is None
        need = 4 if has_sum else 3
        if len(zn) >= need:
            hd = th.get_text(" ", strip=True) if th else ""
            if has_sum:
                summ = tds[0].get_text(" ", strip=True)
                zh_sum, zh_scale, zh_btn = zn[1], zn[2], zn[3]
            else:
                summ, zh_sum, zh_scale, zh_btn = "", None, zn[1], zn[2]
            scale = zh_scale.split() if len(zh_scale.split()) >= 2 else ["-5", "0", "5"]
            body = ('<div class="dv-body">%s</div>' % bi(md_inline(summ), md_inline(zh_sum))
                    if has_sum and summ else "")
            return f'''<div class="device {wide}" id="blk-{sid}">
<div class="dv-title">{bi(esc(hd), md_inline(zn[0]))}</div>
{body}<div class="dv-slider">
  <div class="knob-wrap"><span class="knob"></span></div>
  <div class="track"></div>
  <table class="scale"><tr>{"".join("<td>%s</td>" % esc(x) for x in scale)}</tr></table>
</div>
<div class="dv-actions"><span class="dv-btn">{bi(esc(btn.get_text(" ", strip=True) if btn else ""), md_inline(zh_btn))}</span></div>
</div>''', notes
        report["unresolved"].append({
            "id": sid, "reason": "vote panel has %d ZH lines, needs %d" % (len(zn), need)})

    # -- message table: 4 columns with header, one row per message ------
    if thead and ncols == 4 and len(rows) >= 2:
        ths = [x.get_text(" ", strip=True) for x in thead.find_all("th")]
        zhhead = rows[0]
        tbody = table.find("tbody")
        body_trs = tbody.find_all("tr") if tbody else []
        out = [f'<div class="device wide" id="blk-{sid}">',
               '<table class="dv dv-msg">',
               '<colgroup><col style="width:7.5em"/><col style="width:2em"/><col/><col style="width:4.2em"/></colgroup>',
               "<thead><tr><th>%s</th><th></th><th>%s</th><th>%s</th></tr></thead>"
               % (bi(esc(ths[0]), md_inline(zhhead[0] if zhhead else "")),
                  bi(esc(ths[2]), md_inline(zhhead[2] if len(zhhead) > 2 else "")),
                  bi(esc(ths[3]), md_inline(zhhead[3] if len(zhhead) > 3 else ""))),
               "<tbody>"]
        for k, tr in enumerate(body_trs):
            tds = tr.find_all("td")
            zhrow = rows[k + 1] if len(rows) > k + 1 else []
            name = (tds[0].find(string=True) or "").strip() if tds else ""
            box = tds[0].find("div") if tds else None
            box_html = _inner(box, pal) if box else ""
            arrow = tds[1].get_text(" ", strip=True) if len(tds) > 1 else ""
            msg = _inner(tds[2], pal) if len(tds) > 2 else ""
            tm = tds[3].get_text(" ", strip=True) if len(tds) > 3 else ""
            if box is None:
                zhname = zhrow[0] if zhrow else ""
                zhbox = ""
            else:
                zhname, _, zhbox = zhrow[0].partition(" ") if zhrow else ("", "", "")
            out.append(
                '<tr><td class="dv-center"%s>%s<div class="repbox">%s</div></td>'
                '<td class="dv-center">%s</td>'
                '<td%s><span class="msgtext">%s</span><span class="msgtext zh-line">%s</span></td>'
                '<td class="num"%s>%s</td></tr>'
                % (_cell_style(tds[0]) if tds else "", bi(esc(name), md_inline(zhname)),
                   bi(box_html, md_inline(zhbox)), esc(arrow),
                   _cell_style(tds[2]) if len(tds) > 2 else "", msg,
                   md_inline(zhrow[2] if len(zhrow) > 2 else ""),
                   _cell_style(tds[3]) if len(tds) > 3 else "", esc(tm)))
        out.append("</tbody></table>")
        out.append(footer_html(b, tables, others, pal))
        out.append("</div>")
        return "".join(out), notes

    # -- basic summary: h3 + 3 columns ---------------------------------
    h3 = b.find("h3")
    if h3 and thead and ncols == 3 and len(rows) >= 2:
        ths = [x.get_text(" ", strip=True) for x in thead.find_all("th")]
        zhhead = rows[0]
        out = ['<div class="device wide" id="blk-%s">' % sid]
        out.append('<h3 class="dv-h3">%s</h3>' % bi(esc(h3.get_text(" ", strip=True)),
                                                     md_inline(others[0] if others else "")))
        out.append('<table class="dv dv-sum"><colgroup><col style="width:4em"/><col/><col style="width:6em"/></colgroup>')
        out.append("<thead><tr>%s</tr></thead>" % "".join(
            '<th%s>%s</th>' % (_num_class(ths[j]), bi(esc(ths[j]), md_inline(zhhead[j] if len(zhhead) > j else "")))
            for j in range(3)))
        out.append("<tbody>")
        for k, tr in enumerate(table.find("tbody").find_all("tr")):
            tds = tr.find_all("td")
            zhrow = rows[k + 1] if len(rows) > k + 1 else ["", "", ""]
            zh_tier, zh_crit, zh_rate = (zhrow + ["", "", ""])[:3]
            lis = tds[1].find_all("li") if tds[1].find("ul") else []
            crit_en = ('<ul class="dv-list">%s</ul>' % "".join("<li>%s</li>" % _inner(li, pal) for li in lis)) \
                if lis else _inner(tds[1], pal)
            # sentence split that keeps both separators ("。" and "且")
            crit_zh = "".join("<li>%s</li>" % md_inline(x) for x in re.split(r"(?<=。)(?=且)", zh_crit)) \
                if lis else md_inline(zh_crit)
            crit_zh = '<ul class="dv-list">%s</ul>' % crit_zh if lis else crit_zh
            out.append('<tr><td class="num">%s</td><td>%s<div class="zh-line">%s</div></td><td class="num">%s</td></tr>'
                       % (bi(md_inline(_inner(tds[0], pal)), md_inline(zh_tier)), crit_en, crit_zh,
                          bi(md_inline(_inner(tds[2], pal)), md_inline(zh_rate))))
        out.append("</tbody></table>")
        out.append(footer_html(b, tables, others[1:], pal, skip_title=True))
        out.append("</div>")
        return "".join(out), notes

    # -- tax table: 2 columns with header ------------------------------
    if thead and ncols == 2 and len(rows) >= 2:
        hdr_cells, zi = [], 0
        for c in thead.find_all("tr")[0].find_all(["th", "td"]):
            zhchunk, zi = _zh_cells(rows[0] if rows else [], c, zi)
            hdr_cells.append('<th%s%s>%s</th>' % (
                ' colspan="%s"' % c.get("colspan") if c.get("colspan") else "",
                _num_class(c.get_text(" ", strip=True)),
                bi(esc(c.get_text(" ", strip=True)), md_inline(zhchunk))))
        out = ['<div class="device narrow" id="blk-%s">' % sid]
        body = ["<tr>%s</tr>" % "".join(hdr_cells)]
        for k, tr in enumerate(table.find("tbody").find_all("tr")):
            tds = tr.find_all("td")
            zhrow = rows[k + 1] if len(rows) > k + 1 else []
            body.append('<tr><td%s>%s</td><td class="num">%s</td></tr>' % (
                _cell_style(tds[0]),
                bi(_inner(tds[0], pal), md_inline(zhrow[0] if zhrow else "")),
                bi(esc(tds[1].get_text(" ", strip=True)), md_inline(zhrow[1] if len(zhrow) > 1 else ""))))
        out.append('<table class="dv dv-tax"><colgroup><col/><col style="width:5em"/></colgroup>'
                   "<tbody>%s</tbody></table>" % "".join(body))
        out.append(footer_html(b, tables, others, pal, skip_title=True))
        out.append("</div>")
        return "".join(out), notes

    # -- generic table: keep DOM rows/colspan, merge ZH per cell -------
    trs = table.find_all("tr")
    if len(rows) != len(trs):
        report["unresolved"].append({
            "id": sid, "reason": "ZH table rows %d != DOM rows %d" % (len(rows), len(trs))})
    out = ['<div class="device %s" id="blk-%s">' % (wide, sid)]
    if h3:
        out.append('<h3 class="dv-h3">%s</h3>' % bi(esc(h3.get_text(" ", strip=True)),
                                                     md_inline(others[0] if others else "")))
    out.append(table_html(table, rows, pal))
    out.append(footer_html(b, tables, others[1:] if h3 else others, pal, skip_title=True))
    out.append("</div>")
    return "".join(out), notes


def render_segment(seg, zht, pal, report):
    typ = seg["type"]
    b = BeautifulSoup(seg["rich_html"], "html.parser").find()
    if typ == "heading":
        return render_heading(seg, zht, pal)
    if typ == "dateline":
        return render_dateline(seg, zht, pal)
    if typ == "break":
        return render_break(seg, zht, pal)
    if typ == "quote":
        return render_quote(seg, zht, pal)
    if seg.get("device") == "figure":
        return render_figure(seg, zht, pal)
    if seg.get("device") == "table":
        try:
            html, notes = render_table(seg, zht, pal, report)
            report["fallbacks"].extend(notes)
            return html
        except Exception as e:  # keep the block visible, never drop it
            report["unresolved"].append({"id": seg["id"], "reason": "table render failed: %r" % e})
            return ('<div class="device wide" id="blk-%s"><div class="dv-fallback">%s</div>'
                    '<div class="dv-zh">%s</div></div>' % (seg["id"], _inner(b, pal), md_inline(zht)))
    if typ == "body" and (b is None or b.name != "blockquote"):
        return render_body(seg, zht, pal)
    if b is not None and b.name == "blockquote":
        return render_quote(seg, zht, pal)
    return '<div class="device wide" id="blk-%s">%s<div class="dv-zh">%s</div></div>' % (
        seg["id"], _inner(b, pal), md_inline(zht))
