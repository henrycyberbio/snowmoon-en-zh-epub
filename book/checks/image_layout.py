"""Figure-asset layout gate + headless pixel probe.

Why this exists
---------------
The source page embeds the map SVGs *inline* and neutralises layout styles on the
SVG root with article CSS (`.document-page svg { margin: 0 !important; }` and
`.document-page .device-view svg { margin-left:auto !important; ... }`).
The book renders the same SVG files as standalone <img> documents. Inside an
<img> the SVG is its own root, that article CSS no longer applies, and any
`margin-left` left on the root shifts the artwork inside the image viewport:
everything right of (width - margin) is clipped.

So two independent checks:
  * static  : root-level layout offsets / viewBox sanity / content bbox  (build gate)
  * pixel   : render the asset as <img> and compare against an inline render
              of the same SVG with the article CSS applied (site ground truth)

Pixel mode needs a Chromium binary and Pillow; the build gate never calls it.
"""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

SVG_NS = "http://www.w3.org/2000/svg"

# Layout declarations that only had meaning in the site's inline flow. The site
# neutralises exactly these with !important rules; as <img> they must not apply.
ROOT_LAYOUT_PROPS = (
    "margin", "margin-left", "margin-right", "margin-top", "margin-bottom",
    "position", "left", "right", "top", "bottom", "inset",
    "float", "vertical-align",
)

SVG_OPEN_RE = re.compile(rb"<svg\b[^>]*>", re.S)


# ------------------------------------------------------------------ static side
def _root_tag(data):
    m = SVG_OPEN_RE.search(data)
    return m


def _attr(tag, name):
    m = re.search(r'\b' + re.escape(name) + r'\s*=\s*"([^"]*)"', tag)
    if not m:
        m = re.search(r"\b" + re.escape(name) + r"\s*=\s*'([^']*)'", tag)
    return m.group(1) if m else None


def parse_style(style):
    out = []
    for part in (style or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out.append((k.strip().lower(), v.strip()))
    return out


def split_root_style(data):
    """Return (decls, offending_props) for the SVG root's inline style."""
    m = _root_tag(data)
    if not m:
        return [], []
    tag = m.group(0).decode("utf-8", "replace")
    decls = parse_style(_attr(tag, "style"))
    offending = [k for k, _v in decls if k in ROOT_LAYOUT_PROPS]
    return decls, offending


def strip_root_layout_style(data):
    """Remove layout-only declarations from the SVG root's inline style.

    Everything else (attributes, viewBox, marker defs, drawing content, bytes of
    whitespace) is preserved. An empty style attribute is dropped entirely.
    Returns (new_bytes, removed_decls).
    """
    m = _root_tag(data)
    if not m:
        return data, []
    tag = m.group(0).decode("utf-8", "replace")
    if not re.search(r'\bstyle\s*=', tag):
        return data, []
    decls, offending = split_root_style(data)
    if not offending:
        return data, []
    kept = ["%s:%s" % (k, v) for k, v in decls if k not in ROOT_LAYOUT_PROPS]
    style_m = re.search(r'(\s)style\s*=\s*"([^"]*)"', tag)
    if not style_m:
        return data, []
    new_tag = tag[:style_m.start()] + (
        (' style="%s"' % ";".join(kept)) if kept else "") + tag[style_m.end():]
    new_data = data[:m.start()] + new_tag.encode("utf-8") + data[m.end():]
    removed = ["%s:%s" % (k, v) for k, v in decls if k in ROOT_LAYOUT_PROPS]
    return new_data, removed


def _num(v, default=None):
    try:
        return float(str(v).replace("px", "").strip())
    except Exception:
        return default


def content_bbox(text):
    """Conservative ink bbox in user units from element geometry.

    Covers rect / line / circle / ellipse / polygon points / text (anchor aware,
    glyph width approximated). Marker tips are not modelled. This is a gate for
    'artwork sits inside its artboard', not a renderer.
    """
    x0 = y0 = float("inf")
    x1 = y1 = -float("inf")

    def take(a, b, c, d):
        nonlocal x0, y0, x1, y1
        x0, y0 = min(x0, a), min(y0, b)
        x1, y1 = max(x1, c), max(y1, d)

    for m in re.finditer(r"<rect\b([^>]*)>", text):
        a = m.group(1)
        x, y = _num(_attr(a, "x"), 0.0), _num(_attr(a, "y"), 0.0)
        w, h = _num(_attr(a, "width"), 0.0), _num(_attr(a, "height"), 0.0)
        take(x, y, x + w, y + h)
    for m in re.finditer(r"<line\b([^>]*)>", text):
        a = m.group(1)
        take(min(_num(_attr(a, "x1"), 0), _num(_attr(a, "x2"), 0)),
             min(_num(_attr(a, "y1"), 0), _num(_attr(a, "y2"), 0)),
             max(_num(_attr(a, "x1"), 0), _num(_attr(a, "x2"), 0)),
             max(_num(_attr(a, "y1"), 0), _num(_attr(a, "y2"), 0)))
    for m in re.finditer(r"<circle\b([^>]*)>", text):
        a = m.group(1)
        cx, cy, r = _num(_attr(a, "cx"), 0), _num(_attr(a, "cy"), 0), _num(_attr(a, "r"), 0)
        take(cx - r, cy - r, cx + r, cy + r)
    for m in re.finditer(r"<ellipse\b([^>]*)>", text):
        a = m.group(1)
        cx, cy = _num(_attr(a, "cx"), 0), _num(_attr(a, "cy"), 0)
        rx, ry = _num(_attr(a, "rx"), 0), _num(_attr(a, "ry"), 0)
        take(cx - rx, cy - ry, cx + rx, cy + ry)
    for m in re.finditer(r"<polygon\b([^>]*points=\"([^\"]*)\")", text):
        pts = [_num(p) for p in re.split(r"[,\s]+", m.group(2).strip()) if p]
        xs, ys = pts[0::2], pts[1::2]
        if xs:
            take(min(xs), min(ys), max(xs), max(ys))
    for m in re.finditer(r"<text\b([^>]*)>([^<]*)</text>", text):
        a, txt = m.group(1), m.group(2)
        x, y = _num(_attr(a, "x"), 0), _num(_attr(a, "y"), 0)
        fs = _num(_attr(a, "font-size"), 10.0)
        anchor = (_attr(a, "text-anchor") or "start").strip()
        width = len(txt) * fs * 0.60          # generous latin-average advance
        left = x - width / 2 if anchor == "middle" else (x - width if anchor == "end" else x)
        take(left, y - fs * 0.80, left + width, y + fs * 0.25)
    if x0 == float("inf"):
        return None
    return {"x0": round(x0, 2), "y0": round(y0, 2), "x1": round(x1, 2), "y1": round(y1, 2)}


def inspect_svg(path):
    data = open(path, "rb").read()
    m = _root_tag(data)
    head = data[:200].decode("utf-8", "replace")
    text = data.decode("utf-8", "replace")
    tag = m.group(0).decode("utf-8", "replace") if m else ""
    w, h = _num(_attr(tag, "width"), None), _num(_attr(tag, "height"), None)
    vb = _attr(tag, "viewBox")
    vb_parts = [float(p) for p in re.split(r"[,\s]+", vb.strip())] if vb else []
    decls, offending = split_root_style(data)
    bbox = content_bbox(text)
    rep = {
        "file": os.path.basename(path),
        "bytes": len(data),
        "xml_decl_utf8_upper": "encoding='UTF-8'" in head or 'encoding="UTF-8"' in head,
        "root_width": w, "root_height": h,
        "viewBox": vb,
        "viewBox_ok": len(vb_parts) == 4 and vb_parts[2] > 0 and vb_parts[3] > 0,
        "root_style": ";".join("%s:%s" % kv for kv in decls),
        "root_layout_props": offending,
        "content_bbox": bbox,
    }
    if bbox and len(vb_parts) == 4:
        vx, vy, vw, vh = vb_parts
        rep["overflow_right"] = round(bbox["x1"] - (vx + vw), 2)
        rep["overflow_bottom"] = round(bbox["y1"] - (vy + vh), 2)
        rep["content_fully_inside"] = rep["overflow_right"] <= 0.5 and rep["overflow_bottom"] <= 0.5
        # effective visible width when the file is used as a standalone <img>
        rep["img_visible_width"] = round((w or vw) - sum(
            _num(dict(decls).get(k), 0) for k in ("margin-left", "margin", "left")), 2)
    rep["ok_for_img"] = bool(rep["viewBox_ok"] and not offending and w and h)
    if rep.get("content_bbox") and rep.get("viewBox_ok") and rep["content_bbox"]["x1"] > vb_parts[0] + vb_parts[2] + 0.5:
        rep["note"] = ("artwork extends past the artboard right edge; identical in the "
                       "site's inline render (SVG root overflow is hidden), so not an "
                       "img-embedding regression")
    return rep


# ------------------------------------------------------------------- pixel side
PROBE_CSS = """
html,body{margin:0;padding:0;background:#fff}
.box{width:%(w)dpx;height:%(h)dpx;overflow:hidden;position:relative}
.box img{display:block;width:%(w)dpx;height:%(h)dpx;border:0}
/* article CSS from the source page: neutralises root layout styles inline */
.box svg{margin:0 !important;display:block}
"""


def _chrome():
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(p):
            return p
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        from shutil import which
        w = which(name)
        if w:
            return w
    return None


def _shot(html, png, w, h, workdir, name="probe"):
    """Screenshot `html` at exactly w x h. Chrome runs in TEMP: on this host it
    cannot write into the project tree, and backslash args confuse it."""
    chrome = _chrome()
    if not chrome:
        raise RuntimeError("no chromium/edge binary found")
    tmpw = os.path.join(tempfile.gettempdir(), "snowmoon-probe")
    os.makedirs(tmpw, exist_ok=True)
    html_path = os.path.join(tmpw, name + ".html")
    open(html_path, "w", encoding="utf-8").write(html)
    shot = os.path.join(tmpw, name + ".png")
    if os.path.exists(shot):
        os.remove(shot)
    cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--force-device-scale-factor=1", "--no-first-run", "--no-default-browser-check",
           "--user-data-dir=" + os.path.join(tmpw, "ud").replace("\\", "/"),
           "--window-size=%d,%d" % (w, h),
           "--screenshot=" + shot.replace("\\", "/"),
           "file:///" + html_path.replace("\\", "/")]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    if not os.path.exists(shot):
        raise RuntimeError("screenshot failed for %s" % html_path)
    os.makedirs(workdir, exist_ok=True)
    shutil.copyfile(shot, os.path.abspath(png))
    return png


def load_png(path):
    """Minimal stdlib PNG reader: 8-bit RGB/RGBA/gray, non-interlaced."""
    raw = open(path, "rb").read()
    assert raw[:8] == bytes([137, 80, 78, 71, 13, 10, 26, 10]), "not a png"

    pos, idat, w = 8, b"", None
    h = bitdepth = ctype = None
    while pos < len(raw):
        ln = struct.unpack(">I", raw[pos:pos + 4])[0]
        typ = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bitdepth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            assert bitdepth == 8 and interlace == 0, "unsupported png variant"
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        pos += 12 + ln
    nch = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
    stride = w * nch
    data = zlib.decompress(idat)
    out = bytearray(stride * h)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = data[p]; p += 1
        line = bytearray(data[p:p + stride]); p += stride
        if ft == 1:
            for i in range(nch, stride):
                line[i] = (line[i] + line[i - nch]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                b = prev[i]
                c = prev[i - nch] if i >= nch else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, nch, out


def px_at(img, x, y):
    w, h, nch, buf = img
    i = (y * w + x) * nch
    if nch <= 2:
        return (buf[i],) * 3
    return (buf[i], buf[i + 1], buf[i + 2])


def probe_asset(svg_path, workdir, tol=24):
    """Render one SVG twice (inline + article CSS, then as <img>) and diff pixels.

    `img_matches_inline` is the pass flag: the <img> rendering must be pixel-equal
    to the inline rendering the source page produces.
    """
    data = open(svg_path, "rb").read()
    text = data.decode("utf-8", "replace")
    m = _root_tag(data)
    tag = m.group(0).decode("utf-8", "replace")
    w = int(round(_num(_attr(tag, "width"), 400)))
    h = int(round(_num(_attr(tag, "height"), 200)))
    inline_svg = re.sub(r"^\s*<\?xml[^>]*\?>", "", text[m.start():])

    css = PROBE_CSS % {"w": w, "h": h}
    ref_html = ("<!doctype html><meta charset=utf-8><style>%s</style>"
                "<body><div class=box>%s</div></body>" % (css, inline_svg))
    img_html = ("<!doctype html><meta charset=utf-8><style>%s</style>"
                "<body><div class=box><img src=\"%s\"></div></body>"
                % (css, "file:///" + os.path.abspath(svg_path).replace("\\", "/")))

    base = os.path.basename(svg_path)
    ref_png = _shot(ref_html, os.path.join(workdir, base + ".inline.png"), w, h, workdir,
                    name=base + "-inline")
    img_png = _shot(img_html, os.path.join(workdir, base + ".img.png"), w, h, workdir,
                    name=base + "-img")

    a, b = load_png(ref_png), load_png(img_png)
    assert (a[0], a[1]) == (b[0], b[1]), "size mismatch"
    W, H = a[0], a[1]
    changed, cols, bbox = 0, [0] * W, None
    for y in range(H):
        for x in range(W):
            pa, pb = px_at(a, x, y), px_at(b, x, y)
            if max(abs(pa[i] - pb[i]) for i in range(3)) > tol:
                changed += 1
                cols[x] += 1
                bbox = (x, y, x, y) if bbox is None else (
                    min(bbox[0], x), min(bbox[1], y), max(bbox[2], x), max(bbox[3], y))
    edge = W - max(40, W // 8)
    right = [x for x in range(edge, W) if cols[x]]
    # Report PNG paths relative to the probe outdir, not the caller's cwd: on
    # Windows the cwd and the outdir can sit on different drives, where the
    # default start=os.curdir makes os.path.relpath raise ValueError.
    wd = os.path.abspath(workdir)
    stats = {
        "svg": base,
        "size": [W, H],
        "changed_pixels": changed,
        "changed_bbox": bbox,
        "right_edge_changed_cols": len(right),
        "img_matches_inline": changed == 0,
        "inline_png": os.path.relpath(os.path.abspath(ref_png), wd).replace("\\", "/"),
        "img_png": os.path.relpath(os.path.abspath(img_png), wd).replace("\\", "/"),
    }
    decls, offending = split_root_style(data)
    if offending:
        d = dict(decls)
        shift = int(round(_num(d.get("margin-left", d.get("margin", d.get("left"))), 0.0)))
        stats["shift_px"] = shift
        if shift:
            mism = 0
            for y in range(H):
                for x in range(shift, W):
                    pa, pb = px_at(a, x - shift, y), px_at(b, x, y)
                    if max(abs(pa[i] - pb[i]) for i in range(3)) > tol:
                        mism += 1
            stats["pixels_matching_inline_shifted_by_margin"] = mism == 0
            stats["shift_compare_mismatch_pixels"] = mism
    return stats


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: image_layout.py <svg> [<svg> ...]   |   --pixel <outdir> <svg> ...")
        return 2
    pixel = False
    if argv[0] == "--pixel":
        pixel = True
        outdir = argv[1]
        argv = argv[2:]
        os.makedirs(outdir, exist_ok=True)
    rc = 0
    for p in argv:
        rep = inspect_svg(p)
        print(json.dumps(rep, ensure_ascii=False))
        if pixel:
            st = probe_asset(p, outdir)
            print(json.dumps(st, ensure_ascii=False))
            if not st["img_matches_inline"]:
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
