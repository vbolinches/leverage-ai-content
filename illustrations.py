#!/usr/bin/env python3
"""Topic illustrations drawn behind slides — the "image related to the post".

Each post spec carries an `art` tag chosen by the generator (the same model
that writes the post knows what it is about). The renderer paints the matching
illustration large in the lower-right of cover/stat/recap slides, in the
account's accent color at low opacity, away from the text zone.

Everything is drawn procedurally: no stock photos (licensing + relevance risk
on a public repo, and tone risk for the immigration audience), no image-model
dependency. Adding a tag = adding a draw function here plus the enum entry in
generate_batch.SUBMIT_TOOL.

    python illustrations.py   # contact sheet of every tag -> scratchpad
"""
import math
from PIL import Image, ImageDraw

REGISTRY = {}


def reg(fn):
    REGISTRY[fn.__name__] = fn
    return fn


# Every function draws into an L-mode overlay via `d` using value `a` (0-255),
# inside a box anchored at (x, y) with unit size s (roughly 420px).

@reg
def spreadsheet(d, a, x, y, s):
    cw, ch = s * 0.22, s * 0.14
    for r in range(5):
        for c in range(4):
            d.rectangle([x + c*cw, y + r*ch, x + (c+1)*cw - 5, y + (r+1)*ch - 5],
                        outline=a, width=3)
    d.rectangle([x + cw, y + ch, x + 2*cw - 5, y + 2*ch - 5], fill=int(a*0.9))
    d.rectangle([x, y - ch*1.4, x + 4*cw - 5, y - ch*0.55], outline=a, width=3)
    d.text((x + 14, y - ch*1.15), "=SUM(", fill=a)


@reg
def document(d, a, x, y, s):
    w, h = s * 0.75, s
    d.rounded_rectangle([x, y, x + w, y + h], radius=16, outline=a, width=4)
    for i, ly in enumerate(range(int(y + h*0.16), int(y + h*0.72), int(h*0.09))):
        d.line([x + w*0.1, ly, x + w*0.9 - (w*0.18 if i % 3 == 2 else 0), ly],
               fill=int(a*0.8), width=5)
    cx, cy, r = x + w*0.75, y + h*0.84, s*0.11
    d.ellipse([cx-r, cy-r, cx+r, cy+r], outline=a, width=4)
    d.ellipse([cx-r*0.6, cy-r*0.6, cx+r*0.6, cy+r*0.6], outline=int(a*0.7), width=2)


@reg
def email(d, a, x, y, s):
    w, h = s, s * 0.62
    d.rounded_rectangle([x, y, x + w, y + h], radius=14, outline=a, width=4)
    d.line([x + 6, y + 8, x + w/2, y + h*0.55], fill=a, width=4)
    d.line([x + w - 6, y + 8, x + w/2, y + h*0.55], fill=a, width=4)


@reg
def calendar(d, a, x, y, s):
    w, h = s, s * 0.85
    d.rounded_rectangle([x, y, x + w, y + h], radius=14, outline=a, width=4)
    d.rectangle([x, y, x + w, y + h*0.18], fill=int(a*0.8))
    for r in range(3):
        for c in range(5):
            cx, cy = x + w*0.1 + c*w*0.18, y + h*0.28 + r*h*0.22
            d.rectangle([cx, cy, cx + w*0.12, cy + h*0.14], outline=int(a*0.7), width=2)
    d.rectangle([x + w*0.1 + 2*w*0.18, y + h*0.28 + h*0.22,
                 x + w*0.1 + 2*w*0.18 + w*0.12, y + h*0.28 + h*0.22 + h*0.14],
                fill=a)


@reg
def chat(d, a, x, y, s):
    d.rounded_rectangle([x, y, x + s*0.7, y + s*0.34], radius=24, outline=a, width=4)
    d.polygon([(x + s*0.12, y + s*0.33), (x + s*0.22, y + s*0.33),
               (x + s*0.1, y + s*0.46)], fill=a)
    d.rounded_rectangle([x + s*0.3, y + s*0.5, x + s, y + s*0.84],
                        radius=24, fill=int(a*0.55))
    for i in range(3):
        d.ellipse([x + s*0.5 + i*s*0.1, y + s*0.64, x + s*0.55 + i*s*0.1,
                   y + s*0.69], fill=a)


@reg
def checklist(d, a, x, y, s):
    for i in range(4):
        by = y + i * s * 0.26
        d.rounded_rectangle([x, by, x + s*0.18, by + s*0.18], radius=8,
                            outline=a, width=4)
        if i < 3:
            d.line([x + s*0.04, by + s*0.09, x + s*0.08, by + s*0.14], fill=a, width=5)
            d.line([x + s*0.08, by + s*0.14, x + s*0.15, by + s*0.04], fill=a, width=5)
        d.line([x + s*0.28, by + s*0.09, x + s, by + s*0.09],
               fill=int(a*0.75), width=6)


@reg
def chart(d, a, x, y, s):
    base = y + s * 0.9
    d.line([x, base, x + s, base], fill=a, width=4)
    d.line([x, base, x, y], fill=a, width=4)
    for i, h in enumerate((0.3, 0.55, 0.42, 0.75)):
        bx = x + s*0.12 + i * s*0.22
        d.rectangle([bx, base - s*h, bx + s*0.14, base], fill=int(a*0.7))
    pts = [(x + s*0.05, base - s*0.25), (x + s*0.35, base - s*0.5),
           (x + s*0.6, base - s*0.4), (x + s*0.95, base - s*0.85)]
    d.line(pts, fill=a, width=6)
    d.polygon([(x + s*0.95, base - s*0.85), (x + s*0.83, base - s*0.8),
               (x + s*0.9, base - s*0.7)], fill=a)


@reg
def clock(d, a, x, y, s):
    r = s * 0.48
    cx, cy = x + r, y + r
    d.ellipse([cx-r, cy-r, cx+r, cy+r], outline=a, width=5)
    d.line([cx, cy, cx, cy - r*0.65], fill=a, width=6)
    d.line([cx, cy, cx + r*0.45, cy + r*0.2], fill=a, width=6)
    for k in range(12):
        ang = k * math.pi / 6
        d.line([cx + math.cos(ang)*r*0.86, cy + math.sin(ang)*r*0.86,
                cx + math.cos(ang)*r*0.95, cy + math.sin(ang)*r*0.95],
               fill=int(a*0.7), width=3)


@reg
def gavel(d, a, x, y, s):
    # head
    d.rounded_rectangle([x + s*0.35, y, x + s*0.75, y + s*0.22], radius=10,
                        fill=int(a*0.8))
    # handle
    d.line([x + s*0.55, y + s*0.22, x + s*0.2, y + s*0.75], fill=a, width=12)
    # block
    d.rounded_rectangle([x + s*0.45, y + s*0.82, x + s, y + s*0.95], radius=8,
                        outline=a, width=4)


@reg
def passport(d, a, x, y, s):
    w, h = s * 0.68, s * 0.95
    d.rounded_rectangle([x, y, x + w, y + h], radius=18, outline=a, width=4)
    cx, cy, r = x + w/2, y + h*0.38, w*0.24
    d.ellipse([cx-r, cy-r, cx+r, cy+r], outline=a, width=3)
    d.arc([cx-r, cy-r, cx+r, cy+r], 0, 360, fill=a)
    d.line([cx-r, cy, cx+r, cy], fill=int(a*0.7), width=2)
    d.arc([cx-r*0.45, cy-r, cx+r*0.45, cy+r], 0, 360, fill=int(a*0.7))
    d.line([x + w*0.2, y + h*0.72, x + w*0.8, y + h*0.72], fill=a, width=4)
    d.line([x + w*0.2, y + h*0.82, x + w*0.8, y + h*0.82], fill=a, width=4)


@reg
def scales(d, a, x, y, s):
    cx = x + s/2
    d.line([cx, y, cx, y + s*0.8], fill=a, width=6)
    d.line([x + s*0.08, y + s*0.14, x + s*0.92, y + s*0.14], fill=a, width=5)
    for bx in (x + s*0.08, x + s*0.92):
        d.line([bx, y + s*0.14, bx - s*0.12, y + s*0.42], fill=int(a*0.8), width=3)
        d.line([bx, y + s*0.14, bx + s*0.12, y + s*0.42], fill=int(a*0.8), width=3)
        d.arc([bx - s*0.14, y + s*0.3, bx + s*0.14, y + s*0.54], 0, 180, fill=a, width=4)
    d.rectangle([cx - s*0.14, y + s*0.82, cx + s*0.14, y + s*0.88], fill=a)


@reg
def building(d, a, x, y, s):
    d.polygon([(x, y + s*0.22), (x + s/2, y), (x + s, y + s*0.22)], outline=a, width=4)
    for i in range(4):
        bx = x + s*0.12 + i * s*0.22
        d.rectangle([bx, y + s*0.28, bx + s*0.1, y + s*0.82], outline=a, width=4)
    d.rectangle([x, y + s*0.85, x + s, y + s*0.95], outline=a, width=4)


@reg
def form(d, a, x, y, s):
    w, h = s * 0.75, s
    d.rounded_rectangle([x, y, x + w, y + h], radius=16, outline=a, width=4)
    d.rectangle([x + w*0.1, y + h*0.08, x + w*0.5, y + h*0.14], fill=int(a*0.8))
    for i in range(3):
        ly = y + h*0.28 + i*h*0.2
        d.rectangle([x + w*0.1, ly, x + w*0.2, ly + h*0.07], outline=a, width=3)
        d.line([x + w*0.28, ly + h*0.05, x + w*0.88, ly + h*0.05],
               fill=int(a*0.7), width=4)


@reg
def lightbulb(d, a, x, y, s):
    r = s * 0.32
    cx, cy = x + s/2, y + r
    d.ellipse([cx-r, cy-r, cx+r, cy+r], outline=a, width=5)
    d.rectangle([cx - r*0.4, cy + r*0.9, cx + r*0.4, cy + r*1.35], outline=a, width=4)
    for ang in (-0.8, 0, 0.8, math.pi - 0.8, math.pi, math.pi + 0.8):
        d.line([cx + math.cos(ang)*r*1.2, cy + math.sin(ang)*r*1.2,
                cx + math.cos(ang)*r*1.5, cy + math.sin(ang)*r*1.5],
               fill=int(a*0.8), width=4)


@reg
def folder(d, a, x, y, s):
    d.polygon([(x, y + s*0.12), (x + s*0.35, y + s*0.12), (x + s*0.45, y + s*0.22),
               (x + s, y + s*0.22), (x + s, y + s*0.85), (x, y + s*0.85)],
              outline=a, width=4)
    d.line([x + s*0.12, y + s*0.4, x + s*0.6, y + s*0.4], fill=int(a*0.7), width=5)
    d.line([x + s*0.12, y + s*0.55, x + s*0.75, y + s*0.55], fill=int(a*0.7), width=5)


@reg
def newspaper(d, a, x, y, s):
    w, h = s, s * 0.8
    d.rounded_rectangle([x, y, x + w, y + h], radius=10, outline=a, width=4)
    d.rectangle([x + w*0.06, y + h*0.08, x + w*0.94, y + h*0.24], fill=int(a*0.85))
    d.rectangle([x + w*0.06, y + h*0.34, x + w*0.45, y + h*0.9], outline=a, width=3)
    for ly in range(int(y + h*0.38), int(y + h*0.86), int(h*0.1)):
        d.line([x + w*0.52, ly, x + w*0.92, ly], fill=int(a*0.7), width=4)


@reg
def globe(d, a, x, y, s):
    r = s * 0.48
    cx, cy = x + r, y + r
    d.ellipse([cx-r, cy-r, cx+r, cy+r], outline=a, width=5)
    d.line([cx-r, cy, cx+r, cy], fill=int(a*0.75), width=3)
    d.arc([cx-r, cy-r*0.5, cx+r, cy+r*0.5], 0, 360, fill=int(a*0.75), width=3)
    d.arc([cx-r*0.5, cy-r, cx+r*0.5, cy+r], 0, 360, fill=int(a*0.75), width=3)


TAGS = sorted(REGISTRY)


def paint(img, tag, color, alpha=0.5):
    """Composite the tag's illustration onto img, lower-right, text-safe."""
    if tag not in REGISTRY:
        return img
    W, H = img.size
    s = 430
    x, y = W - s - 80, H - s - 190     # clear of footer and headline zone
    ov = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(ov)
    REGISTRY[tag](d, int(255 * alpha), x, y, s)
    solid = Image.new("RGB", (W, H), color)
    return Image.composite(solid, img, ov)


if __name__ == "__main__":
    out = Image.new("RGB", (1080 * 4, 1350 * 4), (11, 15, 26))
    for i, tag in enumerate(TAGS):
        tile = Image.new("RGB", (1080, 1350), (11, 15, 26))
        tile = paint(tile, tag, (45, 160, 160), alpha=0.8)
        ImageDraw.Draw(tile).text((60, 60), tag, fill=(255, 255, 255))
        out.paste(tile, ((i % 4) * 1080, (i // 4) * 1350))
    out.resize((1080, 1350)).save("contact_sheet.png")
    print(f"{len(TAGS)} tags -> contact_sheet.png")
