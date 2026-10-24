#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Full-book merge entry for the accepted per-chapter bilingual builder.

    uv run python book/build_full.py --chapters-root work/chapters --output output
Use --chapters 1,5,9 for a subset; the default scope is the full 1-32.

It does not re-implement the chapter pipeline.  Each chapter is rendered by the
accepted ``render.py`` (same DOM, tables, colspan, speaker colours, device
panels) and its figures are copied through ``image_layout.py`` (root layout
declarations removed, artboard re-inspected *after* the copy).  This file adds
only what a single merged book needs:

  * one global speaker palette, so a colour pair maps to exactly one ``.sN``
    across all chapters (no cross-chapter colour clash, no duplicate class);
  * an ``id`` namespace per chapter (``c01-blk-01-002``), so ids stay unique in
    one XML document set;
  * a per-chapter asset directory (``assets/ch01/``), so identical basenames
    from different chapters can never overwrite each other;
  * one OPF/NAV/EPUB with real chapter order, plus per-chapter checked HTML.

Deliberately absent: no JavaScript, buttons or input controls reach the output
(render.py turns them into static spans); no absolute local paths and no
internal orchestration text in the book; no visual/EPUBCheck claim.

Exit status is 0 only when the requested scope was built completely: every
requested chapter present, ids unique, every figure verified after copy, every
asset reference resolvable, XML well-formed, and (unless --html-only) Calibre
returned 0 and produced a zip whose spine order, member set, text and author
metadata were read back.
"""
import argparse, ctypes, datetime, hashlib, html as htmlmod, json, os, re, shutil
import subprocess, sys, zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
AUTHOR = "Vitalik Buterin"
TRANSLATION_REPO = "https://github.com/XiaoHai67890/snowmoon-zh"
TRANSLATION_COMMIT = "55f9a20b383ee124eb3eeffd137844d4b45990d6"
SITE = "https://vitalik.eth.limo/snowmoon/"
SNAPSHOT = "20261023T101827Z"
SNAPSHOT_DATE = "2026-10-23"
FULL_RANGE = list(range(1, 33))
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"


# --------------------------------------------------------------------- imports
def load_engine(code_root):
    """Use the accepted render.py / image_layout.py unchanged (read-only)."""
    code_root = os.path.abspath(code_root)
    missing = [f for f in ("render.py", "style_base.css",
                           os.path.join("checks", "image_layout.py"))
               if not os.path.exists(os.path.join(code_root, f))]
    if missing:
        raise SystemExit("engine files missing in %s: %s" % (code_root, missing))
    sys.path.insert(0, code_root)
    sys.path.insert(0, os.path.join(code_root, "checks"))
    import render as R
    import image_layout as IL
    return R, IL


# ----------------------------------------------------------------------- input
def parse_chapters(spec):
    if not spec:
        return list(FULL_RANGE), True
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if re.fullmatch(r"\d+-\d+", part):
            a, b = (int(x) for x in part.split("-"))
            if b < a:
                raise SystemExit("bad chapter range: %s" % part)
            out.extend(range(a, b + 1))
        elif re.fullmatch(r"\d+", part):
            out.append(int(part))
        else:
            raise SystemExit("bad --chapters entry: %r" % part)
    out = sorted(set(out))
    if not out:
        raise SystemExit("--chapters selected nothing")
    return out, False


def load_chapter(root, n):
    d = os.path.join(root, "%02d" % n)
    src_p, zh_p = os.path.join(d, "source.json"), os.path.join(d, "zh.json")
    if not os.path.exists(src_p):
        return None, "missing %s" % os.path.basename(src_p)
    if not os.path.exists(zh_p):
        return None, "missing %s" % os.path.basename(zh_p)
    src = json.load(open(src_p, encoding="utf-8"))
    zh = json.load(open(zh_p, encoding="utf-8"))
    if int(src.get("chapter") or 0) != n:
        return None, "source.json says chapter %r, directory is %02d" % (src.get("chapter"), n)
    ids = [s["id"] for s in src["segments"]]
    zids = [s["id"] for s in zh["segments"]]
    if ids != zids:
        miss = [i for i in ids if i not in set(zids)]
        extra = [i for i in zids if i not in set(ids)]
        return None, "segment id order mismatch (missing %s, extra %s)" % (miss[:5], extra[:5])
    for sid in ids:
        if not sid.startswith("%02d-" % n):
            return None, "segment id %s is not namespaced to chapter %02d" % (sid, n)
    if len(set(ids)) != len(ids):
        return None, "duplicate segment ids in source.json"
    align_p = os.path.join(d, "alignment.json")
    align = None
    if os.path.exists(align_p):
        try:
            align = json.load(open(align_p, encoding="utf-8"))
        except Exception as e:
            return None, "alignment.json unreadable: %r" % e
        verdict = ((align.get("mapping") or {}).get("verdict") or "").lower()
        unres = align.get("unresolved") or []
        if unres:
            return None, "alignment has %d unresolved mapping(s)" % len(unres)
        if verdict != "verified":
            return None, "alignment verdict is %r, not 'verified'" % verdict
    return {"n": n, "dir": d, "src": src, "zh": zh, "zmap":
            {s["id"]: s["zh"] for s in zh["segments"]}, "align": align,
            "align_gate": "absent" if align is None else "verified"}, None


def revision_state(R, ch):
    """Reuse the accepted per-chapter status logic (no reinterpretation)."""
    zh, d = ch["zh"], ch["dir"]
    rev = zh.get("revision")
    if not isinstance(rev, dict):
        p = os.path.join(d, "revision.json")
        if os.path.exists(p):
            try:
                rev = json.load(open(p, encoding="utf-8"))
            except Exception:
                rev = None
    if not isinstance(rev, dict):
        return {"reviewed": False, "revised": False}
    revised = bool(rev.get("revised"))
    reviewed = bool(rev.get("reviewed") or revised)
    if not reviewed:
        return {"reviewed": False, "revised": False}
    sc = rev.get("scope_counts") or {}
    body = int(sc.get("body") or (rev.get("edits") or 0))
    note = int(sc.get("note") or 0)
    meta = int(sc.get("svg_metadata") or 0)
    return {"reviewed": True, "revised": revised,
            "edits": body if revised else 0,
            "scopes": {"body": body if revised else 0,
                       "note": note if revised else 0,
                       "svg_metadata": meta if revised else 0},
            "targets": rev.get("reviewed_targets") or rev.get("reviewed_items")
            or rev.get("items_reviewed")}


# -------------------------------------------------------------------- rendering
FIG_TOKEN = "@@FIG:%s@@"


def ns_ids(fragment, n):
    prefix = "c%02d-" % n
    return re.sub(r'\bid="([^"]+)"', lambda m: 'id="%s%s"' % (prefix, m.group(1)), fragment)


def render_chapter(R, IL, ch, out, pal, report):
    """Render body + figures for one chapter; return body html with FIG tokens."""
    n, nn, src, zmap = ch["n"], "%02d" % ch["n"], ch["src"], ch["zmap"]
    parts = []
    for seg in src["segments"]:
        zht = zmap.get(seg["id"], "")
        parts.append(R.render_segment(seg, zht, pal, report))
    body = "\n".join(parts)

    asset_dir = os.path.join(out, "assets", "ch%s" % nn)
    os.makedirs(asset_dir, exist_ok=True)
    figs = []
    for fig in src.get("figures") or []:
        fid = fig.get("segment_id") or "*"
        entry = {"id": fid, "file": fig.get("file"), "en": None, "zh": None,
                 "root_style_adapted": {}, "layout": []}
        for key in ("en", "zh"):
            rel = fig.get(key)
            if not rel:
                report["unresolved"].append({"chapter": n, "id": fid,
                                             "reason": "figure path not declared (%s) for %s" % (key, fig.get("file"))})
                continue
            srcp = os.path.join(ch["dir"], rel.replace("/", os.sep))
            base = os.path.basename(rel)
            if not os.path.exists(srcp):
                report["unresolved"].append({"chapter": n, "id": fid,
                                             "reason": "figure asset missing: %s" % rel})
                continue
            # output copy: only root-level layout declarations are dropped
            data, removed = IL.strip_root_layout_style(open(srcp, "rb").read())
            dst = os.path.join(asset_dir, base)
            with open(dst, "wb") as fh:
                fh.write(data)
            if removed:
                entry["root_style_adapted"][key] = removed
            # gate the COPY, not the source: viewBox/root style/artboard
            insp = IL.inspect_svg(dst)
            insp.update({"lang": key, "figure_id": fid, "copied_rel": "assets/ch%s/%s" % (nn, base)})
            entry["layout"].append(insp)
            entry[key] = base
            body = body.replace('src="%s"' % rel, 'src="%s"' % (FIG_TOKEN % ("ch%s/%s" % (nn, base))))
        figs.append(entry)
    ch["figures"] = figs
    ch["body"] = ns_ids(body, n)
    ch["rendered_segments"] = len(parts)
    return ch


# ------------------------------------------------------------------ page shells
def chapter_xhtml(R, ch, w, title):
    n = ch["n"]
    head = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="%s" xmlns:epub="%s" xml:lang="en" lang="en">\n<head>\n'
            '<meta charset="utf-8"/>\n<title>%s</title>\n'
            '<link rel="stylesheet" type="text/css" href="%s"/>\n</head>\n'
            '<body>\n<div class="page" id="c%02d-page">\n' % (XHTML_NS, EPUB_NS, R.esc(title), w["css"], n))
    pre, post = FIG_TOKEN.split("%s")
    body = re.sub(re.escape(pre) + r"([^@]+)" + re.escape(post),
                  lambda m: w["fig"] + m.group(1), ch["body"])
    tail = "\n</div>\n</body>\n</html>\n"
    return head + body + tail


# ------------------------------------------------------- front matter (扉页)
# The author's three closing declarations, transcribed verbatim from
# https://vitalik.eth.limo/snowmoon/ (checked 2026-10-25).  Pinned here on
# purpose: the build must never depend on a live fetch, and the English wording
# is the author's own, so it is copied rather than paraphrased.  Each English
# paragraph is followed by a full Chinese translation of that same paragraph.
GPL_URL = "https://www.gnu.org/licenses/gpl-3.0.html"
TRANSLATORS = (("XiaoHai67890", "https://github.com/XiaoHai67890"),
               ("henrycyberbio", "https://github.com/henrycyberbio"))
DECLARATIONS = (
    ("License:", "许可", (
        ('Snowmoon is released under the <a href="%s">GPL v3</a>' % GPL_URL,
         '《雪月》以 <a href="%s">GPL v3</a> 许可发布。' % GPL_URL),
        ("Yes, I said GPL v3, not CC-BY-SA. My legal theory, which Kimi K3 says is "
         "plausible, is that you are free to go turn it into a movie or a vibe-coded "
         "anime or whatever, but if you do that, you are required to open-source the "
         "pipeline (AI prompts, scripts, task-specific harness, etc) and other "
         "non-commodity materials that you used to make it so that other people can "
         "build on top of your work.",
         "是的，我说的是 GPL v3，不是 CC-BY-SA。我的法律理论——Kimi K3 认为它说得通——是："
         "你可以放手把它改成电影、改成一部用 vibe coding 制作的动画，或者随便什么别的东西。"
         "但如果你真这么做了，就必须把制作它所用的流水线（AI 提示词、脚本、针对具体任务的 "
         "harness 等）以及其他非通用的制作材料开源出来，好让别人能在你的成果之上继续搭建。"),
    )),
    ("AI usage declaration:", "AI 使用声明", (
        ("All words were written directly by me.",
         "书中所有文字都由我本人直接写成。"),
        ("Spelling, grammar and style checking, verifying consistency of the rules of "
         "Minpentai and Dzegoban, the HTML and CSS format and style, and the SVGs were "
         "done with assistance from Kimi K3 and Qwen 3.8 Flash Next.",
         "拼写、语法和文风检查，Minpentai 与 Dzegoban 规则一致性的核验，HTML 和 CSS 的格式与"
         "样式，以及所有 SVG，都是在 Kimi K3 和 Qwen 3.8 Flash Next 的协助下完成的。"),
    )),
    # the author's own love/AI wordplay: the label really is "爱 usage declaration:"
    ("爱 usage declaration:", "爱 使用声明", (
        ("Snowmoon was written with love.",
         "《雪月》是带着爱写成的。"),
    )),
)


def front_xhtml(R, title, css, scope_note=""):
    translators = "、".join('<a href="%s">%s</a>' % (url, R.esc(name))
                            for name, url in TRANSLATORS)
    blocks = []
    for label_en, label_zh, paras in DECLARATIONS:
        blocks.append('<h3 class="bm-title"><span lang="en" xml:lang="en">%s</span> '
                      '<span lang="zh-Hans" xml:lang="zh-Hans">%s</span></h3>'
                      % (R.esc(label_en), R.esc(label_zh)))
        for en, zh in paras:
            blocks.append('<p class="bm-p" lang="en" xml:lang="en">%s</p>' % en)
            blocks.append('<p class="bm-p" lang="zh-Hans" xml:lang="zh-Hans">%s</p>' % zh)
    note = '<p class="bm-p">%s</p>' % R.esc(scope_note) if scope_note else ""
    page = ('<div class="page" id="c-title">'
            '<h1 class="ch-title"><span class="t-en" lang="en" xml:lang="en">Snowmoon</span> '
            '<span class="t-zh" lang="zh-Hans" xml:lang="zh-Hans">雪月</span></h1>'
            '<p class="bm-p">作者：Vitalik Buterin</p>'
            '<p class="bm-p">译者：%s</p>'
            '<p class="bm-p">License: <a href="%s">GPL v3</a></p>%s'
            '<h2 class="bm-title"><span lang="zh-Hans" xml:lang="zh-Hans">原作者声明</span>'
            ' / <span lang="en" xml:lang="en">Original author\'s statements</span></h2>'
            % (translators, GPL_URL, note)
            + "".join(blocks) + '</div>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="%s" xmlns:epub="%s" xml:lang="zh-Hans" lang="zh-Hans">\n<head>\n'
            '<meta charset="utf-8"/>\n<title>%s</title>\n'
            '<link rel="stylesheet" type="text/css" href="%s"/>\n</head>\n<body>\n'
            '%s\n</body>\n</html>\n'
            % (XHTML_NS, EPUB_NS, R.esc(title), css, page))


def notes_xhtml(R, chapters, title, css):
    secs = []
    for ch in chapters:
        n, rev = ch["n"], ch["rev"]
        zh_title = ""
        if ch["src"]["segments"] and ch["src"]["segments"][0]["type"] == "heading":
            zh_title = ch["zmap"].get(ch["src"]["segments"][0]["id"], "")
        if rev.get("revised"):
            sc = rev.get("scopes") or {}
            parts = []
            if sc.get("body"):
                parts.append("正文 %d 段" % sc["body"])
            if sc.get("note"):
                parts.append("译注 %d 条" % sc["note"])
            if sc.get("svg_metadata"):
                parts.append("图表无障碍文字 %d 处" % sc["svg_metadata"])
            if parts:
                state = "本章疑点经 AI 校对，最小修改：%s（其余保留原译）。" % "、".join(parts)
            else:
                state = "本章疑点经 AI 校对，并对正文作了 %s 处最小修改。" % (
                    rev.get("edits") if rev.get("edits") is not None else "若干")
        elif rev.get("reviewed"):
            state = "本章疑点经 AI 校对，保留原译（0 处修改）。"
        else:
            state = "本章中文为既有译本，未作专项校对记录。"
        items = "".join('<li><b>%s</b>：%s</li>' % (R.esc(x["id"]), R.md_inline(x["note"]))
                        for x in (ch["zh"].get("notes") or []))
        secs.append('<section class="notes-ch" id="c%02d-notes"><h3 class="bm-title">第 %d 章 · %s</h3>'
                    '<p class="bm-p">%s</p>%s</section>'
                    % (n, n, R.md_inline(zh_title) if zh_title else "", R.esc(state),
                       ('<ul class="bm-notes">%s</ul>' % items) if items else ""))
    src = ('<p class="bm-p">原作：Vitalik Buterin，<i>Snowmoon</i>，'
           '<a href="https://vitalik.eth.limo/snowmoon/">vitalik.eth.limo/snowmoon</a>，'
           '作者声明以 GPL v3 发布。</p>'
           '<p class="bm-p">中译来源：<a href="%s">XiaoHai67890/snowmoon-zh</a> @ %s，原译校：Codex（AI）。</p>'
           '<p class="bm-p">英文快照：%s（快照 ID %s）。排版与构建：Hermes Agent 构建脚本；'
           'EPUB 转换：Calibre 9.9.0。</p>'
           % (TRANSLATION_REPO, TRANSLATION_COMMIT, SNAPSHOT_DATE, SNAPSHOT))
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="%s" xmlns:epub="%s" xml:lang="zh-Hans" lang="zh-Hans">\n<head>\n'
            '<meta charset="utf-8"/>\n<title>译注与来源</title>\n'
            '<link rel="stylesheet" type="text/css" href="%s"/>\n</head>\n<body>\n'
            '<div class="page" id="c-notes"><h2 class="bm-title">译注与来源 · Notes &amp; Source</h2>\n%s\n%s\n</div>\n'
            '</body>\n</html>\n' % (XHTML_NS, EPUB_NS, css, src, "\n".join(secs)))


def nav_xhtml(R, chapters, title):
    items = []
    for ch in chapters:
        items.append('<li><a href="text/ch%02d.xhtml">%d · %s</a></li>'
                     % (ch["n"], ch["n"], R.esc(ch["title_nav"])))
    items.append('<li><a href="text/notes.xhtml">译注与来源</a></li>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="%s" xmlns:epub="%s" xml:lang="zh-Hans" lang="zh-Hans">\n<head>\n'
            '<meta charset="utf-8"/>\n<title>目录</title>\n'
            '<link rel="stylesheet" type="text/css" href="style.css"/>\n</head>\n<body>\n'
            '<nav epub:type="toc" id="toc"><h1 class="bm-title">目录</h1>\n<ol class="toc">\n%s\n</ol>\n'
            '</nav>\n<nav epub:type="landmarks" id="landmarks" hidden="hidden"><ol>\n'
            '<li><a epub:type="bodymatter" href="text/ch%02d.xhtml">正文</a></li>\n</ol></nav>\n'
            '</body>\n</html>\n' % (XHTML_NS, EPUB_NS, "\n".join(items), chapters[0]["n"]))


def opf_xml(R, chapters, title, uid, modified, images, subset):
    lang = "zh-Hans" if subset else "en"
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="css" href="style.css" media-type="text/css"/>',
                '<item id="front" href="text/front.xhtml" media-type="application/xhtml+xml"/>']
    spine = ['<itemref idref="front"/>']
    for ch in chapters:
        n = ch["n"]
        manifest.append('<item id="ch%02d" href="text/ch%02d.xhtml" media-type="application/xhtml+xml"/>' % (n, n))
        spine.append('<itemref idref="ch%02d"/>' % n)
    manifest.append('<item id="notes" href="text/notes.xhtml" media-type="application/xhtml+xml"/>')
    spine.append('<itemref idref="notes"/>')
    for idx, img in enumerate(sorted(images)):
        manifest.append('<item id="img%d" href="%s" media-type="image/svg+xml"/>' % (idx, img))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="bookid" xml:lang="%s">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            '    <dc:identifier id="bookid">%s</dc:identifier>\n'
            '    <dc:title id="title">%s</dc:title>\n'
            '    <dc:creator id="author">%s</dc:creator>\n'
            '    <meta refines="#author" property="role" scheme="marc:relators">aut</meta>\n'
            '    <dc:contributor id="translator">中译：XiaoHai67890/snowmoon-zh（原译校 Codex, AI）</dc:contributor>\n'
            '    <dc:language>en</dc:language>\n'
            '    <dc:language>zh-Hans</dc:language>\n'
            '    <dc:source>%s</dc:source>\n'
            '    <dc:rights>原作以 GPL v3 发布</dc:rights>\n'
            '    <meta property="dcterms:modified">%s</meta>\n'
            '  </metadata>\n'
            '  <manifest>\n    %s\n  </manifest>\n'
            '  <spine>\n    %s\n  </spine>\n'
            '</package>\n'
            % (lang, uid, R.esc(title), AUTHOR, SITE, modified,
               "\n    ".join(manifest), "\n    ".join(spine)))


CONTAINER = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
             '  <rootfiles><rootfile full-path="OEBPS/content.opf" '
             'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n')


# --------------------------------------------------------------------- checking
def ids_and_refs(doc):
    ids = re.findall(r'\bid="([^"]+)"', doc)
    refs = re.findall(r'(?:src|href)="([^"]+)"', doc)
    return ids, refs


def text_of(xhtml):
    body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", xhtml)
    body = re.sub(r"(?is)<[^>]+>", " ", body)
    body = htmlmod.unescape(body)
    return re.sub(r"\s+", " ", body).strip()


def colour_map(css):
    """class -> colour for the simple `.cls { ... color:#hex ... }` rules."""
    out = {}
    for m in re.finditer(r"\.([A-Za-z][\w-]*)\s*\{[^}]*?color:\s*(#[0-9a-fA-F]{6})[^}]*\}", css):
        out.setdefault(m.group(1), m.group(2).lower())
    return out


def speaker_colours(doc, cmap):
    """Ordered colour of every `span.s*` (Calibre renames .s1 to .s, so never
    compare class names across the conversion, only the resolved colours)."""
    out = []
    for cl in re.findall(r'<span class="([^"]+)"', doc):
        for c in cl.split():
            if re.fullmatch(r"s\d*", c):
                out.append(cmap.get(c, "?"))
    return out


def mem_available_mb():
    try:
        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(m)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullAvailPhys // (1024 * 1024)
    except Exception:
        return None


def palette_audit(css, docs):
    defs = re.findall(r"\.s(\d+)\s*\{\s*color:\s*(#[0-9a-fA-F]{6})\s*;\s*\}", css)
    by_class = {}
    for k, col in defs:
        by_class.setdefault("s" + k, set()).add(col.lower())
    dup_class = {k: sorted(v) for k, v in by_class.items() if len(v) > 1}
    colors = [list(v)[0] for v in by_class.values()]
    dup_color = len(colors) != len(set(colors))
    used = set()
    for d in docs:
        for cl in re.findall(r'class="([^"]*)"', d):
            used |= set(cl.split())
    used_s = {c for c in used if re.fullmatch(r"s\d+", c)}
    missing = sorted(used_s - set(by_class), key=lambda x: int(x[1:]))
    return {"classes": len(by_class), "duplicate_class_with_different_colour": dup_class,
            "same_colour_on_two_classes": dup_color,
            "classes_used_without_definition": missing,
            "ok": not dup_class and not dup_color and not missing and bool(by_class)}


def scan_forbidden(docs):
    """Static EPUB must carry no controls, no local paths, no internal workflow."""
    hits = {}
    for name, d in docs.items():
        c = {k: len(re.findall(k, d)) for k in
             (r"<script\b", r"<input\b", r"<button\b", r"onclick=", r"javascript:")}
        if any(c.values()):
            hits[name] = c
    path_like = []
    for name, d in docs.items():
        for m in re.findall(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?!/)[^\"'<> ]{3,}", d):
            path_like.append({"doc": name, "match": m[:40]})
    boiler = " ".join(d for k, d in docs.items()
                      if "front" in k or "notes" in k or k == "index.html")
    return {"control_tags": hits, "absolute_local_paths": path_like,
            "workflow_words": [w for w in ("子代理", "subagent", "workflow", "hermes",
                                           "cache/scratch", "screening", "judge.py")
                               if w.lower() in boiler.lower()]}


# ----------------------------------------------------------------------- build
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapters-root", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--chapters", default=None, help="e.g. 1,5,9 or 1-32 (default 1-32)")
    ap.add_argument("--tmpdir", default=None)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--html-only", action="store_true")
    ap.add_argument("--code-root", default=HERE,
                    help="directory holding render.py/style_base.css/checks (default: this dir)")
    args = ap.parse_args()

    R, IL = load_engine(args.code_root)
    root = os.path.abspath(args.chapters_root)
    out = os.path.abspath(args.output)
    want, default_scope = parse_chapters(args.chapters)
    subset = want != FULL_RANGE
    tmpdir = os.path.abspath(args.tmpdir or os.path.join(out, "build", "tmp"))
    os.makedirs(out, exist_ok=True)
    os.makedirs(tmpdir, exist_ok=True)
    os.makedirs(os.path.join(out, "build"), exist_ok=True)

    report = {"generated_at": datetime.datetime.now(datetime.timezone.utc)
              .strftime("%Y-%m-%dT%H:%M:%SZ"),
              "chapters_root": root, "output": out,
              "requested_chapters": want, "scope": "subset" if subset else "full",
              "scope_declared_by_default": default_scope,
              "fallbacks": [], "unresolved": [], "chapters": [],
              "figures": [], "commands": [], "checks": {}}

    chapters, bad = [], []
    for n in want:
        ch, err = load_chapter(root, n)
        if err:
            bad.append({"chapter": n, "reason": err})
            report["unresolved"].append({"chapter": n, "id": "*", "reason": err})
        else:
            chapters.append(ch)
    report["missing_or_invalid"] = bad
    report["expected_segments"] = sum(len(c["src"]["segments"]) for c in chapters)

    if not chapters:
        report["checks"]["aborted"] = "no usable chapter input"
        finish(report, out, 1, "no usable chapter input")
        return 1

    # one palette for the whole book: a colour pair -> exactly one .sN
    pal = R.Palette()
    for ch in chapters:
        render_chapter(R, IL, ch, out, pal, report)
        ch["rev"] = revision_state(R, ch)
        title_en, title_zh = "", ""
        segs = ch["src"]["segments"]
        if segs and segs[0]["type"] == "heading":
            import bs4
            b = bs4.BeautifulSoup(segs[0]["rich_html"], "html.parser").find()
            title_en = b.get_text(" ", strip=True) if b else ""
            title_zh = ch["zmap"].get(segs[0]["id"], "")
        ch["title_en"], ch["title_zh"] = title_en, title_zh
        ch["title_nav"] = ("%s · %s" % (title_en, title_zh)) if title_zh else title_en
        report["chapters"].append({
            "chapter": ch["n"], "segments": len(segs), "rendered": ch["rendered_segments"],
            "zh_chars": sum(len(v) for v in ch["zmap"].values()),
            "body_text_chars": len(text_of(ch["body"])),
            "tables": ch["body"].count("<table"),
            "figures": len(ch["figures"]),
            "speaker_classes": sorted({int(x) for x in re.findall(r'class="s(\d+)"', ch["body"])}),
            "revision": ch["rev"], "alignment_gate": ch["align_gate"],
            "title": title_en,
        })

    # ---- book-level style (base + global palette) -----------------------
    base_css = open(os.path.join(args.code_root, "style_base.css"), encoding="utf-8").read()
    css = base_css + "\n/* speaker colours: one global palette for the whole book */\n" + pal.css() + "\n"
    os.makedirs(os.path.join(out, "assets"), exist_ok=True)
    open(os.path.join(out, "assets", "style.css"), "w", encoding="utf-8").write(css)

    label = ("完整版" if not subset else "试跑子集：第 %s 章" %
             "、".join(str(c["n"]) for c in chapters))
    title = (("Snowmoon · 英中对照（第 %d–%d 章）" % (chapters[0]["n"], chapters[-1]["n"]))
             if not subset else
             "Snowmoon · 英中对照（试跑子集：第 %s 章）" % "、".join(str(c["n"]) for c in chapters))
    stem = ("snowmoon-bilingual-full-ch01-32" if not subset else
            "snowmoon-bilingual-subset-" + "-".join("ch%02d" % c["n"] for c in chapters))

    # ---- per-chapter HTML pages (checked input for the merge) -----------
    hdir = os.path.join(out, "html")
    os.makedirs(hdir, exist_ok=True)
    html_files = {}
    for ch in chapters:
        doc = chapter_xhtml(R, ch, {"css": "../assets/style.css", "fig": "../assets/"}, ch["title_nav"])
        p = os.path.join(hdir, "chapter-%02d.html" % ch["n"])
        open(p, "w", encoding="utf-8").write(doc)
        html_files[ch["n"]] = doc
    index_items = "".join('<li><a href="chapter-%02d.html">第 %d 章 · %s</a></li>'
                          % (c["n"], c["n"], R.esc(c["title_en"])) for c in chapters)
    index_doc = ('<!DOCTYPE html>\n<html xmlns="%s" xml:lang="zh-Hans" lang="zh-Hans">\n<head>\n'
                 '<meta charset="utf-8"/>\n<title>%s</title>\n'
                 '<link rel="stylesheet" type="text/css" href="../assets/style.css"/>\n</head>\n'
                 '<body>\n<div class="page" id="book-top"><h1 class="ch-title">Snowmoon</h1>\n'
                 '<p class="bm-p">%s</p>\n<ul>%s</ul></div>\n</body>\n</html>\n'
                 % (XHTML_NS, R.esc(title), R.esc(label), index_items))
    open(os.path.join(hdir, "index.html"), "w", encoding="utf-8").write(index_doc)

    # ---- html-side gates ------------------------------------------------
    all_ids, per_doc_ids, ref_problems = {}, {}, []
    for p in sorted(os.listdir(hdir)):
        if not p.endswith(".html"):
            continue
        d = open(os.path.join(hdir, p), encoding="utf-8").read()
        ids, refs = ids_and_refs(d)
        per_doc_ids[p] = ids
        for i in ids:
            all_ids.setdefault(i, []).append(p)
        for r in refs:
            if re.match(r"^[a-z]+:", r) or r.startswith("#"):
                continue
            target = os.path.normpath(os.path.join(hdir, r))
            if not os.path.exists(target):
                ref_problems.append({"page": p, "ref": r})
    dup_ids = {k: v for k, v in all_ids.items() if len(v) > 1}
    html_scan = scan_forbidden({p: open(os.path.join(hdir, p), encoding="utf-8").read()
                                for p in os.listdir(hdir) if p.endswith(".html")})
    report["checks"].update({
        "html_pages": sorted(html_files),
        "html_ids_total": len(all_ids),
        "html_ids_duplicate": dup_ids,
        "html_asset_refs_missing": ref_problems,
        "html_forbidden": html_scan,
        "palette": palette_audit(css, [open(os.path.join(hdir, p), encoding="utf-8").read()
                                       for p in os.listdir(hdir) if p.endswith(".html")]),
    })

    fig_layout = [r for ch in chapters for f in ch["figures"] for r in f["layout"]]
    report["figures"] = [{"chapter": ch["n"], **f} for ch in chapters for f in ch["figures"]]
    report["checks"]["figures_expected"] = 2 * sum(len(ch["figures"]) for ch in chapters)
    report["checks"]["figures_layout"] = fig_layout

    gates = []
    if bad:
        gates.append("missing/invalid chapter input: %s" % bad)
    if dup_ids:
        gates.append("duplicate ids across pages: %s" % list(dup_ids)[:5])
    if ref_problems:
        gates.append("unresolved asset refs: %s" % ref_problems[:3])
    p = report["checks"]["palette"]
    if not p["ok"]:
        gates.append("palette gate failed: %s" % p)
    for r in fig_layout:
        if r.get("root_layout_props"):
            gates.append("figure %s keeps root %s" % (r["file"], r["root_layout_props"]))
        elif not r.get("viewBox_ok") or not r.get("root_width") or not r.get("root_height"):
            gates.append("figure %s has no usable viewport" % r["file"])
        elif not r.get("content_bbox"):
            gates.append("figure %s has no measurable artwork bbox" % r["file"])
    if len(fig_layout) < report["checks"]["figures_expected"]:
        gates.append("figure gate covered %d of %d files"
                     % (len(fig_layout), report["checks"]["figures_expected"]))
    if html_scan["control_tags"]:
        gates.append("control/script tags in static pages: %s" % html_scan["control_tags"])
    if html_scan["absolute_local_paths"]:
        gates.append("absolute local path in static pages")
    if html_scan["workflow_words"]:
        gates.append("internal workflow wording in static pages: %s" % html_scan["workflow_words"])
    if sum(c["rendered_segments"] for c in chapters) != report["expected_segments"]:
        gates.append("rendered segment total != source segment total")

    print("scope=%s chapters=%s segments=%d/%d palette=%d figures=%d tables=%d"
          % (report["scope"], want, sum(c["rendered_segments"] for c in chapters),
             report["expected_segments"], len(pal.colors), len(fig_layout),
             sum(1 for ch in chapters for _ in re.findall("<table", ch["body"]))))
    for u in report["unresolved"]:
        print("  UNRESOLVED", u.get("chapter"), u.get("id"), u.get("reason"))

    if gates:
        report["checks"]["gates_failed"] = gates
        report["checks"]["epub_status"] = "blocked-by-html-gates"
        finish(report, out, 1, "html gates failed")
        return 1

    if args.html_only:
        report["checks"]["epub_status"] = "not-requested"
        finish(report, out, 0, "HTML only")
        return 0

    # ---- assemble EPUB container ---------------------------------------
    oebps = os.path.join(out, "build", "oebps")
    if os.path.isdir(oebps):
        shutil.rmtree(oebps)
    os.makedirs(os.path.join(oebps, "text"))
    os.makedirs(os.path.join(oebps, "images"))
    shutil.copyfile(os.path.join(out, "assets", "style.css"), os.path.join(oebps, "style.css"))

    docs = {"nav.xhtml": nav_xhtml(R, chapters, title),
            "text/front.xhtml": front_xhtml(
                R, title, "style.css",
                "本文件只覆盖下列章节，不代表全书。" if subset else "")}
    images = []
    for ch in chapters:
        nn = "%02d" % ch["n"]
        doc = chapter_xhtml(R, ch, {"css": "../style.css", "fig": "../images/"}, ch["title_nav"])
        docs["text/ch%s.xhtml" % nn] = doc
        src_dir = os.path.join(out, "assets", "ch%s" % nn)
        dst_dir = os.path.join(oebps, "images", "ch%s" % nn)
        os.makedirs(dst_dir, exist_ok=True)
        for f in sorted(os.listdir(src_dir)):
            shutil.copyfile(os.path.join(src_dir, f), os.path.join(dst_dir, f))
            images.append("images/ch%s/%s" % (nn, f))
    docs["text/notes.xhtml"] = notes_xhtml(R, chapters, title, "style.css")
    uid = "urn:uuid:snowmoon-bilingual-%s" % (
        "01-32" if not subset else "subset-" + "-".join("%02d" % c["n"] for c in chapters))
    docs["style.css"] = css
    opf = opf_xml(R, chapters, title, uid, report["generated_at"], images, subset)
    docs["content.opf"] = opf

    # every manifest item must exist inside the container: a declared-but-absent
    # stylesheet makes Calibre rebuild generic classes and silently drop colours
    missing_members = []
    for item in re.findall(r"<item\b[^>]*\bhref=\"([^\"]+)\"", opf):
        if item not in docs and item not in images:
            missing_members.append(item)
    if missing_members:
        report["checks"]["manifest_members_missing"] = missing_members
        report["checks"]["epub_status"] = "container-incomplete"
        finish(report, out, 1, "manifest declares files absent from the container")
        return 1

    # XML must be well-formed before Calibre sees it (epub is an XML format)
    xml_bad = []
    for name, d in docs.items():
        if not name.endswith((".xhtml", ".opf")):
            continue
        try:
            ET.fromstring(d.encode("utf-8"))
        except Exception as e:
            xml_bad.append({"doc": name, "error": str(e)})
    if xml_bad:
        report["checks"]["xml_not_well_formed"] = xml_bad
        report["checks"]["epub_status"] = "xml-invalid"
        finish(report, out, 1, "XML not well-formed")
        return 1

    # ids must be unique across the whole book (not just within a page)
    book_ids = {}
    for name, d in docs.items():
        if not name.endswith(".xhtml"):
            continue
        for i in re.findall(r'\bid="([^"]+)"', d):
            book_ids.setdefault(i, []).append(name)
    clash = {k: v for k, v in book_ids.items() if len(v) > 1}
    if clash:
        report["checks"]["book_ids_duplicate"] = clash
        report["checks"]["epub_status"] = "id-clash"
        finish(report, out, 1, "id clash")
        return 1
    report["checks"]["book_ids_total"] = len(book_ids)
    report["checks"]["book_ids_unique"] = True

    for name, d in docs.items():
        open(os.path.join(oebps, name.replace("/", os.sep)), "w", encoding="utf-8").write(d)

    source_epub = os.path.join(out, "build", "source.epub")
    if os.path.exists(source_epub):
        os.remove(source_epub)
    with zipfile.ZipFile(source_epub, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        for n in sorted(docs):
            z.writestr("OEBPS/" + n, docs[n], compress_type=zipfile.ZIP_DEFLATED)
        for img in images:
            z.write(os.path.join(oebps, img.replace("/", os.sep)), "OEBPS/" + img,
                    compress_type=zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(source_epub) as _sz:
        _src_members = len(_sz.namelist())
    report["checks"]["source_epub"] = {
        "bytes": os.path.getsize(source_epub),
        "members": _src_members,
        "sha256": sha256(source_epub)}
    report["checks"]["preconversion_text_chars"] = {
        "%02d" % c["n"]: len(text_of(docs["text/ch%02d.xhtml" % c["n"]])) for c in chapters}

    epub = os.path.join(out, stem + ".epub")
    status = convert(epub, source_epub, oebps, tmpdir, title, stem, images, chapters, docs,
                     report, args.timeout, R, css)
    report["checks"]["epub_status"] = status
    ok = (status == "ok") and not bad and not report["unresolved"]
    finish(report, out, 0 if ok else 1, "EPUB %s" % status)
    return 0 if ok else 1


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def convert(epub, source_epub, oebps, tmpdir, title, stem, images, chapters, docs,
            report, timeout, R, css):
    """One Calibre run, low priority, no retry. Then read the result back."""
    exe = shutil.which("ebook-convert")
    report["checks"]["ebook_convert"] = exe
    if os.path.exists(epub):
        os.remove(epub)          # a stale file must never read as success
    if not exe:
        report["unresolved"].append({"id": "*", "reason": "ebook-convert not on PATH"})
        return "missing-ebook-convert"
    avail = mem_available_mb()
    report["checks"]["memory_available_mb"] = avail
    if avail is not None and avail < 600:
        report["unresolved"].append({"id": "*", "reason": "only %d MB free; conversion not started" % avail})
        return "low-memory"
    cmd = [exe, source_epub, epub, "--epub-version", "3", "--no-default-epub-cover",
           "--disable-font-rescaling", "--title", title, "--authors", AUTHOR,
           "--language", "en", "--output-profile", "tablet"]
    env = os.environ.copy()
    for k in ("TMP", "TEMP", "TMPDIR"):
        env[k] = tmpdir
    report["commands"].append({"argv": cmd, "tmp": tmpdir, "low_priority": True,
                               "timeout_s": timeout, "memory_available_mb": avail})
    print("  converting (idle priority, tmp=%s, timeout=%ss)" % (tmpdir, timeout))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=timeout,
                           creationflags=getattr(subprocess, "IDLE_PRIORITY_CLASS", 0))
        rc = p.returncode
        tail = [x for x in (p.stderr or p.stdout or "").strip().splitlines() if x.strip()][-8:]
    except subprocess.TimeoutExpired:
        report["unresolved"].append({"id": "*", "reason": "ebook-convert timed out after %ss; no rerun" % timeout})
        return "timeout"
    report["checks"]["calibre_returncode"] = rc
    report["checks"]["calibre_output_tail"] = tail
    if rc != 0:
        report["unresolved"].append({"id": "*", "reason": "ebook-convert failed rc=%s" % rc})
        return "nonzero-exit"
    if not os.path.exists(epub):
        report["unresolved"].append({"id": "*", "reason": "ebook-convert rc=0 but wrote no file"})
        return "no-output-file"

    post = readback(epub, chapters, docs, images, R, css)
    report["checks"]["epub"] = post
    for f in post["failures"]:
        report["unresolved"].append({"id": "*", "reason": "epub readback: %s" % f})
    return "ok" if not post["failures"] else "readback-failed"


def readback(epub, chapters, docs, images, R, css_text=""):
    """Independent checks on the converted file, not on our own assumptions."""
    res = {"bytes": os.path.getsize(epub), "sha256": sha256(epub), "failures": []}
    base_css_palette = "\n".join(re.findall(r"\.s\d+ \{ color:#[0-9a-fA-F]{6}; \}", css_text))
    with zipfile.ZipFile(epub) as z:
        names = z.namelist()
        res["members"] = len(names)
        res["first_member"] = names[0]
        res["mimetype_stored"] = z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        if names[0] != "mimetype":
            res["failures"].append("mimetype is not the first member")
        if not res["mimetype_stored"]:
            res["failures"].append("mimetype is compressed")
        bad = z.testzip()
        if bad:
            res["failures"].append("corrupt zip member %s" % bad)
        cont = ET.fromstring(z.read("META-INF/container.xml"))
        rootfile = cont.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get("full-path")
        opf = ET.fromstring(z.read(rootfile))
        ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
        man = {i.get("id"): i.get("href") for i in opf.findall(".//opf:manifest/opf:item", ns)}
        spine = [i.get("idref") for i in opf.findall(".//opf:spine/opf:itemref", ns)]
        res["opf_path"] = rootfile
        res["title"] = (opf.findtext(".//dc:title", namespaces=ns) or "")
        res["creator"] = (opf.findtext(".//dc:creator", namespaces=ns) or "")
        res["language"] = [x.text for x in opf.findall(".//dc:language", ns)]
        res["manifest_items"] = len(man)
        if res["creator"].strip() != AUTHOR:
            res["failures"].append("dc:creator is %r, not %r" % (res["creator"], AUTHOR))
        base = os.path.dirname(rootfile)
        hrefs = [man.get(s) for s in spine]
        res["spine_hrefs"] = hrefs
        # order: front, ch01..chNN, notes
        want_tail = ["ch%02d" % c["n"] for c in chapters]
        joined = " ".join(os.path.basename(h or "") for h in hrefs)
        pos = [joined.find("%s.xhtml" % w) for w in want_tail]
        res["chapter_order_ok"] = all(p >= 0 for p in pos) and pos == sorted(pos) and \
            joined.find("notes.xhtml") > max(pos)
        if not res["chapter_order_ok"]:
            res["failures"].append("spine order does not match requested chapter order")
        # text + ids per converted chapter
        conv_text, conv_docs, seg_counts, text_chars = {}, {}, {}, {}
        for chref, ch in zip([h for h in hrefs if "notes" not in h and "front" not in h], chapters):
            path = posixjoin(base, chref)
            d = z.read(path).decode("utf-8")
            conv_text[ch["n"]] = text_of(d)
            conv_docs["%02d" % ch["n"]] = d
            text_chars["%02d" % ch["n"]] = len(conv_text[ch["n"]])
            seg_counts["%02d" % ch["n"]] = len(set(re.findall(r'id="(c%02d-blk-[^"]+)"' % ch["n"], d)))
            ids = re.findall(r'\bid="([^"]+)"', d)
            if len(ids) != len(set(ids)):
                res["failures"].append("duplicate ids inside converted chapter %02d" % ch["n"])
            for ref in re.findall(r'(?:src|href)="([^"]+)"', d):
                if re.match(r"^[a-z]+:", ref) or ref.startswith("#"):
                    continue
                if posixjoin(os.path.dirname(path), ref) not in names:
                    res["failures"].append("converted ch%02d references missing %s" % (ch["n"], ref))
        res["converted_text_chars"] = text_chars
        res["converted_segment_blocks"] = seg_counts
        res["segment_blocks_match"] = all(
            seg_counts["%02d" % c["n"]] == len(c["src"]["segments"]) for c in chapters)
        if not res["segment_blocks_match"]:
            res["failures"].append("converted segment block count != source segments")
        pre = {k: len(text_of(v)) for k, v in
               (("%02d" % c["n"], docs["text/ch%02d.xhtml" % c["n"]]) for c in chapters)}
        res["preconversion_text_chars"] = pre
        res["text_delta"] = {k: text_chars[k] - pre[k] for k in pre}
        lost = {k: v for k, v in res["text_delta"].items() if v < 0}
        if lost:
            res["failures"].append("text loss after conversion: %s" % lost)
        # speaker classes survive conversion with the same colour values
        css_names = [n for n in names if n.endswith(".css")]
        res["css_members"] = css_names
        if not css_names:
            res["failures"].append("no stylesheet in converted epub")
        else:
            css = "".join(z.read(n).decode("utf-8", "replace") for n in css_names)
            res["css_colour_decls"] = len(re.findall(r"color:\s*#[0-9a-fA-F]{6}", css))
            res["css_oklch"] = css.count("oklch")
            res["css_var"] = css.count("var(")
            pal_hex = [re.search(r"color:\s*(#[0-9a-fA-F]{6})", ln).group(1).lower()
                       for ln in re.findall(r"\.s\d+ \{ color:#[0-9a-fA-F]{6}; \}", base_css_palette)]
            res["palette_colours"] = len(pal_hex)
            res["palette_missing_after_conversion"] = [h for h in pal_hex
                                                       if h not in css.lower()]
            if res["palette_missing_after_conversion"]:
                res["failures"].append("speaker colours lost in conversion: %s"
                                       % res["palette_missing_after_conversion"])
            s_map, e_map = colour_map(css_text), colour_map(css)
            res["speaker_colour_sequences"] = {}
            for ch in chapters:
                want = speaker_colours(docs["text/ch%02d.xhtml" % ch["n"]], s_map)
                got = speaker_colours(conv_docs["%02d" % ch["n"]], e_map)
                same = want == got
                res["speaker_colour_sequences"]["%02d" % ch["n"]] = {
                    "spans": len(want), "identical": same,
                    "mismatches": [i for i, (a, b) in enumerate(zip(want, got)) if a != b][:5]}
                if not same or len(want) != len(got):
                    res["failures"].append("speaker colour sequence changed in chapter %02d"
                                           % ch["n"])
            if res["css_oklch"] or res["css_var"]:
                res["failures"].append("stylesheet relies on oklch/var()")
        res["image_members"] = [n for n in names if n.lower().endswith(".svg")]
        res["image_count"] = len(res["image_members"])
        # no count gate: the converter may prune a copy no page references; a figure
        # that is still referenced but absent already fails the per-chapter check above.
        nav = [n for n in names if n.endswith(("nav.xhtml", "toc.xhtml", ".ncx"))]
        res["nav_members"] = nav
        if not nav:
            res["failures"].append("no nav/toc document in epub")
        else:
            nd = z.read(nav[0]).decode("utf-8")
            res["nav_entries"] = len(re.findall(r"<a\b", nd))
            res["nav_order"] = [x for x in re.findall(r'href="([^"]+\.x?html)"', nd)]
        if not any("notes" in n or "toc" in n or "nav" in n for n in names):
            res["failures"].append("no contents document")
        res["controls"] = {k: len(re.findall(k, "".join(
            z.read(n).decode("utf-8", "replace") for n in names if n.endswith((".xhtml", ".html")))))
            for k in (r"<script\b", r"<input\b", r"<button\b")}
        if any(res["controls"].values()):
            res["failures"].append("controls present in converted xhtml: %s" % res["controls"])
    return res


def posixjoin(a, b):
    p = os.path.normpath(os.path.join(a, b)).replace("\\", "/")
    return p.lstrip("./") if p.startswith("./") else p


def finish(report, out, code, note):
    report["exit_code"] = code
    report["result"] = note
    p = os.path.join(out, "build", "report.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(report, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("output:", out)
    if code == 0:
        print("STATUS OK: %s" % note)
    else:
        print("STATUS FAILED: %s" % note)
        for g in report["checks"].get("gates_failed") or []:
            print("  FAIL", g)
        for u in report["unresolved"]:
            print("  FAIL", u.get("chapter"), u.get("id"), u.get("reason"))


if __name__ == "__main__":
    sys.exit(main())
