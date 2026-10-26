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
#
# stderr goes to the day's log rather than to devnull. It used to be devnull,
# and from 2026-09-28 every run died on `llm.ensure()` - the checker model had
# been removed from Ollama - with its "Model ... is not pulled" message thrown
# away. Each log held one header line for 26 nights and said nothing about why.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    _logs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(_logs, exist_ok=True)
    sys.stderr = open(os.path.join(
        _logs, f"batch-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"),
        "a", encoding="utf-8")

# Generate when fewer than this many posts are queued. A 7-post batch is about
# two weeks of runway and the run is free, so checking nightly means a queue
# never gets close to empty. Same number the workflow used.
REFILL_BELOW = 8

# Posts ATTEMPTED per account per night. With the dense writer a post takes
# about an hour to explain, write and check (2026-09-25: two attempts per
# account took four hours), so seven per account no longer fits a night. A
# rejected topic is replaced by a spare brief, which can add attempts.
DEFAULT_COUNT = 3


def child_python():
    """The interpreter for child processes: python.exe, never pythonw.exe.

    The scheduled task starts this file under pythonw.exe so no console
    flashes up, and sys.executable is then pythonw.exe too. Children started
    with it have no usable stdout, so the 2026-09-26/27 runs logged nothing
    at all from generate_batch.py for three hours. python.exe with
    CREATE_NO_WINDOW (see run()) is just as invisible and its output arrives.
    """
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        alt = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(alt):
            return alt
    return exe

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
                            env={**os.environ, **(env or {})},
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for line in proc.stdout:
        line = line.rstrip()
        print(line, flush=True)
        log.write(line + "\n")
        # Flushed per line: an account takes hours, and an unflushed log
        # looks exactly like a hung run (2026-10-23: 30 quiet minutes while
        # the writer was busy at 95%).
        log.flush()
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

    wanted = set()
    for a in (accounts.get(s) for s in enabled_slugs()):
        wanted.add(a.get("model", llm.DEFAULT_MODEL))
        wanted.add(a.get("writer_model") or a.get("model", llm.DEFAULT_MODEL))
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

    # Optional: only an account with motion_cover on needs it, and without
    # it those Reels keep still covers. Reported, never a FAIL.
    motion_on = [s for s in enabled_slugs()
                 if (accounts.get(s).get("motion_cover") or {}).get("enabled")]
    if motion_on:
        import motion
        mp = motion.python()
        print(f"  [{'x' if mp else '!'}] moving covers ({', '.join(motion_on)})  "
              f"{'ready - details: python motion.py --check' if mp else 'environment missing - covers stay still; python motion.py --check'}")

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
    ap.add_argument("--count", type=int, default=DEFAULT_COUNT)
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
        enabled = enabled_slugs()
        if a.account:
            if a.account not in enabled:
                say(f"::error::no enabled account {a.account!r}", log)
                return 1
            enabled = [a.account]

        # Every model each account uses - checker AND writer - checked here,
        # with the reason in the log. Both have gone missing: the checker was
        # removed from Ollama around 2026-09-28 and nothing ran for 26 nights.
        for slug in enabled:
            acct = accounts.get(slug)
            for m in {acct.get("model") or llm.DEFAULT_MODEL,
                      acct.get("writer_model") or acct.get("model") or llm.DEFAULT_MODEL}:
                try:
                    llm.ensure(m)
                except SystemExit as e:
                    say(f"::error::[{slug}] {e}. Nothing generated.", log)
                    return 1

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
                heartbeat(slug, "skipped", a.dry_run)
                if not a.dry_run:
                    commit_push(f"batch heartbeat {today} ({slug})", log, a.no_push)
                continue
            say(f"[{slug}] {n} posts queued — generating {a.count}", log)
            cmd = [child_python(), "-u", "generate_batch.py",
                   "--count", str(a.count)]
            if a.dry_run:
                cmd += ["--dry-run", "--out", os.path.join(REPO, "review_out", slug)]
            try:
                # Posts that pass every gate are QUEUED and publish on their
                # date (owner's call, 2026-10-26: "Release the held drafts
                # automatically from now on"). From 2026-09-24 they were held
                # for the owner to release; QUEUE_STATUS=held restores that.
                run(cmd, log, env={"ACCOUNT": slug, "PYTHONIOENCODING": "utf-8",
                                   "QUEUE_STATUS": os.environ.get("QUEUE_STATUS", "queued")})
            except RuntimeError as e:
                # One account failing must not cost the other its batch, the
                # same reason the workflow matrix ran with fail-fast: false.
                say(f"::error::[{slug}] generation failed: {e}", log)
                heartbeat(slug, "failed", a.dry_run)
                if not a.dry_run:
                    try:
                        commit_push(f"batch heartbeat {today} ({slug})", log, a.no_push)
                    except RuntimeError as e2:
                        say(f"::error::[{slug}] commit/push failed: {e2}", log)
                continue
            generated.append(slug)
            heartbeat(slug, "ran", a.dry_run)

            # Moving covers, once this account's writing is done: the video
            # model needs the GPU the text model was holding. Opt-in per
            # account; a failure leaves still covers and costs no post.
            if not a.dry_run and (accounts.get(slug).get("motion_cover") or {}).get("enabled"):
                llm.unload_all()
                try:
                    run([child_python(), "-u", "motion.py", "--account", slug],
                        log, env={"PYTHONIOENCODING": "utf-8"})
                except RuntimeError as e:
                    say(f"::warning::[{slug}] moving covers failed ({e}); "
                        f"Reels keep their still covers", log)

            # Commit and push THIS account now, not after the last one. A run
            # cut off by the task's time limit used to lose every account's
            # finished work, because the only commit came at the very end.
            if not a.dry_run:
                try:
                    commit_push(f"generated batch {today} ({slug})", log, a.no_push)
                except RuntimeError as e:
                    say(f"::error::[{slug}] commit/push failed: {e}", log)

        if not generated:
            say("nothing generated; queue unchanged", log)
        elif a.dry_run:
            say(f"dry run — review review_out/, nothing queued or committed", log)
        return 0


def heartbeat(slug, result, dry_run=False):
    """Record that tonight's run reached this account, whatever it produced.

    The queue-health workflow in Actions cannot see this PC's logs, so it
    used to guess whether the generator was alive from the queue itself -
    first from queued posts (failed every Monday while drafts were held),
    then from held drafts (failed on 2026-10-26 an hour after the drafts
    were released). This file is the direct signal: it is committed every
    night the job runs, so a stale date means the generator has stopped -
    PC off, Ollama down, a model missing - and nothing else does.
    """
    if dry_run:
        return
    path = os.path.join(REPO, "accounts", slug, "last_batch.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"last_run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "result": result, "queued_after": queued(slug)},
                  f, indent=2)
        f.write(chr(10))


def commit_push(message, log, no_push=False):
    """Commit accounts/ only, then pull --rebase and push.

    Only accounts/ is checked and committed. A bare `git commit` takes
    everything staged, and on 2026-09-12 that swept an unrelated staged
    workflow deletion into a commit titled "generated batch".
    """
    run(["git", "add", "accounts"], log)
    if run(["git", "diff", "--cached", "--quiet", "--", "accounts"],
           log, check=False) == 0:
        say("no changes to commit", log)
        return False
    run(["git", "commit", "-m", message, "--", "accounts"], log)
    if no_push:
        say("committed locally; push skipped (--no-push)", log)
        return True
    run(["git", "pull", "--rebase", "--autostash"], log)
    run(["git", "push"], log)
    say(f"pushed: {message}", log)
    return True


if __name__ == "__main__":
    sys.exit(main())
