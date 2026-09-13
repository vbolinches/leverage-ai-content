#!/usr/bin/env python3
"""The nightly batch run, on this machine instead of a GitHub runner.

Generation moved here on 2026-09-11 when the pipeline dropped the Anthropic
API for a local model. A GitHub-hosted runner cannot reach Ollama on
localhost, and a self-hosted runner on a PUBLIC repo would let a fork's pull
request execute code on this PC — so the schedule moved to the machine that
has the GPU, and the generate-batch workflow was retired.

This is the GitHub workflow's logic, unchanged: for every enabled account,
generate only when the queue is short, then commit and push so the daily
publish workflow (still in Actions, still on time) has posts to publish.

    python run_local_batch.py                 # the scheduled job
    python run_local_batch.py --force         # ignore the queue-length gate
    python run_local_batch.py --dry-run       # author and render, queue nothing
    python run_local_batch.py --no-push       # commit locally, push by hand

Install the 03:00 schedule with setup_schedule.ps1 (Windows) or
deploy/vps-setup.sh (Linux).
Everything it prints is appended to logs/batch-<date>.log.
"""
import argparse, json, os, shutil, subprocess, sys
from datetime import datetime, timezone

import accounts
import llm

REPO = os.path.dirname(os.path.abspath(__file__))

# The scheduled task runs this under pythonw.exe so no console window appears
# at 3am. pythonw has no stdout at all — sys.stdout is None — and the first
# print() would end the run with an AttributeError before anything generated.
# The log file is the real output either way.
if sys.stdout is None:
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")

# Generate when fewer than this many posts are queued. A 7-post batch is about
# two weeks of runway and the run is free, so checking nightly means a queue
# never gets close to empty. Same number the workflow used.
REFILL_BELOW = 8

LOG_DIR = os.path.join(REPO, "logs")


def say(msg, log):
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    log.write(line + "\n")
    log.flush()


def run(cmd, log, env=None, check=True):
    """Run a child process, streaming its output into the log as it arrives."""
    say(f"$ {' '.join(cmd)}", log)
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace",
                            env={**os.environ, **(env or {})})
    for line in proc.stdout:
        line = line.rstrip()
        print(line, flush=True)
        log.write(line + "\n")
    proc.wait()
    log.flush()
    if check and proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} exited {proc.returncode}")
    return proc.returncode


def check():
    """Everything a batch needs, verified before a batch needs it.

    Written for moving hosts. Each of these has its own failure signature at
    3am and none of them says what is actually missing: no curl and every
    government source 403s, no DejaVu and the slides render as boxes, no push
    credential and the batch is generated and then stranded on the box that
    made it. Run this first on any machine that is going to do the writing.
    """
    ok = True

    def row(label, good, detail=""):
        nonlocal ok
        ok = ok and good
        print(f"  {'PASS' if good else 'FAIL'}  {label:<22} {detail}")

    print(f"host {llm.HOST}")
    tags = llm.available()
    row("ollama", bool(tags), f"{len(tags)} model(s) pulled" if tags
        else "not answering — start it with `ollama serve`")

    wanted = {a.get("model", llm.DEFAULT_MODEL)
              for a in (accounts.get(s) for s in enabled_slugs())}
    for m in sorted(wanted):
        have = m in tags or m.split(":")[0] in {t.split(":")[0] for t in tags}
        row(f"model {m}", have, "" if have else f"run: ollama pull {m}")

    try:
        import ddgs                                     # noqa: F401
        row("ddgs", True, "web search available")
    except ImportError:
        row("ddgs", False, "run: pip install ddgs")

    row("curl", bool(shutil.which("curl")),
        "reads pages urllib cannot (uscis.gov, dhs.gov)")

    try:
        import render_slides
        render_slides.configure(accounts.get(enabled_slugs()[0]))
        render_slides.font("regular", 40)
        row("fonts", True, "DejaVu found")
    except Exception as e:                              # noqa: BLE001
        row("fonts", False, f"{e}")

    try:
        import imageio_ffmpeg
        row("ffmpeg", bool(imageio_ffmpeg.get_ffmpeg_exe()), "Reel encoding")
    except Exception as e:                              # noqa: BLE001
        row("ffmpeg", False, f"{e}")

    remote = subprocess.run(["git", "remote", "-v"], cwd=REPO,
                            capture_output=True, text=True)
    row("git remote", "push" in remote.stdout, remote.stdout.split("\n")[0][:60])
    # ls-remote, not `push --dry-run`: a push is refused when the branch is
    # merely behind, which is the normal state of a box that has not pulled
    # today and says nothing about whether the credential works. This asks
    # only the question being asked — can this host reach and authenticate
    # to the remote — which on a fresh VPS is the one that is usually no.
    ls = subprocess.run(["git", "ls-remote", "--heads", "origin"], cwd=REPO,
                        capture_output=True, text=True)
    row("git credentials", ls.returncode == 0,
        "can reach origin" if ls.returncode == 0
        else ls.stderr.strip().split("\n")[-1][:70])

    print("\nready" if ok else "\nNOT ready — fix the FAIL lines above")
    return 0 if ok else 1


def enabled_slugs():
    return [x["slug"] for x in json.loads(
        subprocess.run([sys.executable, "accounts.py", "--list-json"],
                       cwd=REPO, capture_output=True, text=True,
                       check=True).stdout)]


def queued(slug):
    acct = accounts.get(slug)
    with open(acct.queue, encoding="utf-8") as f:
        sched = json.load(f)
    return sum(1 for p in sched["posts"] if p["status"] == "queued")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=7)
    ap.add_argument("--force", action="store_true",
                    help="generate even when the queue still has runway")
    ap.add_argument("--dry-run", action="store_true",
                    help="author and render, but queue and commit nothing")
    ap.add_argument("--no-push", action="store_true",
                    help="commit locally but do not push")
    ap.add_argument("--account", default=None, help="only this slug")
    ap.add_argument("--check", action="store_true",
                    help="verify this host can run a batch, then exit")
    a = ap.parse_args()

    if a.check:
        return check()

    os.makedirs(LOG_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(os.path.join(LOG_DIR, f"batch-{today}.log"), "a",
              encoding="utf-8") as log:
        say(f"=== local batch run {datetime.now(timezone.utc).isoformat()} ===",
            log)

        # Ollama is started by its Windows service, but a machine that just
        # woke up may not have it yet. Fail here with the reason rather than
        # part-way through an account.
        if not llm.available():
            say(f"::error::Ollama is not answering at {llm.HOST}. Nothing "
                f"generated.", log)
            return 1
        llm.ensure()

        enabled = enabled_slugs()
        if a.account:
            if a.account not in enabled:
                say(f"::error::no enabled account {a.account!r}", log)
                return 1
            enabled = [a.account]

        # Pull first, and REFUSE TO GENERATE if it fails. The publish workflow
        # commits to main every day, so this box is always behind when it
        # wakes, and post numbers come from the local queue. On 2026-09-12
        # this pull failed on uncommitted files, the failure was ignored, and
        # the batch was numbered post51-56 against a queue 23 commits stale —
        # while GitHub already held post51, 54 and 55. Pushing it would have
        # published duplicate post IDs. A missed night costs a day of runway;
        # a duplicate costs the owner's trust. --autostash so a stray local
        # edit does not block the pull in the first place.
        try:
            run(["git", "pull", "--rebase", "--autostash"], log)
        except RuntimeError as e:
            say(f"::error::could not update from origin ({e}). Refusing to "
                f"generate against a stale queue — post numbers would collide "
                f"with posts already on GitHub. Nothing generated.", log)
            return 1

        generated = []
        for slug in enabled:
            n = queued(slug)
            if not a.force and n >= REFILL_BELOW:
                say(f"[{slug}] {n} posts queued (threshold {REFILL_BELOW}) "
                    f"— skipping", log)
                continue
            say(f"[{slug}] {n} posts queued — generating {a.count}", log)
            cmd = [sys.executable, "-u", "generate_batch.py",
                   "--count", str(a.count)]
            if a.dry_run:
                cmd += ["--dry-run", "--out", os.path.join(REPO, "review_out", slug)]
            try:
                run(cmd, log, env={"ACCOUNT": slug, "PYTHONIOENCODING": "utf-8"})
            except RuntimeError as e:
                # One account failing must not cost the other its batch, the
                # same reason the workflow matrix ran with fail-fast: false.
                say(f"::error::[{slug}] generation failed: {e}", log)
                continue
            generated.append(slug)

        if not generated:
            say("nothing generated; queue unchanged", log)
            return 0
        if a.dry_run:
            say(f"dry run — review review_out/, nothing queued or committed", log)
            return 0

        run(["git", "add", "accounts"], log)
        # Only accounts/ is checked for changes and only accounts/ is
        # committed. A bare `git commit` takes everything staged, and on
        # 2026-09-12 that swept an unrelated staged workflow deletion into a
        # commit titled "generated batch".
        staged = run(["git", "diff", "--cached", "--quiet", "--", "accounts"],
                     log, check=False)
        if staged == 0:
            say("no changes to commit", log)
            return 0
        run(["git", "commit", "-m",
             f"generated batch {today} ({', '.join(generated)})",
             "--", "accounts"], log)
        if a.no_push:
            say("committed locally; push skipped (--no-push)", log)
            return 0
        run(["git", "pull", "--rebase", "--autostash"], log)
        run(["git", "push"], log)
        say(f"pushed batches for {', '.join(generated)}", log)
        return 0


if __name__ == "__main__":
    sys.exit(main())
