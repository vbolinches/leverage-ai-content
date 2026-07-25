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

    python render_reel.py specs/_example.json
    python render_reel.py specs/_example.json --out queue/

Requires imageio-ffmpeg (ships its own ffmpeg binary; no system install).
"""
import argparse, json, os, subprocess, sys
from PIL import Image

import render_slides

W, H = 1080, 1920
FPS = 30
BG = render_slides.BG

# Timing, in seconds. Hooks need less time than prompt slides people read.
COVER_HOLD = 2.6
SLIDE_HOLD = 3.4
PROMPT_HOLD = 5.0        # code slides need dwell time
FINAL_HOLD = 3.2
XFADE = 0.4
ZOOM = 0.05              # 5% drift over a card's hold

PROGRESS_H = 6


def _ffmpeg():
    try:
        import imageio_ffmpeg
    except ImportError:
        sys.exit("pip install imageio-ffmpeg")
    return imageio_ffmpeg.get_ffmpeg_exe()


def hold_for(slide, is_last):
    if is_last:
        return FINAL_HOLD
    kind = slide.get("kind")
    if kind == "cover":
        return COVER_HOLD
    if kind == "prompt" or slide.get("code"):
        return PROMPT_HOLD
    return SLIDE_HOLD


def card(spec_slide, index, total):
    """One 1080x1920 frame: the carousel slide centred, plus a progress bar."""
    slide = render_slides.render_slide(spec_slide, index, total)
    frame = Image.new("RGB", (W, H), BG)
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
    frame = Image.new("RGB", (W, H), BG)
    frame.paste(slide, ((W - slide.width) // 2, (H - slide.height) // 2))

    from PIL import ImageDraw
    d = ImageDraw.Draw(frame)
    y = H - 90
    d.rectangle([0, y, W, y + PROGRESS_H], fill=(28, 36, 52))
    d.rectangle([0, y, int(W * index / total), y + PROGRESS_H],
                fill=render_slides.COLORS["mark"])
    return frame


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
    return _encode(cards, slug, out_root)


def render(spec, out_root="queue"):
    slides = spec["slides"]
    total = len(slides)
    cards = [
        (card(s, i + 1, total), hold_for(s, i == total - 1))
        for i, s in enumerate(slides)
    ]
    return _encode(cards, spec["slug"], out_root)


def _encode(cards, slug, out_root):
    out_dir = os.path.join(out_root, slug)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "reel.mp4")

    cmd = [
        _ffmpeg(), "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        # Instagram is happier with an audio track present, even a silent one.
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.0",
        "-r", str(FPS), "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "128k",
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
