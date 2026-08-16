#!/usr/bin/env python3
"""Author the next batch of posts, render them, and append them to the queue.

Writes post specs with Claude, renders them through render_slides.py, and
extends queue/schedule.json so the daily publisher picks them up.

    python generate_batch.py --count 7
    python generate_batch.py --count 7 --dry-run    # author + render, don't queue

Requires ANTHROPIC_API_KEY. Nothing here touches Instagram — output lands in the
queue and the existing publish workflow does the posting.

NOTE ON REVIEW: anything this produces publishes unreviewed unless someone reads
it. --dry-run renders to specs/ and queue/ without scheduling, which is the
intended way to keep a human in the loop.
"""
import argparse, json, os, re, sys
from datetime import date, timedelta

import accounts
import render_slides

MODEL = "claude-opus-5"

# One account per invocation (ACCOUNT env / --account). Everything the
# generator reads and writes — queue, specs, strategy, brand voice — belongs
# to this account and no other.
ACCT = accounts.get()
render_slides.configure(ACCT)
QUEUE = ACCT.queue
SPEC_DIR = ACCT.spec_dir

# Server-side web search. Dynamic filtering is built into this tool version —
# do NOT also declare code_execution, a second execution environment confuses
# the model.
WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 8,
}

# The account-agnostic parts of the brand system prompt. Voice, theme, handle,
# CTA line — and optionally the slide arc and eyebrow series — come from
# accounts/<slug>/account.json; the no-reply-promise rule is universal — every
# account here publishes unattended.
DEFAULT_ARC = """Each post is a 4-7 slide carousel following this arc:
  1. cover     - hook + one-line promise
  2-4. step    - concrete steps; one may be a `prompt` slide with a copyable prompt
  5. stat      - the payoff, one big number or phrase
  6. recap     - the system as 3-4 arrows, plus a save CTA"""

SERIES = ACCT.get("eyebrow_prefix", "WORKFLOW")

BRAND = f"""You write carousel posts for {ACCT.handle}, an Instagram account \
publishing {ACCT['theme']}.

Voice: {ACCT['voice']}

{ACCT.get('post_arc') or DEFAULT_ARC}

Captions: 2-4 short paragraphs, a save/comment prompt, the line \
"{ACCT['cta_line']}", then 8-10 \
lowercase hashtags. Under 2000 characters.

NEVER promise anything you cannot deliver inside the post itself. This account \
publishes on a schedule and nobody is watching the inbox, so a caption must not \
say "comment X and I'll send you Y", offer a template, doc, checklist or DM, or \
imply a reply. Anyone who took you up on it would get silence. Everything of \
value must already be on the slides. Asking people to save, share or give an \
opinion in the comments is fine — promising them something back is not."""

SCHEMA = """Return ONLY a JSON array of post objects. No prose, no markdown fence.

Each post:
{
  "slug": "postNN-short-kebab-topic",   // NN is provided to you
  "caption": "full Instagram caption",
  "slides": [ ...4 to 7 slide objects... ]
}

Slide kinds and their fields:
  {"kind":"cover","eyebrow":"__SERIES__ NNN","headline":[{"t":"Plain "},{"t":"accent.","c":"blue"}],"sub":"one line","footer_right":"SWIPE →"}
  {"kind":"step","eyebrow":"STEP 1","headline":"Short imperative.","body":[{"t":"explanation "},{"t":"key point.","c":"green","b":true}]}
  {"kind":"prompt","eyebrow":"STEP 2","headline":"Short.","sub":"one line","label":"COPY THIS PROMPT","code":"literal prompt\\nwith newlines"}
  {"kind":"stat","eyebrow":"THE PAYOFF","headline":"Framing question:","stat":"~big phrase"}
  {"kind":"recap","eyebrow":"RECAP","headline":"The system","items":["step","step","step"],"cta_title":"Save this for later","cta_sub":"__CTA_SUB__","footer_right":"SAVE THIS ↓"}

Hard limits (text overflows the canvas otherwise):
  headline <= 40 chars   sub <= 90 chars   body <= 260 chars
  code <= 9 lines, each <= 46 chars       stat <= 22 chars
  items: 3-4, each <= 44 chars            eyebrow <= 16 chars"""

# The schema examples must show this account's CTA and eyebrow series, not
# placeholders — the model copies examples far more reliably than instructions.
SCHEMA = (SCHEMA.replace("__CTA_SUB__", ACCT["cta_line"].rstrip("."))
                .replace("__SERIES__", SERIES))


def load_queue():
    with open(QUEUE, encoding="utf-8") as f:
        return json.load(f)


def next_index(sched):
    n = 0
    for p in sched["posts"]:
        m = re.match(r"post(\d+)", p["id"])
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def next_date(sched):
    dates = [date.fromisoformat(p["date"]) for p in sched["posts"]]
    start = max(dates) if dates else date.today()
    return max(start + timedelta(days=1), date.today() + timedelta(days=1))


def existing_topics(sched):
    return [p["id"] for p in sched["posts"]]


# Forcing a tool call guarantees well-formed JSON. Parsing free text failed on
# literal newlines inside the `code` field, which are invalid inside a JSON string.
RICH_TEXT = {
    "anyOf": [
        {"type": "string"},
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "t": {"type": "string"},
                    "c": {"type": "string", "enum": ["blue", "green", "dim", "white"]},
                    "b": {"type": "boolean"},
                },
                "required": ["t"],
            },
        },
    ]
}

SUBMIT_TOOL = {
    "name": "submit_posts",
    "description": "Submit the authored carousel posts and the updated strategy.",
    "input_schema": {
        "type": "object",
        "properties": {
            "strategy": {
                "type": "string",
                "description": (
                    "The full rewritten contents of strategy.md. Move hypotheses "
                    "into Confirmed or Disproven only when the performance brief "
                    "actually supports it; otherwise carry them forward unchanged "
                    "and say what evidence is still missing. Never invent results."
                ),
            },
            "posts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string"},
                        "art": {
                            "type": "string",
                            "description": (
                                "Background illustration matching the post's "
                                "subject, drawn behind the cover. Pick the "
                                "closest; 'document' is the safe general "
                                "fallback."
                            ),
                            "enum": ["spreadsheet", "document", "email",
                                     "calendar", "chat", "checklist", "chart",
                                     "clock", "gavel", "passport", "scales",
                                     "building", "form", "lightbulb",
                                     "folder", "newspaper", "globe"],
                        },
                        "caption": {"type": "string"},
                        "slides": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {
                                        "type": "string",
                                        "enum": ["cover", "step", "prompt",
                                                 "stat", "recap"],
                                    },
                                    "eyebrow": {"type": "string"},
                                    "headline": RICH_TEXT,
                                    "sub": {"type": "string"},
                                    "body": RICH_TEXT,
                                    "label": {"type": "string"},
                                    "code": {"type": "string"},
                                    "stat": {"type": "string"},
                                    "items": {"type": "array",
                                              "items": {"type": "string"}},
                                    "cta_title": {"type": "string"},
                                    "cta_sub": {"type": "string"},
                                    "footer_right": {"type": "string"},
                                },
                                "required": ["kind"],
                            },
                        },
                    },
                    "required": ["slug", "art", "caption", "slides"],
                },
            }
        },
        "required": ["posts", "strategy"],
    },
}


def strategy_context():
    """The performance brief plus accumulated strategy, for the prompt."""
    import performance

    text, stats = performance.brief()
    strategy = ""
    if os.path.exists(ACCT.strategy):
        with open(ACCT.strategy, encoding="utf-8") as f:
            strategy = f.read()

    if text:
        perf = text
        note = (
            "Use the brief above. Double down on what the best performers share "
            "and stop repeating what the worst ones did. Then rewrite the "
            "strategy file, moving hypotheses into Confirmed or Disproven where "
            "the data now supports it."
        )
    else:
        perf = (f"PERFORMANCE: not yet meaningful — {stats.get('reason')}.\n"
                f"({stats.get('posts', 0)} posts, "
                f"{stats.get('total_engagement', 0)} total engagement.)")
        note = (
            "There is not enough data to optimise against. Do NOT invent "
            "conclusions or claim a hypothesis is confirmed. Write the best "
            "posts you can on the working hypotheses, and return the strategy "
            "file essentially unchanged apart from noting what is still unproven."
        )

    return f"{perf}\n\nCURRENT STRATEGY FILE:\n{strategy}\n\n{note}"


def author(count, start_index, avoid):
    try:
        import anthropic
    except ImportError:
        sys.exit("pip install anthropic")

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY is not set — cannot author a batch.")

    # Monitored-source signals (X, RSS, HN) come in as leads to investigate.
    # They suggest WHAT is being talked about; web_search still decides what is
    # actually true. Empty string when the ideas file is stale or absent.
    import ideas
    signals = ideas.digest(ACCT)

    slugs = ", ".join(f"post{start_index + i:02d}" for i in range(count))
    prompt = (
        "First, use web_search to ground this batch in what is actually current: "
        f"{ACCT['search_brief']}. "
        "Search before writing — do not rely on memory for facts that "
        "change often.\n\n"
        + (signals + "\n\n" if signals else "")
        + "Then write the posts and submit them with the submit_posts tool.\n\n"
        f"{SCHEMA}\n\n"
        f"Write {count} posts. Use these slug prefixes in order: {slugs}.\n"
        f"Number the cover eyebrows {SERIES} {start_index:03d} onward.\n\n"
        "Prefer topics that are timely without being disposable — a workflow that "
        "is useful because of something that changed recently, not news commentary. "
        "Never state a fact you did not verify by search.\n\n"
        f"Already covered — pick genuinely different topics:\n"
        + "\n".join(f"- {t}" for t in avoid)
        + "\n\n" + strategy_context()
    )

    client = anthropic.Anthropic(api_key=key)
    messages = [{"role": "user", "content": prompt}]

    # tool_choice stays "auto": forcing submit_posts would stop the model
    # searching first. Server-side search can hit its own iteration limit and
    # return stop_reason "pause_turn" — resend to resume, bounded.
    for _ in range(6):
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            system=BRAND,
            tools=[WEB_SEARCH_TOOL, SUBMIT_TOOL],
            messages=messages,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            resp = stream.get_final_message()

        if resp.stop_reason == "pause_turn":
            messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": resp.content},
            ]
            continue
        break
    else:
        sys.exit("Search kept pausing without finishing — try a smaller --count.")

    if resp.stop_reason == "refusal":
        sys.exit("Request was declined by safety classifiers, and the fallback "
                 "model declined too.")
    if resp.stop_reason == "max_tokens":
        sys.exit(f"Response hit max_tokens before finishing. "
                 f"Try a smaller --count (asked for {count}).")

    # tool_choice is "auto", so the model *can* skip searching and still submit
    # a valid batch. That batch would be written from training data alone — the
    # exact failure this pipeline exists to avoid, and invisible unless we say
    # so. Warn rather than fail: an empty queue is worse than a stale batch, and
    # the posts are still schema-valid.
    searches = sum(1 for b in resp.content if b.type == "server_tool_use")
    if searches:
        print(f"grounded on {searches} web search(es)")
    else:
        print("::warning::Batch was authored with ZERO web searches — content is "
              "from training data, not current sources. Review before it publishes.")

    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_posts":
            return block.input

    sys.exit(f"Model returned no submit_posts call (stop_reason={resp.stop_reason}).")


def validate(post):
    """Catch the failure modes that would silently ship a broken carousel."""
    errs = []
    if not post.get("slug"):
        errs.append("missing slug")
    cap = post.get("caption", "")
    if not cap:
        errs.append("missing caption")
    if len(cap) > 2200:
        errs.append(f"caption {len(cap)} chars > 2200 Instagram limit")
    # Nobody is watching the inbox, so a caption must not promise a reply —
    # in any of the languages this system publishes in.
    low = cap.lower()
    for phrase in ("i'll send", "ill send", "i will send", "dm me", "send you the",
                   "comment below and i", "and i'll share", "i'll dm", "i'll reply",
                   "te enviaré", "te envío", "te envio", "te mando", "te paso el",
                   "escríbeme y", "escribeme y", "mándame un dm", "mandame un dm",
                   "te respondo", "te comparto por dm"):
        if phrase in low:
            errs.append(f"caption promises a reply nobody will send: {phrase!r}")
    # e.g. 'Comment "LOG" for the template' / 'Comenta "VISA" y te mando...' —
    # an implicit promise of a hand-off.
    for pat in (r'comment\s+["“\']?\w+["”\']?\s+(?:for|and|to get)\b',
                r'comenta\s+["“\']?\w+["”\']?\s+y\s+te\b'):
        m = re.search(pat, low)
        if m:
            errs.append(f"caption implies a hand-off nobody will make: {m.group(0)!r}")

    slides = post.get("slides", [])
    if not 2 <= len(slides) <= 10:
        errs.append(f"{len(slides)} slides, Instagram carousels allow 2-10")

    # Accounts that mandate a plain-language slide (owner rule for
    # inmigraforma, 2026-08-16: a 10-year-old must understand every post)
    # cannot ship without it - the validator is the guarantee, not the prompt.
    req = ACCT.get("required_eyebrow")
    if req:
        eyebrows = [(s.get("eyebrow") or "").upper() for s in slides]
        if not any(req.upper() in e for e in eyebrows):
            errs.append(f"missing mandatory '{req}' slide")
        if "en palabras simples" in req.lower():
            low_cap = cap.lower()
            if "en palabras simples" not in low_cap:
                errs.append("caption missing the 'En palabras simples:' paragraph")
    for i, s in enumerate(slides, 1):
        hl = s.get("headline")
        flat = hl if isinstance(hl, str) else "".join(x.get("t", "") for x in hl or [])
        if len(flat) > 60:
            errs.append(f"slide {i}: headline {len(flat)} chars, will overflow")
        for ln in (s.get("code") or "").split("\n"):
            if len(ln) > 52:
                errs.append(f"slide {i}: code line {len(ln)} chars, will overflow")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true",
                    help="author and render, but do not add to the queue")
    ap.add_argument("--out", default=None,
                    help="where to render slides; use a separate dir for dry runs "
                         "so the review bundle holds only the new posts")
    ap.add_argument("--account", default=None,
                    help="account slug (defaults to ACCOUNT env / sole account)")
    a = ap.parse_args()

    if a.account and a.account != ACCT["slug"]:
        sys.exit(f"--account {a.account} conflicts with resolved account "
                 f"{ACCT['slug']} — set ACCOUNT={a.account} instead (module "
                 f"state is bound at import).")

    # No --out means rendering into the account's live queue directory.
    live = a.out is None
    out = a.out or ACCT.queue_dir

    sched = load_queue()
    start = next_index(sched)
    result = author(a.count, start, existing_topics(sched))
    posts = result["posts"]
    print(f"authored {len(posts)} posts starting at post{start:02d}")

    # The updated strategy is the loop's memory. On a dry run it lands beside
    # the rendered slides so it can be reviewed without touching the live file.
    strategy = result.get("strategy")
    if strategy:
        target = ACCT.strategy if live else os.path.join(out, "strategy.md")
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(strategy.rstrip() + "\n")
        print(f"strategy updated -> {target}")

    # For a dry run the specs go beside the rendered slides, so the review
    # bundle carries the captions too — slides alone are half a review.
    spec_dir = SPEC_DIR if live else os.path.join(out, "specs")
    os.makedirs(spec_dir, exist_ok=True)
    cursor = next_date(sched)
    added = 0

    # reel_ratio = reels per carousel (default 1 = alternate). Reels are the
    # only discovery surface — carousels reach single digits on a young
    # account — so growth-phase accounts run reel-heavy. The pattern is
    # deterministic by post number, so batches stay consistent across runs.
    ratio = int(ACCT.get("reel_ratio", 1))
    period = ratio + 1

    def is_reel(slug):
        m = re.match(r"post(\d+)", slug)
        n = int(m.group(1)) if m else 0
        return n % period != 0

    for post in posts:
        errs = validate(post)
        if errs:
            print(f"REJECTED {post.get('slug')}: {'; '.join(errs)}")
            continue

        with open(os.path.join(spec_dir, f"{post['slug']}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(post, f, indent=2, ensure_ascii=False)

        slides = render_slides.render_post(post, out)

        # Alternate surfaces: carousels reach existing followers, Reels reach
        # strangers. Alternating keeps one post a day while putting half the
        # schedule on the discovery surface.
        entry = {
            "id": post["slug"],
            "date": cursor.isoformat(),
            "slides": slides,
            "caption": post["caption"],
            "status": "queued",
        }

        if is_reel(post["slug"]):
            import render_reel
            video, secs = render_reel.render(post, out)
            entry["format"] = "reel"
            entry["video"] = video
            print(f"  {post['slug']}: {len(slides)} slides + {secs:.0f}s reel "
                  f"-> {out}/")
        else:
            print(f"  {post['slug']}: {len(slides)} slides -> {out}/")

        if not a.dry_run:
            sched["posts"].append(entry)
            cursor += timedelta(days=1)
            added += 1

    if a.dry_run:
        print("\ndry run — nothing queued. Review specs/ and queue/, then re-run without --dry-run.")
        return 0

    if not added:
        print("\nnothing passed validation; queue unchanged")
        return 1

    with open(QUEUE, "w", encoding="utf-8") as f:
        json.dump(sched, f, indent=2, ensure_ascii=False)

    dates = [p["date"] for p in sched["posts"]]
    assert len(set(dates)) == len(dates), "duplicate dates in queue"
    print(f"\nqueued {added} posts through {max(dates)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
