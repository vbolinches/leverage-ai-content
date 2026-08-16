#!/usr/bin/env python3
"""Cover-hook scoring, shape taxonomy, and the log that keeps both honest.

The generator authors several candidate hooks per post instead of one. A
SEPARATE model call then grades them blind and the winner is spliced into the
cover slide. Two things make this more than a vibe check:

  Blindness.  The grader never learns which hook the author preferred, and the
  candidates are shuffled before grading. A model asked to grade its own draft
  grades the draft it just committed to; this removes that.

  Falsifiability.  Every candidate, its score and the winner are appended to
  accounts/<slug>/hooks.json against the slug. Once reach lands for that post,
  performance.py joins predicted score to actual reach — so over time the
  question "is the grader any good?" has an answer instead of a vibe. Nothing
  else here compounds; this does.

The rubric is not generic scroll-stopping advice. It is this account's own
Confirmed/Disproven sections from strategy.md, which were paid for with real
reach. Generic advice is what the account already ignores.

    python hooks.py                 # show the log for the selected account
    python hooks.py --shapes        # print the taxonomy
"""
import argparse, io, json, os, random, sys
from datetime import date

import accounts

SCORER_MODEL = "claude-opus-5"

# A hook's structure, independent of its topic. Recorded per post so the
# generator can be told what it has been leaning on, and so performance.py has
# an axis to learn along that is not the topic.
#
# "category" is in here precisely because strategy.md lists it as disproven —
# naming the failure mode lets the grader mark it and builds the evidence
# trail, rather than leaving it as an unlabelled low score.
SHAPES = {
    "number-claim": "a specific number, count or timeframe carries the hook",
    "contrarian": "denies something this audience currently believes",
    "mistake": "names an error the reader is probably making right now",
    "question": "asks something the reader cannot immediately answer",
    "before-after": "contrasts how it is now with how it could be",
    "change": "something changed and the reader has not adjusted to it yet",
    "cost": "names money, time or work being lost as we speak",
    "category": "labels a topic without a claim — the null shape",
}

# "Be blunt, a 6 out of 10 hook is a wasted video." Below this the batch gets
# one targeted retry for that post rather than shipping a hook we already
# believe is dead.
MIN_SCORE = 6.0

# Cover headlines overflow the canvas past this; render_slides wraps but the
# cover font is large. Kept in step with the hard limit in generate_batch.SCHEMA.
MAX_HEADLINE = 40


def flatten(rich):
    """Rich-text segments or a plain string -> plain string."""
    if isinstance(rich, str):
        return rich
    return "".join(seg.get("t", "") for seg in rich or [])


# Headlines are either a plain string or accent-coloured segments. Defined here
# rather than in generate_batch because the retry grader below needs it too and
# hooks.py is the lower module — generate_batch imports this, never the reverse.
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


# ------------------------------------------------------------------- store ---

def _load(acct):
    path = acct.path("hooks.json")
    if not os.path.exists(path):
        return {"entries": []}
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def log(acct, entry, path=None):
    """Append one post's hook decision. Re-running a slug replaces its entry.

    `path` overrides the destination so a dry run can write its log beside the
    rendered slides instead of into the live account, the same way
    generate_batch diverts strategy.md.
    """
    target = path or acct.path("hooks.json")
    store = {"entries": []}
    if os.path.exists(target):
        with io.open(target, encoding="utf-8") as f:
            store = json.load(f)
    store["entries"] = [e for e in store["entries"] if e["slug"] != entry["slug"]]
    store["entries"].append(entry)
    store["entries"].sort(key=lambda e: e["slug"])
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with io.open(target, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)


def score_for(acct, slug):
    """The winning hook's score and shape for a slug, or None. For the join."""
    for e in _load(acct)["entries"]:
        if e["slug"] == slug:
            return {"hook_score": e["chosen"]["score"],
                    "hook_shape": e["chosen"]["shape"]}
    return None


def recent_shapes(acct, n=10):
    """Shapes of the last n chosen hooks, newest first."""
    entries = sorted(_load(acct)["entries"], key=lambda e: e["slug"], reverse=True)
    return [e["chosen"]["shape"] for e in entries[:n]]


def ban_list(acct, window=4):
    """Prompt fragment enforcing hook-shape variety across the queue.

    Their method's one genuinely actionable line was "do not reuse a hook
    structure twice in the same week". The generator already avoids repeat
    topics; this is the same mechanism on a second axis.
    """
    catalogue = "\n".join(f"  {k} — {v}" for k, v in SHAPES.items())
    recent = recent_shapes(acct, n=window * 2)
    banned = recent[:window]
    text = f"HOOK SHAPES (the structure of a hook, not its topic):\n{catalogue}\n"
    if recent:
        text += ("\nShapes used on the most recent posts, newest first: "
                 + ", ".join(recent) + ".\n")
    if banned:
        text += ("Do NOT build any cover in this batch on these shapes — they "
                 "are still fresh in the feed: " + ", ".join(sorted(set(banned)))
                 + ".\n")
    text += ("Across the candidates for a single post, vary the SHAPE, not just "
             "the wording. Five rephrasings of one shape is one candidate.\n"
             "Never use the 'category' shape.")
    return text


# ------------------------------------------------------------------ rubric ---

def rubric(acct):
    """This account's earned rules — the Confirmed/Disproven strategy sections.

    Falls back to nothing rather than to generic advice: an empty rubric makes
    the grader say so, which is honest. Invented rules would not be.
    """
    if not os.path.exists(acct.strategy):
        return ""
    with io.open(acct.strategy, encoding="utf-8") as f:
        text = f.read()
    out, keep = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            keep = line.startswith("## Confirmed") or line.startswith("## Disproven")
        if keep:
            out.append(line)
    return "\n".join(out).strip()


# ----------------------------------------------------------------- grading ---

GRADE_TOOL = {
    "name": "submit_grades",
    "description": "Grade every candidate hook.",
    "input_schema": {
        "type": "object",
        "properties": {
            "gradings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string",
                                  "description": "The candidate's letter."},
                        "score": {
                            "type": "number",
                            "description": ("0-10: how hard is this to scroll "
                                            "past for THIS audience. Be blunt. "
                                            "A 6 is a wasted post."),
                        },
                        "shape": {"type": "string", "enum": list(SHAPES)},
                        "stopping": {
                            "type": "string",
                            "description": ("The exact word or beat that does "
                                            "the stopping, or 'nothing' if the "
                                            "hook has no such moment."),
                        },
                        "reason": {"type": "string",
                                   "description": "One line. No hedging."},
                    },
                    "required": ["label", "score", "shape", "stopping", "reason"],
                },
            }
        },
        "required": ["gradings"],
    },
}


def _grader_system(acct):
    ru = rubric(acct)
    ru_block = (f"\n\nWhat this account has already learned the hard way — these "
                f"rules were paid for with real reach, and they outrank your "
                f"instincts about hooks in general:\n\n{ru}"
                if ru else
                "\n\nThis account has no confirmed hook evidence yet. Say so in "
                "your reasons where it matters, and grade on the audience "
                "rather than on general copywriting taste.")
    return (
        f"You grade Instagram cover hooks for {acct.handle}, an account "
        f"publishing {acct['theme']}.\n\n"
        "You did not write these hooks and have no stake in any of them. You do "
        "not know which one the writer preferred, and the order is random — do "
        "not read anything into it.\n\n"
        "Grade each candidate on one axis only: how hard is it for THIS "
        "audience to scroll past. Not how clever, not how accurate, not how "
        "well written. Scrolling past is the default; a hook has to earn the "
        "stop.\n\n"
        "Be blunt and spread your scores. If everything lands 7-8 you have not "
        "graded anything. Most hooks are mediocre and should score like it."
        + ru_block
    )


def grade(client, acct, slug, pool):
    """Grade a pool of candidates blind. Returns them scored, best first.

    `pool` is a list of {"headline": rich, "sub": str, "authored": bool}.
    The shuffle is seeded from the slug so a dry run and the real run present
    the grader with the same order — reruns stay reproducible, matching how
    audio.py seeds its bed.
    """
    order = list(pool)
    random.Random(slug).shuffle(order)
    labelled = [dict(c, label=chr(ord("A") + i)) for i, c in enumerate(order)]

    listing = "\n\n".join(
        f"{c['label']}. {flatten(c['headline'])}"
        + (f"\n   sub: {c['sub']}" if c.get("sub") else "")
        for c in labelled
    )
    msg = (f"Grade every candidate below. Return one grading per label, "
           f"{len(labelled)} in total.\n\n{listing}")

    resp = client.messages.create(
        model=SCORER_MODEL,
        max_tokens=4000,
        system=_grader_system(acct),
        tools=[GRADE_TOOL],
        tool_choice={"type": "tool", "name": "submit_grades"},
        messages=[{"role": "user", "content": msg}],
    )

    grades = {}
    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_grades":
            for g in block.input["gradings"]:
                grades[g["label"]] = g
    if not grades:
        raise RuntimeError("grader returned no submit_grades call")

    scored = []
    for c in labelled:
        g = grades.get(c["label"])
        if not g:
            continue
        scored.append({**c, "score": float(g["score"]), "shape": g["shape"],
                       "stopping": g["stopping"], "reason": g["reason"]})
    if not scored:
        raise RuntimeError("grader graded none of the candidates")

    # Deterministic ordering: score desc, then label asc so ties never wobble
    # between runs.
    scored.sort(key=lambda c: (-c["score"], c["label"]))
    return scored


RETRY_TOOL = {
    "name": "submit_hooks",
    "description": "Submit replacement cover hooks.",
    "input_schema": {
        "type": "object",
        "properties": {
            "hooks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "headline": RICH_TEXT,
                        "sub": {"type": "string",
                                "description": "One line under the headline."},
                    },
                    "required": ["headline", "sub"],
                },
            }
        },
        "required": ["hooks"],
    },
}


def retry(client, acct, brand, post, scored, n=4):
    """Ask for fresh hooks after a round graded below threshold.

    Deliberately shows the writer the grades and the reasons: "rewrite the
    three weakest" only works if the writer knows why they were weak. The new
    hooks go back into the same blind grader with the old ones still in the
    pool, so a retry can lose.
    """
    verdicts = "\n".join(
        f"  {c['score']:.1f} [{c['shape']}] {flatten(c['headline'])}\n"
        f"      stops on: {c['stopping']} — {c['reason']}"
        for c in scored
    )
    msg = (
        f"Every cover hook written for this post graded below {MIN_SCORE}/10 on "
        f"how hard it is to scroll past. The grades and the grader's reasons:\n\n"
        f"{verdicts}\n\n"
        f"The post itself is fine — do not change what it is about. Its caption "
        f"opens:\n\n{post.get('caption', '')[:400]}\n\n"
        f"Write {n} genuinely different cover hooks for it. Fix what the grader "
        f"named. Vary the SHAPE, not the wording:\n\n{ban_list(acct)}\n\n"
        f"Headline must be at most {MAX_HEADLINE} characters — it is set very "
        f"large and overflows the canvas past that. Submit with submit_hooks."
    )
    resp = client.messages.create(
        model=SCORER_MODEL,
        max_tokens=4000,
        system=brand,
        tools=[RETRY_TOOL],
        tool_choice={"type": "tool", "name": "submit_hooks"},
        messages=[{"role": "user", "content": msg}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_hooks":
            return [{"headline": h["headline"], "sub": h.get("sub", ""),
                     "authored": False, "retry": True}
                    for h in block.input["hooks"]]
    return []


def report(slug, scored, chosen):
    """Human-readable grading table — the whole point of the dry run."""
    lines = [f"  {slug}: hook test"]
    for c in scored:
        mark = "->" if c is chosen else "  "
        src = "authored" if c.get("authored") else "candidate"
        lines.append(f"   {mark} {c['score']:>4.1f}  [{c['shape']}] "
                     f"{flatten(c['headline'])}   ({src})")
        lines.append(f"          stops on: {c['stopping']} — {c['reason']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shapes", action="store_true", help="print the taxonomy")
    a = ap.parse_args()

    if a.shapes:
        for k, v in SHAPES.items():
            print(f"{k:<14} {v}")
        return 0

    acct = accounts.get()
    store = _load(acct)
    if not store["entries"]:
        print(f"[{acct['slug']}] no hooks logged yet")
        return 0
    for e in store["entries"]:
        c = e["chosen"]
        print(f"{e['slug']:<34} {c['score']:>4.1f}  [{c['shape']:<13}] "
              f"{flatten(c['headline'])}")
        if e.get("retried"):
            print(f"{'':<34} (retried — first round scored below "
                  f"{e.get('threshold', MIN_SCORE)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
