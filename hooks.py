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
import llm

# Grading used to run on a stronger model than authoring (Opus grading
# Sonnet), so the grader brought an outside opinion as well as blindness.
# Running locally there is one model, and only the blindness survives — which
# is the property this file's docstring calls load-bearing, so the loop still
# works. Set OLLAMA_SCORER_MODEL to a second pulled model to get the
# independence back.
SCORER_MODEL = llm.SCORER_MODEL

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


# A note on what used to live here. The Anthropic version wrapped every
# system prompt in cached() and every tool list in cached_tools(), because
# prompt caching turned a repeated instruction block into a tenth of its
# input price. Ollama has no such parameter — it keeps a KV cache of the
# longest matching prompt PREFIX automatically, per loaded model. The
# discipline still pays, it is just no longer expressed in the request: keep
# the system prompt byte-identical across the calls in a run (grade() does,
# once per post) and the prefix is reused. Change the system prompt per call
# and it is recomputed every time. llm.log_usage prints the token counts that
# make that visible.


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

# Constrained decoding, not a forced tool call. Ollama's `format` takes a JSON
# schema and restricts the sampler to tokens that keep the output valid
# against it — a stricter guarantee than tool_choice ever gave us, and the
# reason a 30B local model can be trusted with this shape at all.
GRADE_SCHEMA = {
        "type": "object",
        "properties": {
            "gradings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string",
                                  "description": "The candidate's letter."},
                        "rank": {
                            "type": "integer",
                            "description": (
                                "1 = hardest to scroll past, then 2, 3... "
                                "Every candidate gets a DIFFERENT rank. Ranks "
                                "decide the winner, so commit to an order even "
                                "when two hooks feel equally strong — that "
                                "judgement is the job."
                            ),
                        },
                        "score": {
                            "type": "number",
                            "description": ("0-10: how hard is this to scroll "
                                            "past for THIS audience. Be blunt. "
                                            "A 6 is a wasted post. Decimals are "
                                            "fine and two hooks may share a "
                                            "score — rank is what separates "
                                            "them."),
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
                    "required": ["label", "rank", "score", "shape", "stopping",
                                 "reason"],
                },
            }
        },
        "required": ["gradings"],
}


def _accuracy_floor(acct):
    """The one exception to grading on stopping power alone.

    Scoring only "how hard is this to scroll past" is deliberate and it works
    — until the account reports news people act on. Grading a real batch, this
    rubric ranked "¿Usas el I-864 viejo? ¡Detente! / pierde tu Green Card"
    first *because* it was the most alarming, and using an outdated form does
    not cost anyone a Green Card they already hold. The grader was working as
    specified; the specification had a hole on exactly the account whose own
    arc says ACCURACY OVER BREVITY, because people act on it.

    Applied only to accounts that must cite an official source. Everywhere
    else the single axis stands.
    """
    if not acct.get("require_source_url"):
        return ""
    return (
        "\n\nONE OVERRIDE, and only this one. This account reports news that "
        "readers act on, so a hook that overstates what happened is not a "
        "strong hook — it is a false one. If a candidate claims a worse "
        "consequence than the facts support, names a penalty that does not "
        "follow, or would make a reader think they are in danger when they "
        "are not, score it below 3 and say so in the reason, however hard it "
        "is to scroll past. Alarm is not stopping power here; it is the thing "
        "that costs this account the trust it runs on."
    )


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
        "graded anything. Most hooks are mediocre and should score like it.\n\n"
        "Rank is what picks the winner, and no two candidates may share a rank. "
        "When two hooks feel equally strong, do not leave it a coin flip — "
        "decide, using what this account has actually learned. Refusing to "
        "separate them just hands the choice to whatever order they arrived in."
        + _accuracy_floor(acct)
        + ru_block
    )


def grade(acct, slug, pool):
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
           f"{len(labelled)} in total, ranked 1 to {len(labelled)} with no "
           f"rank used twice.\n\n{listing}")

    # The system prompt is byte-identical on every grade() call for this
    # account within a run, so Ollama reuses its KV prefix from post 2 onward.
    # Thinking stays ON here: this is a judgement call on eight short strings,
    # the output is tiny, and reasoning before scoring is what stops the model
    # handing everything a 7.
    data = llm.structured(
        _grader_system(acct),
        msg,
        GRADE_SCHEMA,
        model=SCORER_MODEL,
        require=("gradings",),
        label=f"grade:{slug}",
        think=True,
    )

    grades = {g["label"]: g for g in data.get("gradings") or []}
    if not grades:
        raise RuntimeError("grader returned no gradings")

    scored = []
    for c in labelled:
        g = grades.get(c["label"])
        if not g:
            continue
        scored.append({**c, "rank": int(g["rank"]), "score": float(g["score"]),
                       "shape": g["shape"], "stopping": g["stopping"],
                       "reason": g["reason"]})
    if not scored:
        raise RuntimeError("grader graded none of the candidates")

    # Rank, not score, decides the winner. Scores tie often — the first run of
    # this graded two hooks 8.0 and the winner fell out of a `sorted()` call,
    # which threw away the one the account's own evidence favoured. A forced
    # total order makes the grader own that call instead.
    if len({c["rank"] for c in scored}) != len(scored):
        print(f"::warning::grader reused a rank on {slug} — falling back to "
              f"score order for the duplicates.")
    scored.sort(key=lambda c: (c["rank"], -c["score"], c["label"]))
    return scored


RETRY_SCHEMA = {
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
}


def retry(acct, brand, post, scored, n=4, max_sub=None):
    """Ask for fresh hooks after a round graded below threshold.

    Deliberately shows the writer the grades and the reasons: "rewrite the
    three weakest" only works if the writer knows why they were weak. The new
    hooks go back into the same blind grader with the old ones still in the
    pool, so a retry can lose.
    """
    verdicts = "\n".join(
        f"  #{c['rank']} {c['score']:.1f} [{c['shape']}] "
        f"{flatten(c['headline'])}\n"
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
        f"large and overflows the canvas past that."
        + (f" The sub line must be at most {max_sub} characters; a longer one "
           f"is discarded unread." if max_sub else "")
    )
    # Writing, not judging — so thinking is off and the temperature is up.
    # A retry exists because the first four hooks were too alike; sampling at
    # the grader's temperature would hand back four more of the same.
    try:
        data = llm.structured(
            brand, msg, RETRY_SCHEMA,
            model=SCORER_MODEL,
            require=("hooks",),
            label=f"retry:{post.get('slug', '?')}",
            temperature=0.9,
        )
    except llm.LLMError as e:
        print(f"  ::warning::hook retry failed ({e}) — grading the originals")
        return []
    return [{"headline": h["headline"], "sub": h.get("sub", ""),
             "authored": False, "retry": True}
            for h in (data.get("hooks") or [])]


def report(slug, scored, chosen):
    """Human-readable grading table — the whole point of the dry run."""
    lines = [f"  {slug}: hook test"]
    for c in scored:
        mark = "->" if c is chosen else "  "
        src = "authored" if c.get("authored") else "candidate"
        lines.append(f"   {mark} #{c['rank']}  {c['score']:>4.1f}  "
                     f"[{c['shape']}] {flatten(c['headline'])}   ({src})")
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
