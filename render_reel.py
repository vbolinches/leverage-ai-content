#!/usr/bin/env python3
"""Render a post spec as a vertical Reel (1080x1920 MP4).

Reels are where Instagram's discovery actually happens — carousels mostly reach
people who already follow you. This turns the same spec the carousel renderer
uses into a short vertical video, so one piece of content serves both surfaces.

Design: each slide is rendered by render_slides at its native 1080x1350 and
composited onto a 1080x1920 navy frame. Reusing the proven renderer keeps the
branding byte-identical to the carousels instead of maintaining a second layout.

Motion matters — a static slideshow gets poor retention — so each card gets a
slow zoom and cards cross-fade into each other.

Audio comes from audio.py, synthesised per post. Instagram's API cannot attach
trending or licensed audio, so it must be embedded here; silent Reels are
suppressed. Slide changes get an audible cue.

    python render_reel.py specs/_example.json
    python render_reel.py specs/_example.json --out queue/

Requires imageio-ffmpeg (ships its own ffmpeg binary; no system install) and
numpy (audio synthesis).
"""
import argparse, json, os, subprocess, sys, tempfile
from PIL import Image

import audio
import render_slides

W, H = 1080, 1920
FPS = 30

# Timing, in seconds. Dwell scales with how much text is actually on the
# slide (owner feedback 2026-08: fixed holds were too fast to read). The
# static values below are fallbacks for the no-spec path and the floor/cap
# system; spec-rendered slides use slide_seconds().
COVER_HOLD = 2.4
SLIDE_HOLD = 4.8
PROMPT_HOLD = 6.0        # code slides need dwell time
FINAL_HOLD = 4.5
ENDCARD_HOLD = 2.2       # the explicit follow ask, at peak attention
XFADE = 0.4
ZOOM = 0.05              # 5% drift over a card's hold

# A comfortable phone reader manages roughly this many characters per second;
# code/quoted rule text reads slower than prose.
CHAR_RATE = 14.0
CODE_RATE = 9.0

PROGRESS_H = 6


def _ffmpeg():
    try:
        import imageio_ffmpeg
    except ImportError:
        sys.exit("pip install imageio-ffmpeg")
    return imageio_ffmpeg.get_ffmpeg_exe()


def _flat(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return "".join(x.get("t", "") for x in v)


def slide_seconds(slide, is_last):
    """Dwell time a human needs to actually read this slide.

    Base + text-length at reading speed, clamped per slide kind so hooks stay
    snappy and no single card stalls the reel.
    """
    text = " ".join(_flat(slide.get(k)) for k in
                    ("eyebrow", "headline", "sub", "body", "label",
                     "stat", "cta_title", "cta_sub"))
    text += " " + " ".join(_flat(i) for i in (slide.get("items") or []))
    secs = 1.2 + len(text.strip()) / CHAR_RATE
    if slide.get("code"):
        secs += len(slide["code"]) / CODE_RATE
    kind = slide.get("kind")
    lo, hi = {"cover": (2.2, 4.5), "prompt": (5.0, 9.5)}.get(kind, (3.5, 8.0))
    if is_last:
        lo = max(lo, FINAL_HOLD)
    return max(lo, min(secs, hi))


def card(spec_slide, index, total):
    """One 1080x1920 frame: the carousel slide centred, plus a progress bar."""
    slide = render_slides.render_slide(spec_slide, index, total)
    # render_slides.BG is read at call time — configure() may have repointed it.
    frame = Image.new("RGB", (W, H), render_slides.BG)
    frame.paste(slide, (0, (H - slide.height) // 2))

    # Progress bar — tells the viewer how much is left, which helps retention.
    from PIL import ImageDraw
    d = ImageDraw.Draw(frame)
    y = H - 90
    d.rectangle([0, y, W, y + PROGRESS_H], fill=(28, 36, 52))
    d.rectangle([0, y, int(W * index / total), y + PROGRESS_H],
                fill=render_slides.COLORS["mark"])
    return frame


def zoomed(img, z):
    """Centre-crop zoom by factor z (>=1.0)."""
    if z <= 1.0:
        return img
    w, h = int(W / z), int(H / z)
    x, y = (W - w) // 2, (H - h) // 2
    return img.crop((x, y, x + w, y + h)).resize((W, H), Image.LANCZOS)


def frames(cards):
    """Yield every output frame: zooming holds, cross-faded between cards."""
    xf = int(XFADE * FPS)
    for i, (img, secs) in enumerate(cards):
        n = max(int(secs * FPS), 1)
        for f in range(n):
            cur = zoomed(img, 1.0 + ZOOM * (f / n))
            # Cross-fade the opening frames of every card after the first.
            if i and f < xf:
                prev_img, _ = cards[i - 1]
                prev = zoomed(prev_img, 1.0 + ZOOM)
                yield Image.blend(prev, cur, (f + 1) / xf)
            else:
                yield cur


def card_from_image(path, index, total):
    """Composite an already-rendered 1080x1350 slide PNG onto a 9:16 frame.

    Lets the existing hand-made queue become Reels without needing the original
    specs, which were never part of the handoff.
    """
    slide = Image.open(path).convert("RGB")
    frame = Image.new("RGB", (W, H), render_slides.BG)
    frame.paste(slide, ((W - slide.width) // 2, (H - slide.height) // 2))

    from PIL import ImageDraw
    d = ImageDraw.Draw(frame)
    y = H - 90
    d.rectangle([0, y, W, y + PROGRESS_H], fill=(28, 36, 52))
    d.rectangle([0, y, int(W * index / total), y + PROGRESS_H],
                fill=render_slides.COLORS["mark"])
    return frame


def endcard():
    """The follow ask: full-screen handle + one-line promise + logo/mark.

    Every reel ends with this — a stranger who watched to the end is at peak
    intent, and the reels never asked for the follow before. Text comes from
    the account's reel_endcard config via render_slides.configure().
    """
    from PIL import ImageDraw
    img = Image.new("RGB", (W, H), render_slides.BG)
    d = ImageDraw.Draw(img)
    cfg = render_slides.BRANDING.get("reel_endcard") or {}
    title = cfg.get("title") or f"Follow {render_slides.BRANDING['handle']}"
    sub = cfg.get("sub") or ""

    logo_path = render_slides.BRANDING.get("logo")
    y = 730
    if logo_path and os.path.exists(logo_path):
        logo, mask = render_slides._logo(120)
        img.paste(logo, ((W - 120) // 2, y), mask)
    else:
        render_slides.draw_mark(d, (W - 96) // 2, y, size=96)
    y += 190

    ft = render_slides.font("bold", 64)
    wpx = render_slides.measure(d, title, ft)
    d.text(((W - wpx) / 2, y), title, font=ft,
           fill=render_slides.COLORS["white"])
    y += 110

    if sub:
        fs = render_slides.font("regular", 36)
        lines = render_slides.wrap_segments(d, [{"t": sub, "c": "dim"}],
                                            "regular", 36, W - 240)
        for line in lines:
            lw = sum(render_slides.measure(d, w, f) for w, _, f in line) \
                + render_slides.measure(d, " ", fs) * (len(line) - 1)
            cx = (W - lw) / 2
            for i, (word, col, f) in enumerate(line):
                if i:
                    cx += render_slides.measure(d, " ", fs)
                d.text((cx, y), word, font=f, fill=col)
                cx += render_slides.measure(d, word, f)
            y += 52
    return img


def render_from_images(slug, image_paths, out_root="queue"):
    """Build a reel from existing slide images (no spec required)."""
    total = len(image_paths)
    cards = []
    for i, p in enumerate(image_paths):
        # No spec, so infer dwell: first slide is the hook, last is the CTA.
        secs = (COVER_HOLD if i == 0
                else FINAL_HOLD if i == total - 1
                else SLIDE_HOLD)
        cards.append((card_from_image(p, i + 1, total), secs))
    cards.append((endcard(), ENDCARD_HOLD))
    return _encode(cards, slug, out_root)


def render(spec, out_root="queue"):
    slides = spec["slides"]
    total = len(slides)
    cards = [
        (card(s, i + 1, total), slide_seconds(s, i == total - 1))
        for i, s in enumerate(slides)
    ]
    cards.append((endcard(), ENDCARD_HOLD))
    return _encode(cards, spec["slug"], out_root)


def timeline(cards):
    """Exact duration and slide-change times, in seconds.

    Mirrors the frame arithmetic in frames() so the audio bed lines up with the
    video sample-for-sample instead of drifting by a frame per card.
    """
    counts = [max(int(secs * FPS), 1) for _, secs in cards]
    starts = []
    running = 0
    for n in counts[:-1]:
        running += n
        starts.append(running / FPS)
    return sum(counts) / FPS, starts


def _encode(cards, slug, out_root):
    out_dir = os.path.join(out_root, slug)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "reel.mp4")

    # Reels published through the API cannot use Instagram's trending audio —
    # Meta exposes no such parameter, so audio has to be inside the file. We
    # synthesise an original bed (see audio.py) rather than ship a licensed
    # track. Silent Reels get suppressed, so this is not optional.
    duration, changes = timeline(cards)
    bed = os.path.join(tempfile.gettempdir(), f"bed-{slug}.wav")
    audio.write_wav(bed, audio.render_bed(duration, slug, accents=changes))

    cmd = [
        _ffmpeg(), "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", bed,
        "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.0",
        "-r", str(FPS), "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        path,
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    count = 0
    try:
        for frame in frames(cards):
            proc.stdin.write(frame.tobytes())
            count += 1
    finally:
        proc.stdin.close()
    err = proc.stderr.read().decode(errors="replace")
    if proc.wait() != 0:
        sys.exit(f"ffmpeg failed:\n{err[-2000:]}")
    try:
        os.remove(bed)
    except OSError:
        pass

    secs = count / FPS
    # Instagram Reels accept 3s-15min; well outside that is a spec error.
    if not 3 <= secs <= 90:
        print(f"::warning::reel is {secs:.1f}s — outside the 3-90s sweet spot")

    return path.replace("\\", "/"), secs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--out", default="queue")
    a = ap.parse_args()

    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)

    path, secs = render(spec, a.out)
    size = os.path.getsize(path) / 1e6
    print(f"{spec['slug']}: {secs:.1f}s reel -> {path} ({size:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
