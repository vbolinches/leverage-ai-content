#!/usr/bin/env python3
"""Render branded carousel slides from a post spec.

Reproduces the existing @leverageai.daily deck: 1080x1350, deep-navy field,
letterspaced header, blue eyebrow, heavy headline with coloured accent spans,
and a footer carrying the handle and page indicator.

Usage:
    python render_slides.py specs/post15-example.json
    python render_slides.py specs/post15-example.json --out queue/

Spec format (see specs/_example.json):
    {
      "slug": "post15-something",
      "caption": "...",
      "slides": [ {"kind": "cover", ...}, ... ]
    }

Rich text: any headline/body field accepts either a plain string or a list of
segments, e.g. [{"t": "Stop writing "}, {"t": "from scratch.", "c": "blue"}].
Segment keys: t (text), c (blue|green|dim|white), b (bold).
"""
import argparse, json, os, sys
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
BG = (11, 15, 26)

COLORS = {
    "white": (255, 255, 255),
    "blue":  (59, 130, 246),
    "green": (52, 211, 153),
    "body":  (199, 206, 219),
    "dim":   (122, 132, 150),
    "mark":  (45, 212, 191),
}

MARGIN = 85
HEADER_Y = 78
FOOTER_Y = 1258
CONTENT_TOP = 175
CONTENT_BOTTOM = 1215


# --------------------------------------------------------------------------
# Fonts: prefer fonts committed to the repo, then fall back to whatever the
# host provides. CI (ubuntu) has DejaVu; Windows has Segoe UI.
# --------------------------------------------------------------------------
FONT_CANDIDATES = {
    "bold": [
        "brand/fonts/Inter-Bold.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ],
    "semi": [
        "brand/fonts/Inter-SemiBold.ttf",
        "C:/Windows/Fonts/seguisb.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ],
    "regular": [
        "brand/fonts/Inter-Regular.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ],
    "mono": [
        "brand/fonts/JetBrainsMono-Regular.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ],
}
_font_cache = {}


def font(kind, size):
    key = (kind, size)
    if key in _font_cache:
        return _font_cache[key]
    for path in FONT_CANDIDATES[kind]:
        if os.path.exists(path):
            f = ImageFont.truetype(path, size)
            _font_cache[key] = f
            return f
    raise RuntimeError(
        f"No font found for '{kind}'. Tried: {FONT_CANDIDATES[kind]}. "
        "On Linux install fonts-dejavu, or commit fonts to brand/fonts/."
    )


def normalise(value):
    """Accept a plain string or a list of segments; always return segments."""
    if value is None:
        return []
    if isinstance(value, str):
        return [{"t": value}]
    return value


def seg_font(seg, kind, size):
    return font("bold" if seg.get("b") else kind, size)


def measure(draw, text, f):
    return draw.textbbox((0, 0), text, font=f)[2]


def wrap_segments(draw, segments, kind, size, max_w):
    """Wrap rich-text segments into lines of (text, colour, font) runs."""
    words = []
    for seg in segments:
        f = seg_font(seg, kind, size)
        col = COLORS[seg.get("c", "white")]
        for i, w in enumerate(seg["t"].split()):
            words.append((w, col, f, i == 0))

    lines, cur, cur_w = [], [], 0
    space_w = measure(draw, " ", font(kind, size))
    for word, col, f, _ in words:
        ww = measure(draw, word, f)
        add = ww if not cur else ww + space_w
        if cur and cur_w + add > max_w:
            lines.append(cur)
            cur, cur_w = [(word, col, f)], ww
        else:
            cur.append((word, col, f))
            cur_w += add
    if cur:
        lines.append(cur)
    return lines


def draw_lines(draw, lines, x, y, line_h, kind, size):
    space_w = measure(draw, " ", font(kind, size))
    for line in lines:
        cx = x
        for i, (word, col, f) in enumerate(line):
            if i:
                cx += space_w
            draw.text((cx, y), word, font=f, fill=col)
            cx += measure(draw, word, f)
        y += line_h
    return y


def draw_tracked(draw, text, x, y, f, fill, tracking):
    """Letterspaced text — used for the header and eyebrow."""
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill)
        x += measure(draw, ch, f) + tracking
    return x


def draw_mark(draw, x, y, size=34):
    """The teal rounded-square bolt used in the header."""
    r = size // 3
    draw.rounded_rectangle([x, y, x + size, y + size], radius=r, fill=COLORS["mark"])
    s = size / 100.0
    bolt = [(58, 12), (30, 54), (48, 54), (40, 90), (72, 44), (52, 44)]
    draw.polygon([(x + px * s, y + py * s) for px, py in bolt], fill=BG)


def draw_chrome(img, draw, page, total, footer_right=None):
    draw_mark(draw, MARGIN, HEADER_Y)
    f = font("semi", 21)
    draw_tracked(draw, "LEVERAGE AI", MARGIN + 50, HEADER_Y + 6, f, COLORS["dim"], 4)

    ff = font("regular", 25)
    draw.text((MARGIN, FOOTER_Y), "@leverageai.daily", font=ff, fill=COLORS["dim"])

    right = footer_right if footer_right is not None else f"{page}/{total}"
    if right:
        col = COLORS["blue"] if not right[0].isdigit() else COLORS["dim"]
        fr = font("semi", 25) if not right[0].isdigit() else ff
        w = measure(draw, right, fr)
        draw.text((W - MARGIN - w, FOOTER_Y), right, font=fr, fill=col)


def render_slide(spec, page, total):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    kind = spec.get("kind", "step")
    max_w = W - 2 * MARGIN
    y = CONTENT_TOP

    if spec.get("eyebrow"):
        fe = font("bold", 26)
        draw_tracked(draw, spec["eyebrow"].upper(), MARGIN, y, fe, COLORS["blue"], 3)
        y += 62

    if spec.get("headline"):
        size = 78 if kind in ("cover", "stat") else 70
        lines = wrap_segments(draw, normalise(spec["headline"]), "bold", size, max_w)
        y = draw_lines(draw, lines, MARGIN, y, int(size * 1.16), "bold", size)
        y += 26

    if spec.get("sub"):
        lines = wrap_segments(draw, normalise(spec["sub"]), "regular", 40, max_w)
        y = draw_lines(draw, lines, MARGIN, y, 58, "regular", 40)
        y += 18

    if spec.get("body"):
        lines = wrap_segments(draw, normalise(spec["body"]), "regular", 40, max_w)
        y = draw_lines(draw, lines, MARGIN, y, 58, "regular", 40)
        y += 18

    if spec.get("label"):
        fl = font("bold", 24)
        draw_tracked(draw, spec["label"].upper(), MARGIN, y, fl, COLORS["green"], 3)
        y += 56

    if spec.get("code"):
        fm = font("mono", 30)
        raw = [ln for ln in spec["code"].split("\n")]
        pad, lh = 34, 44
        box_h = len(raw) * lh + pad * 2
        draw.rounded_rectangle([MARGIN, y, W - MARGIN, y + box_h], radius=18,
                               fill=(17, 24, 39))
        draw.rectangle([MARGIN, y + 10, MARGIN + 6, y + box_h - 10], fill=COLORS["blue"])
        ty = y + pad
        for ln in raw:
            draw.text((MARGIN + pad + 20, ty), ln, font=fm, fill=COLORS["body"])
            ty += lh
        y += box_h + 26

    if spec.get("stat"):
        fs = font("bold", 110)
        lines = wrap_segments(draw, [{"t": spec["stat"], "c": "green"}], "bold", 110, max_w)
        y = draw_lines(draw, lines, MARGIN, y + 40, 124, "bold", 110)

    if spec.get("items"):
        fi = font("semi", 40)
        for i, item in enumerate(spec["items"]):
            draw.text((MARGIN, y + 4), "→", font=font("semi", 38), fill=COLORS["blue"])
            lines = wrap_segments(draw, normalise(item), "semi", 40, max_w - 70)
            draw_lines(draw, lines, MARGIN + 62, y, 54, "semi", 40)
            y += 54 * len(lines) + 22
            if i < len(spec["items"]) - 1:
                draw.line([MARGIN, y - 12, W - MARGIN, y - 12], fill=(30, 38, 55), width=2)
                y += 12

    if spec.get("cta_title"):
        box_top = min(y + 30, CONTENT_BOTTOM - 190)
        draw.rounded_rectangle([MARGIN, box_top, W - MARGIN, box_top + 175],
                               radius=20, fill=(17, 24, 39))
        ft = font("bold", 46)
        t = spec["cta_title"]
        draw.text(((W - measure(draw, t, ft)) / 2, box_top + 38), t,
                  font=ft, fill=COLORS["green"])
        if spec.get("cta_sub"):
            fsub = font("regular", 28)
            lines = wrap_segments(draw, normalise(spec["cta_sub"]), "regular", 28,
                                  max_w - 120)
            ly = box_top + 102
            for line in lines:
                lw = sum(measure(draw, w, f) for w, _, f in line) + \
                     measure(draw, " ", fsub) * (len(line) - 1)
                cx = (W - lw) / 2
                for i, (word, col, f) in enumerate(line):
                    if i:
                        cx += measure(draw, " ", fsub)
                    draw.text((cx, ly), word, font=f, fill=COLORS["dim"])
                    cx += measure(draw, word, f)
                ly += 36

    draw_chrome(img, draw, page, total, spec.get("footer_right"))
    return img


def render_post(spec, out_root="queue"):
    slug = spec["slug"]
    out_dir = os.path.join(out_root, slug)
    os.makedirs(out_dir, exist_ok=True)
    slides = spec["slides"]
    paths = []
    for i, s in enumerate(slides, 1):
        img = render_slide(s, i, len(slides))
        p = os.path.join(out_dir, f"slide{i:02d}.png")
        img.save(p, "PNG", optimize=True)
        paths.append(f"{out_root}/{slug}/slide{i:02d}.png".replace("\\", "/"))
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--out", default="queue")
    a = ap.parse_args()

    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)

    paths = render_post(spec, a.out)
    print(f"{spec['slug']}: {len(paths)} slides -> {a.out}/{spec['slug']}/")
    for p in paths:
        print("  ", p)


if __name__ == "__main__":
    sys.exit(main())
