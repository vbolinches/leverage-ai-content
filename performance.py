#!/usr/bin/env python3
"""Performance history and the brief that feeds it back into generation.

The Instagram API only reports current counts, so history has to be accumulated:
the monitor appends a dated snapshot on every run and commits it. `brief()` then
joins those numbers back to each post's spec — topic, slide mix, hook — so the
generator has something it can actually learn from.

Deliberately refuses to draw conclusions from thin data. An account with a
handful of posts and near-zero engagement produces noise, and a generator that
"learns" from noise converges on nonsense with total confidence. Below the
thresholds here, brief() returns None and generation runs unguided.
"""
import io, json, os
from datetime import date

import accounts

# Resolved per-invocation: every path belongs to the selected account, so one
# account's history can never leak into another's generation brief.
_ACCT = accounts.get()
HISTORY = _ACCT.performance
SPEC_DIR = _ACCT.spec_dir
QUEUE = _ACCT.queue

# Learning gates. Engagement on a new account is mostly noise; these are the
# floor at which differences between posts start to mean anything. Reach
# counts toward the signal: on a young account it is the first metric with
# real variance (reels 6..144 vs carousels 1..3), and it IS the goal.
MIN_POSTS = 6           # need enough published posts to compare
MIN_TOTAL_SIGNAL = 150  # ...and enough total signal (reach + engagement)


def _load(path, default):
    if not os.path.exists(path):
        return default
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(path, data):
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def media_to_slug():
    """Map published Instagram media IDs back to queue slugs."""
    sched = _load(QUEUE, {"posts": []})
    return {
        p["published_media_id"]: p["id"]
        for p in sched["posts"]
        if p.get("published_media_id")
    }


def record(followers, posts, today=None):
    """Append today's snapshot. Idempotent — re-running replaces today's entry."""
    today = today or date.today().isoformat()
    hist = _load(HISTORY, {"snapshots": []})
    m2s = media_to_slug()

    snapshot = {
        "date": today,
        "followers": followers,
        "posts": [
            {
                "slug": m2s.get(p["id"], p["id"]),
                "likes": p["likes"],
                "comments": p["comments"],
                # Present only once the token carries the insights scope.
                **{k: p[k] for k in ("reach", "saved", "shares") if k in p},
            }
            for p in posts
        ],
    }

    hist["snapshots"] = [s for s in hist["snapshots"] if s["date"] != today]
    hist["snapshots"].append(snapshot)
    hist["snapshots"].sort(key=lambda s: s["date"])
    _save(HISTORY, hist)
    return snapshot


def _spec_attributes(slug):
    """Pull the learnable attributes out of a post's spec."""
    path = os.path.join(SPEC_DIR, f"{slug}.json")
    if not os.path.exists(path):
        return None
    spec = _load(path, None)
    if not spec:
        return None

    slides = spec.get("slides", [])
    cover = next((s for s in slides if s.get("kind") == "cover"), {})
    hl = cover.get("headline")
    hook = hl if isinstance(hl, str) else "".join(x.get("t", "") for x in hl or [])

    caption = spec.get("caption", "")
    tags = [w for w in caption.split() if w.startswith("#")]

    return {
        "hook": hook,
        "slides": len(slides),
        "kinds": [s.get("kind") for s in slides],
        "has_prompt_slide": any(s.get("kind") == "prompt" for s in slides),
        "hashtags": tags[:10],
    }


def brief():
    """A performance brief for the generator, or None when data is too thin.

    Returns (text, stats). `text` is None when the learning gates aren't met —
    callers should then generate unguided rather than optimise against noise.
    """
    hist = _load(HISTORY, {"snapshots": []})
    if not hist["snapshots"]:
        return None, {"reason": "no snapshots recorded yet"}

    latest = hist["snapshots"][-1]
    scored = []
    for p in latest["posts"]:
        attrs = _spec_attributes(p["slug"])
        # Reach is the primary signal while the account is in the discovery
        # phase — it is what the strategy is optimising and the only metric
        # with real variance so far. Engagement outweighs it per-unit: a save
        # is worth far more than an impression. Accounts whose token cannot
        # read insights (reach absent) fall back to engagement-only naturally.
        score = ((p.get("reach") or 0)
                 + 3 * p["likes"]
                 + 9 * p["comments"]
                 + 15 * (p.get("saved") or 0)
                 + 15 * (p.get("shares") or 0))
        scored.append({**p, "score": score, "attrs": attrs})

    total = sum(p["score"] for p in scored)
    stats = {
        "posts": len(scored),
        "total_engagement": total,
        "followers": latest["followers"],
        "as_of": latest["date"],
    }

    if len(scored) < MIN_POSTS:
        stats["reason"] = (f"only {len(scored)} published posts "
                           f"(need {MIN_POSTS} to compare)")
        return None, stats
    if total < MIN_TOTAL_SIGNAL:
        stats["reason"] = (f"total signal {total} "
                           f"(need {MIN_TOTAL_SIGNAL} to rank posts)")
        return None, stats

    scored.sort(key=lambda p: p["score"], reverse=True)
    top, bottom = scored[:3], scored[-3:]

    # Follower trend over the recorded window.
    first = hist["snapshots"][0]
    growth = latest["followers"] - first["followers"]
    days = (date.fromisoformat(latest["date"])
            - date.fromisoformat(first["date"])).days or 1

    def describe(p):
        a = p["attrs"] or {}
        return (f'  {p["slug"]} — reach {p.get("reach", "?")}, '
                f'{p["likes"]}L {p["comments"]}C (score {p["score"]})\n'
                f'    hook: {a.get("hook", "?")}\n'
                f'    {a.get("slides", "?")} slides, '
                f'prompt slide: {a.get("has_prompt_slide")}, '
                f'tags: {" ".join(a.get("hashtags", [])[:6])}')

    text = (
        f"PERFORMANCE TO DATE (as of {latest['date']})\n"
        f"{latest['followers']} followers, {growth:+d} over {days} days across "
        f"{len(scored)} published posts.\n\n"
        f"BEST PERFORMING — do more of what these did:\n"
        + "\n".join(describe(p) for p in top)
        + "\n\nWORST PERFORMING — do less of what these did:\n"
        + "\n".join(describe(p) for p in bottom)
    )
    return text, stats


if __name__ == "__main__":
    text, stats = brief()
    print(json.dumps(stats, indent=2))
    print()
    print(text or f"(no brief — {stats.get('reason')})")
