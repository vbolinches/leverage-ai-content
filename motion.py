#!/usr/bin/env python3
"""Moving covers: a few seconds of generated motion behind each Reel's hook.

A Reel's first second decides whether a stranger stays, and ours opened on a
still slide. This stage runs AFTER the night's writing, when the GPU is free:
for each queued Reel without one, it generates a short abstract clip with a
local video model (motion_worker.py) and re-renders the Reel with the clip
behind the cover (render_reel.MotionCover). Everything else about the Reel -
text, timing, audio - is unchanged.

Rules this module keeps:

- Off unless the account says so: "motion_cover": {"enabled": true, ...}.
- The clip is BACKGROUND ONLY and ABSTRACT: light, texture, colour, slow
  movement. Never people, places, documents, flags, uniforms or text. A
  generated officer or border scene on an immigration account would be a
  picture of something that did not happen; a video model's "text" is
  gibberish. The words stay render_slides' job.
- It can never cost a post. No clip, a failed clip, a missing model: the Reel
  keeps the still cover it already has.
- Free and local: Wan 2.1 1.3B (Apache-2.0) in its own environment
  (~/.cache/leverage-motion/venv), so diffusers never touches the main
  interpreter.

    python motion.py --check                       # is this host ready?
    python motion.py --account leverageai          # the nightly stage
    python motion.py --account leverageai --post post86-x --out review_out/motion
    python motion.py --account leverageai --post post86-x --synthetic --out ...
"""
import argparse, hashlib, io, json, os, shutil, subprocess, sys, tempfile, time

import accounts

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "motion_cache")          # gitignored; the repo is public
# Outside the repo on purpose: the repo lives in a synced Google Drive folder
# on the owner's PC, and a Python environment is thousands of small files.
VENV = os.environ.get("MOTION_VENV") or os.path.join(
    os.path.expanduser("~"), ".cache", "leverage-motion", "venv")
WORKER = os.path.join(HERE, "motion_worker.py")
MODEL = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
PER_CLIP_TIMEOUT = 15 * 60

# Said to the model on every clip, whatever the account's styles ask for.
NEGATIVE = ("text, letters, words, numbers, captions, subtitles, watermark, "
            "logo, signature, people, person, face, hands, body, crowd, "
            "police, officer, uniform, soldier, flag, border, wall, fence, "
            "document, passport, money, building, city, car, "
            "blurry, lowres, jpeg artifacts, flicker, distorted, "
            "overexposed, static, still image")


def python():
    """The motion environment's interpreter, on any host."""
    env = os.environ.get("MOTION_PYTHON")
    if env:
        return env
    for p in (os.path.join(VENV, "Scripts", "python.exe"),
              os.path.join(VENV, "bin", "python")):
        if os.path.exists(p):
            return p
    return None


def config(acct):
    return acct.get("motion_cover") or {}


def prompt_for(post_id, acct):
    """One of the account's abstract styles, chosen by the post id.

    Deterministic, so a re-render gets the same clip; varied, so a week of
    Reels does not open on the same picture. The topic is deliberately NOT in
    the prompt: asked for "visa bulletin", a video model draws a visa.
    """
    styles = config(acct).get("styles") or []
    if not styles:
        return None, None
    h = int(hashlib.sha256(post_id.encode()).hexdigest(), 16)
    style = styles[h % len(styles)]
    seed = h % 2**31
    return style, seed


def check():
    """Everything the stage needs, printed as a checklist. 0 if ready."""
    ok = True

    def row(name, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print(f"  [{'x' if good else ' '}] {name}  {detail}")

    py = python()
    row("motion environment", py, py or f"create it: python -m venv "
        f"--system-site-packages {VENV}")
    if py:
        probe = subprocess.run(
            [py, "-c", "import torch, diffusers, transformers; "
             "print(torch.cuda.is_available(), diffusers.__version__)"],
            capture_output=True, text=True)
        row("torch + diffusers", probe.returncode == 0,
            (probe.stdout or probe.stderr).strip().splitlines()[-1][:90]
            if (probe.stdout or probe.stderr) else "")
    hub = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub",
                       "models--" + MODEL.replace("/", "--"))
    row("model downloaded", os.path.isdir(hub), MODEL)
    for a in accounts.list_accounts():
        print(f"  {a['slug']}: motion_cover "
              f"{'ON' if config(a).get('enabled') else 'off'}")
    return 0 if ok else 1


def synthetic(out, seed=0, seconds=4):
    """A moving abstract clip made by ffmpeg alone - no model.

    Tests everything downstream of the model (decode, dim, cut-out,
    ping-pong, encode) on a host where the model is not installed yet.
    """
    import render_reel
    c0, c1 = "0x0b1f3a", "0x3b1d6e"
    cmd = [render_reel._ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
           f"gradients=s=480x832:c0={c0}:c1={c1}:c2=0x0e6b8a:n=3:"
           f"speed=0.015:seed={seed}:d={seconds}:r=16",
           "-vf", "noise=alls=10:allf=t,gblur=sigma=6",
           "-pix_fmt", "yuv420p", out]
    subprocess.run(cmd, check=True)
    return out


def generate(jobs):
    """Run the worker over jobs; return {out_path: True} for the clips made."""
    py = python()
    if not py or not jobs:
        return {}
    import llm
    freed = llm.unload_all()
    if freed:
        print(f"  motion: unloaded {', '.join(freed)} to free the GPU")
    fd, job_file = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(jobs, f)
    try:
        proc = subprocess.run([py, "-u", WORKER, job_file], cwd=HERE,
                              capture_output=True, text=True,
                              timeout=PER_CLIP_TIMEOUT * len(jobs) + 600)
        for line in (proc.stdout or "").splitlines():
            print(f"  motion: {line}")
        if proc.returncode and proc.stderr:
            print(f"  ::warning::motion worker: {proc.stderr.strip()[-600:]}")
    except subprocess.TimeoutExpired:
        print("  ::warning::motion worker timed out - covers stay still")
    finally:
        os.remove(job_file)
    return {j["out"]: True for j in jobs
            if os.path.exists(j["out"]) and os.path.getsize(j["out"]) > 10_000}


def _load(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def rerender(acct, post_id, clip, out_root):
    """Re-render one post's Reel with the clip behind its cover."""
    import render_reel, render_slides
    render_slides.configure(acct)
    spec = _load(os.path.join(acct.spec_dir, f"{post_id}.json"))
    return render_reel.render(spec, out_root, motion=clip)


def run(acct, only=None, out_root=None, use_synthetic=False, force=False):
    """The stage. Returns how many Reels got a moving cover."""
    cfg = config(acct)
    live = out_root is None
    if live and not cfg.get("enabled"):
        print(f"[{acct['slug']}] motion_cover off - nothing to do")
        return 0
    sched = _load(acct.queue)
    todo = [p for p in sched["posts"]
            if p.get("status") == "queued" and p.get("format") == "reel"
            and (force or not p.get("motion"))
            and (only is None or p["id"] == only)]
    if not todo:
        print(f"[{acct['slug']}] no queued Reel needs a moving cover")
        return 0

    cache = os.path.join(CACHE, acct["slug"])
    os.makedirs(cache, exist_ok=True)
    jobs, meta = [], {}
    for p in todo:
        style, seed = prompt_for(p["id"], acct)
        if not style:
            print(f"[{acct['slug']}] motion_cover has no styles configured")
            return 0
        out = os.path.join(cache, f"{p['id']}{'-synthetic' if use_synthetic else ''}.mp4")
        meta[p["id"]] = (out, style, seed)
        if os.path.exists(out) and not force:
            continue
        jobs.append({"prompt": style, "negative": NEGATIVE, "seed": seed,
                     "out": out, "steps": cfg.get("steps", 30)})

    t = time.time()
    if use_synthetic:
        for j in jobs:
            synthetic(j["out"], seed=j["seed"] % 1000)
    elif jobs:
        if not python():
            print(f"[{acct['slug']}] ::warning::motion environment missing - "
                  f"run `python motion.py --check`; covers stay still")
            return 0
        generate(jobs)
    if jobs:
        print(f"[{acct['slug']}] {len(jobs)} clip(s) in {time.time() - t:.0f}s")

    done = 0
    for p in todo:
        out, style, seed = meta[p["id"]]
        if not os.path.exists(out):
            print(f"  {p['id']}: no clip - cover stays still")
            continue
        path, secs = rerender(acct, p["id"], out, out_root or acct.queue_dir)
        print(f"  {p['id']}: moving cover -> {path} ({secs:.0f}s)")
        if live:
            p["motion"] = {"style": style, "seed": seed, "model": MODEL}
        done += 1
    if live and done:
        with io.open(acct.queue, "w", encoding="utf-8") as f:
            json.dump(sched, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account")
    ap.add_argument("--post", help="only this post id")
    ap.add_argument("--out", help="render here instead of the queue (a test; "
                                  "the schedule is not touched)")
    ap.add_argument("--synthetic", action="store_true",
                    help="use an ffmpeg-made clip instead of the model")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if a clip or moving cover exists")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        return check()
    acct = accounts.get(a.account)
    run(acct, only=a.post, out_root=a.out, use_synthetic=a.synthetic,
        force=a.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
