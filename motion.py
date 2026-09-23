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
- The clip is BACKGROUND ONLY. With "mode": "topic" the local text model
  writes a visual METAPHOR for the post (motion.concepts) - symbolic objects
  and materials, never text, which a video model renders as gibberish; the
  words stay render_slides' job. Each account bans what must never appear
  ("negative", "visual_rules"): on inmigraforma no people, officers, flags or
  borders at all - a generated scene would picture an event that did not
  happen. Without "mode" the account's abstract "styles" are used.
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
import argparse, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile, time

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

# Said to the model on every clip, on every account. Each account adds its own
# bans in "motion_cover.negative" - inmigraforma forbids people, uniforms,
# flags and borders outright; leverageai allows a silhouette at a desk.
NEGATIVE = ("text, letters, words, numbers, captions, subtitles, watermark, "
            "logo, brand, signature, user interface, readable screen, "
            "face close-up, celebrity, gore, weapon, "
            "blurry, lowres, jpeg artifacts, flicker, distorted, deformed, "
            "overexposed, static, still image, flat, plain gradient, empty")


def negative(acct):
    extra = config(acct).get("negative")
    return f"{NEGATIVE}, {extra}" if extra else NEGATIVE


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
                              encoding="utf-8", errors="replace",
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


DIRECTOR = """You are the creative director for a four-second vertical video that plays BEHIND the headline of one social media post. The headline is drawn on top in the upper half of the frame by other software; your video is the picture underneath it. A viewer must connect the picture to THIS post within one second - a pretty picture about nothing is a failure.

First, say in one plain line what the post is actually about: the concrete thing, who it touches, what changed.

Then write two concepts, each a prompt for a video model:
1. LITERAL - the real scene or object this post is about, filmed like a film    still come to life: the actual place, tool, moment or people involved    (people are generated, never real or recognisable individuals).
2. METAPHOR - one physical object or scene that stands for the change the    post reports, doing something that SHOWS that change.

Each prompt: 45-80 words of plain visual English describing only what is SEEN - subject, the one clear action that visibly moves, setting, materials, lighting, colour palette, one camera move (slow dolly in, orbit, crane up, macro push, rack focus). Main subject in the LOWER half of the frame; the upper half darker and simpler, because the headline sits there. Cinematic, high contrast, shallow depth of field.

Prefer PEOPLE, PLACES and physical ACTION over screens: a person doing the thing, the place it happens, the machinery behind it. A laptop or phone screen is the weakest picture there is - use one only as a glow in a scene.

The video model cannot draw writing. So no word, number, date, label, sign, stamp, button, chart or document that has to be READ to be understood - show a date as an old wall calendar's pages lifting in a draft, not as "4 SEP".

Describe only what IS in the frame. Never mention text, words, letters, screens' contents, logos or anything that should be absent - naming a thing, even to exclude it, makes the video model draw it. Screens may glow, blurred. No brand products, no famous people."""

CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "what_the_post_is_about": {"type": "string"},
        "concepts": {
            "type": "array", "minItems": 2, "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["literal", "metaphor"]},
                    "idea": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["kind", "idea", "prompt"],
            },
        },
    },
    "required": ["what_the_post_is_about", "concepts"],
}


_READING = re.compile(
    # A quoted phrase (not a possessive apostrophe), a digit, or a word that
    # asks for writing.
    r"(?:^|(?<=\s))['\u2018\"\u201c][^'\u2019\"\u201d]{1,40}['\u2019\"\u201d]|"
    r"\d|\b(text|texts|word|words|letter|letters|"
    r"label|labeled|labelled|reads|reading|written|writing|sign|signs|stamp|"
    r"button|typed|caption|number|numerals?|date|dates|chart|headline|logo|"
    r"interface|ui|no faces?|no text)\b", re.I)


def _unreadable(prompt):
    """Why this prompt asks the video model to draw writing, or None."""
    hits = sorted({m.group(0).lower() for m in _READING.finditer(prompt)})
    return f"it asks for writing ({', '.join(hits)[:80]})" if hits else None


def _scrub(prompt):
    """Last resort: drop every sentence that still asks for writing."""
    keep = [s for s in re.split(r"(?<=[.!?])\s+", prompt) if not _READING.search(s)]
    return " ".join(keep) or prompt


def _post_text(spec):
    import hooks
    lines = []
    for sl in spec.get("slides") or []:
        for k in ("headline", "sub", "body"):
            v = hooks.flatten(sl.get(k))
            if v:
                lines.append(v)
        lines += [hooks.flatten(i) for i in sl.get("items") or []]
    return "\n".join(lines)


def concepts(acct, spec):
    """Two post-specific visual concepts, written by the local text model and
    cached beside the clips, so a re-render never changes its picture."""
    cache = os.path.join(CACHE, acct["slug"], f"{spec['slug']}.concepts.json")
    if os.path.exists(cache):
        return _load(cache)
    import llm
    cfg = config(acct)
    ask = (f"ACCOUNT: @{acct['username']} - {cfg.get('audience', '')}\n"
           f"PALETTE: {cfg.get('palette', 'deep navy with one accent colour')}\n"
           + (f"RULES FOR THIS ACCOUNT: {cfg['visual_rules']}\n"
              if cfg.get("visual_rules") else "")
           + f"\nTHE POST:\n{_post_text(spec)}\n\n"
           f"Two concepts for the video behind its cover.")
    msgs = [{"role": "user", "content": ask}]
    for attempt in range(3):
        data = llm.structured(DIRECTOR, None, CONCEPT_SCHEMA, require=("concepts",),
                              label=f"motion:{spec['slug']}", think=True,
                              temperature=0.8, messages=msgs)
        bad = [f"concept {i + 1}: {why}" for i, c in enumerate(data["concepts"])
               for why in [_unreadable(c.get("prompt", ""))] if why]
        if not bad:
            break
        # Asking beats scrubbing: a prompt with its quoted word cut out
        # usually still describes a sign, and the model draws the sign.
        print(f"  motion:{spec['slug']}: rewriting ({'; '.join(bad)})")
        msgs = msgs + [
            {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
            {"role": "user", "content": "Rewrite both prompts. " + "; ".join(bad)
             + ". Show it with people, places, objects and movement - "
               "nothing that has to be read."}]
    else:
        for c in data["concepts"]:
            c["prompt"] = _scrub(c.get("prompt", ""))
    about = data.get("what_the_post_is_about", "").strip()
    out = [{"idea": f"{c.get('kind', '')}: {c['idea'].strip()}",
            "prompt": c["prompt"].strip(), "about": about}
           for c in data["concepts"]][:2]
    with io.open(cache, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    return out


def _prompts(acct, spec, variants):
    """[(prompt, seed, idea)] for this post: topic concepts, else a style."""
    seed = int(hashlib.sha256(spec["slug"].encode()).hexdigest(), 16) % 2**31
    if config(acct).get("mode") == "topic":
        try:
            got = [(c["prompt"], seed + i, c["idea"])
                   for i, c in enumerate(concepts(acct, spec))]
            if got:
                return got[:variants]
        except Exception as e:                              # noqa: BLE001
            # No concept, no loss: fall back to the account's styles.
            print(f"  ::warning::motion concept for {spec['slug']} failed "
                  f"({type(e).__name__}: {e}) - using a style")
    style, sseed = prompt_for(spec["slug"], acct)
    return [(style, sseed, "style")] if style else []


def run(acct, only=None, out_root=None, use_synthetic=False, force=False,
        variants=1):
    """The stage. Returns how many Reels got a moving cover.

    With out_root (a trial), `only` may name several posts, comma-separated,
    in any status, and `variants` renders each concept as its own Reel.
    """
    cfg = config(acct)
    live = out_root is None
    if live and not cfg.get("enabled"):
        print(f"[{acct['slug']}] motion_cover off - nothing to do")
        return 0
    sched = _load(acct.queue)
    names = set(only.split(",")) if only else None
    if names and not live:
        todo = [p for p in sched["posts"] if p["id"] in names]
    else:
        todo = [p for p in sched["posts"]
                if p.get("status") == "queued" and p.get("format") == "reel"
                and (force or not p.get("motion"))
                and (names is None or p["id"] in names)]
        variants = 1
    if not todo:
        print(f"[{acct['slug']}] no queued Reel needs a moving cover")
        return 0

    cache = os.path.join(CACHE, acct["slug"])
    os.makedirs(cache, exist_ok=True)
    # Concepts first, while the text model is still loaded; the video model
    # then gets the whole GPU.
    jobs, plan = [], []
    for p in todo:
        spec = _load(os.path.join(acct.spec_dir, f"{p['id']}.json"))
        for k, (prompt, seed, idea) in enumerate(_prompts(acct, spec, variants), 1):
            tag = f"{p['id']}-v{k}" + ("-synthetic" if use_synthetic else "")
            out = os.path.join(cache, f"{tag}.mp4")
            plan.append((p, spec, k, out, prompt, seed, idea))
            print(f"  {p['id']} v{k}: {idea[:110]}")
            if os.path.exists(out) and not force:
                continue
            jobs.append({"prompt": prompt, "negative": negative(acct), "seed": seed,
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

    import render_reel, render_slides
    render_slides.configure(acct)
    done = 0
    for p, spec, k, out, prompt, seed, idea in plan:
        if not os.path.exists(out):
            print(f"  {p['id']} v{k}: no clip - cover stays still")
            continue
        if live:
            path, secs = render_reel.render(spec, acct.queue_dir, motion=out)
            p["motion"] = {"idea": idea, "prompt": prompt, "seed": seed,
                           "model": MODEL}
        else:
            trial = dict(spec, slug=f"{spec['slug']}-v{k}")
            path, secs = render_reel.render(trial, out_root, motion=out)
        print(f"  {p['id']} v{k}: moving cover -> {path} ({secs:.0f}s)")
        done += 1
    if live and done:
        with io.open(acct.queue, "w", encoding="utf-8") as f:
            json.dump(sched, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account")
    ap.add_argument("--post", help="only these post ids, comma-separated")
    ap.add_argument("--variants", type=int, default=1,
                    help="trial only: render each of the post's concepts")
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
        force=a.force, variants=a.variants)
    return 0


if __name__ == "__main__":
    sys.exit(main())
