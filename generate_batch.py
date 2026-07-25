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

import render_slides

MODEL = "claude-sonnet-5"
QUEUE = "queue/schedule.json"
SPEC_DIR = "specs"

BRAND = """You write carousel posts for @leverageai.daily, an Instagram account \
publishing one practical AI workflow a day for knowledge workers.

Voice: direct, concrete, no hype. Never "revolutionary", "game-changer", \
"insane", "unlock". Short sentences. Second person. Always give something \
copy-pasteable. Name real tools (ChatGPT, Claude, NotebookLM, Perplexity, \
Canva, Notion). Be honest about limitations — say what a tool is bad at.

Each post is a 4-7 slide carousel following this arc:
  1. cover     - hook + one-line promise
  2-4. step    - concrete steps; one may be a `prompt` slide with a copyable prompt
  5. stat      - the payoff, one big number or phrase
  6. recap     - the system as 3-4 arrows, plus a save CTA

Captions: 2-4 short paragraphs, a save/comment prompt, the line \
"Follow @leverageai.daily for one practical AI workflow a day.", then 8-10 \
lowercase hashtags. Under 2000 characters."""

SCHEMA = """Return ONLY a JSON array of post objects. No prose, no markdown fence.

Each post:
{
  "slug": "postNN-short-kebab-topic",   // NN is provided to you
  "caption": "full Instagram caption",
  "slides": [ ...4 to 7 slide objects... ]
}

Slide kinds and their fields:
  {"kind":"cover","eyebrow":"WORKFLOW NNN","headline":[{"t":"Plain "},{"t":"accent.","c":"blue"}],"sub":"one line","footer_right":"SWIPE →"}
  {"kind":"step","eyebrow":"STEP 1","headline":"Short imperative.","body":[{"t":"explanation "},{"t":"key point.","c":"green","b":true}]}
  {"kind":"prompt","eyebrow":"STEP 2","headline":"Short.","sub":"one line","label":"COPY THIS PROMPT","code":"literal prompt\\nwith newlines"}
  {"kind":"stat","eyebrow":"THE PAYOFF","headline":"Framing question:","stat":"~big phrase"}
  {"kind":"recap","eyebrow":"RECAP","headline":"The system","items":["step","step","step"],"cta_title":"Save this for later","cta_sub":"Follow @leverageai.daily for one practical AI workflow a day","footer_right":"SAVE THIS ↓"}

Hard limits (text overflows the canvas otherwise):
  headline <= 40 chars   sub <= 90 chars   body <= 260 chars
  code <= 9 lines, each <= 46 chars       stat <= 22 chars
  items: 3-4, each <= 44 chars            eyebrow <= 16 chars"""


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
    "description": "Submit the authored carousel posts.",
    "input_schema": {
        "type": "object",
        "properties": {
            "posts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string"},
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
                    "required": ["slug", "caption", "slides"],
                },
            }
        },
        "required": ["posts"],
    },
}


def author(count, start_index, avoid):
    try:
        import anthropic
    except ImportError:
        sys.exit("pip install anthropic")

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY is not set — cannot author a batch.")

    slugs = ", ".join(f"post{start_index + i:02d}" for i in range(count))
    prompt = (
        f"{SCHEMA}\n\n"
        f"Write {count} posts and submit them with the submit_posts tool.\n"
        f"Use these slug prefixes in order: {slugs}.\n"
        f"Number the cover eyebrows WORKFLOW {start_index:03d} onward.\n\n"
        f"Already covered — pick genuinely different topics:\n"
        + "\n".join(f"- {t}" for t in avoid)
    )

    client = anthropic.Anthropic(api_key=key)
    # Streamed: the SDK requires it once max_tokens implies a possibly-long request.
    with client.messages.stream(
        model=MODEL,
        max_tokens=32000,
        system=BRAND,
        tools=[SUBMIT_TOOL],
        tool_choice={"type": "tool", "name": "submit_posts"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        resp = stream.get_final_message()

    if resp.stop_reason == "max_tokens":
        sys.exit(f"Response hit max_tokens before finishing. "
                 f"Try a smaller --count (asked for {count}).")

    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_posts":
            return block.input["posts"]

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
    slides = post.get("slides", [])
    if not 2 <= len(slides) <= 10:
        errs.append(f"{len(slides)} slides, Instagram carousels allow 2-10")
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
    a = ap.parse_args()

    sched = load_queue()
    start = next_index(sched)
    posts = author(a.count, start, existing_topics(sched))
    print(f"authored {len(posts)} posts starting at post{start:02d}")

    os.makedirs(SPEC_DIR, exist_ok=True)
    cursor = next_date(sched)
    added = 0

    for post in posts:
        errs = validate(post)
        if errs:
            print(f"REJECTED {post.get('slug')}: {'; '.join(errs)}")
            continue

        with open(f"{SPEC_DIR}/{post['slug']}.json", "w", encoding="utf-8") as f:
            json.dump(post, f, indent=2, ensure_ascii=False)

        slides = render_slides.render_post(post)
        print(f"  {post['slug']}: {len(slides)} slides rendered")

        if not a.dry_run:
            sched["posts"].append({
                "id": post["slug"],
                "date": cursor.isoformat(),
                "slides": slides,
                "caption": post["caption"],
                "status": "queued",
            })
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
