#!/usr/bin/env python3
"""Does a stranger understand the post? Checked before it queues.

factcheck.py asks whether a post is TRUE to its source. Nothing asked whether
a person could UNDERSTAND it, and on 2026-09-23 the owner held a whole week of
posts that were true and still meaningless: "Spot-check skills. No AI
allowed", "Connect tools / Ask Gemini / to draft proposals", "Antes:
H-2Bpara 2027. Ahora: no.", a recap reading "1. Familiares: Dates". Every one
passed every check the pipeline had. The owner's rule: posts are for humans,
and the message must leave the person clear and without doubts.

So a second reader. The local model reads ONLY the slides - what a viewer
actually sees in a Reel, no caption - as someone who has never heard of the
tool, the law or the account, and must say in its own words what the post is
about, who it is for and what to do. Then it lists every doubt it was left
with. Its reading is compared with what the post is meant to say (the brief),
because a hook can be clear and still say the wrong thing: "Ahora: no" reads
as "no H-2B at all in 2027" when only the first half is full.

A small model checks better than it writes, as with the fact-check - but only
if it cannot wave a post through, so each doubt must quote the slide text it
is about, and a quote that is not on the slides is discarded in code.

    python clarity.py <spec.json>     # read one post as a stranger would
"""
import json, re, sys

import hooks
import llm

READER = """You are a first-time viewer scrolling Instagram. A Reel shows you \
the slides below, one after another. You see NOTHING else: no caption, no \
link, no context. Who you are - what you already know and what you do not - \
is described in the message. You are smart and busy, and you give up the \
moment you have to guess.

Report honestly what you understood, in your own words, then every doubt the \
slides left you with. A doubt is anything that makes you stop and wonder:
- a fragment with no subject or verb, so you cannot tell who does what;
- a term, acronym, form name or product you do not know and the slide does \
not explain;
- a step you could not actually do: where, which app, which button, which \
office, by when;
- whether it applies to you, and how you would know;
- a phrase that can be read two ways, or a hook that suggests more (or \
worse) than the rest of the slides support;
- a headline that says nothing ("The system", "What this means").

Mark each doubt's severity honestly:
- "blocks": you could not say what this is, whether it is for you, or what to \
do - or you would come away believing something the post does not mean.
- "minor": you understood and could act; a detail would be nicer.
If the slides are genuinely clear, the list is empty - do not invent doubts \
to look thorough. A Reel has a caption underneath it with the official link \
and the details, so "link en la descripción" / "link in the caption" points \
to something real and is not a doubt. Being told to check your own case, \
read the official notice or consult a lawyer is never a doubt either. And a \
sentence that honestly says the official notice does NOT specify something \
(who is exempt, what to do next) is the answer, not a doubt: the post is \
telling you the limit of what is known, which is better than a guess. Dates in the current year or the next are normal: today's \
date is given in the message. So are version numbers you have not seen \
(GPT-6, Gemini 3.8, Claude Opus 5.5): products you know release new \
versions all the time, and a newer number than you remember is not a doubt.
Quote each doubt's slide text EXACTLY as it appears."""

SCHEMA = {
    "type": "object",
    "properties": {
        "i_understood_it_is_about": {"type": "string"},
        "i_think_it_is_for": {"type": "string"},
        "what_i_would_do_now": {"type": "string"},
        "doubts": {
            "type": "array", "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "slide_text": {"type": "string"},
                    "my_doubt": {"type": "string"},
                    "severity": {"type": "string", "enum": ["blocks", "minor"]},
                },
                "required": ["slide_text", "my_doubt", "severity"],
            },
        },
        "matches_intended_message": {"type": "string",
                                     "enum": ["yes", "partly", "no"]},
        "what_i_got_wrong_or_missed": {"type": "string"},
    },
    "required": ["i_understood_it_is_about", "i_think_it_is_for",
                 "what_i_would_do_now", "doubts",
                 "matches_intended_message", "what_i_got_wrong_or_missed"],
}


def slides_text(post):
    """The post exactly as a Reel viewer meets it: slide by slide, no caption."""
    out = []
    for i, sl in enumerate(post.get("slides") or [], 1):
        parts = [hooks.flatten(sl.get(k)) for k in ("headline", "sub", "body",
                                                    "stat")]
        parts += [hooks.flatten(x) for x in sl.get("items") or []]
        if sl.get("code"):
            parts.append(sl["code"])
        text = "\n".join(p for p in parts if p)
        if text:
            out.append(f"SLIDE {i}:\n{text}")
    return "\n\n".join(out)


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def read(post, brief, acct, model=None, label=""):
    """Return (doubts, report). doubts: problems phrased for the author.
    An empty list means a stranger understood the post as intended."""
    from datetime import date
    lang = ("The slides are in Spanish; you read Spanish natively. Answer in "
            "English. " if acct.get("slide_language") == "es" else "")
    who = acct.get("clarity_reader") or (
        "someone who has never heard of the tool, law or program in the post")
    lang = f"TODAY IS {date.today().isoformat()}.\nWHO YOU ARE: {who}\n{lang}"
    intended = ""
    if brief:
        facts = "; ".join((brief.get("facts") or [])[:6])
        intended = (f"\n\n=====\n\nAFTER you have read the slides and formed "
                    f"your own understanding, compare it with what the post is "
                    f"MEANT to tell people:\n{brief.get('topic', '')}. {facts}")
    ask = (f"{lang}\n\nTHE SLIDES:\n\n{slides_text(post)}{intended}").strip()
    try:
        data = llm.structured(READER, ask, SCHEMA, model=model,
                              require=("i_understood_it_is_about",),
                              label=f"clarity:{label}", think=True,
                              temperature=0, max_tokens=12000)
    except llm.LLMError as e:
        return [f"the clarity read could not run ({e})"], {}

    seen = _norm(slides_text(post))
    doubts = []
    for d in data.get("doubts") or []:
        q = (d.get("slide_text") or "").strip()
        # A doubt about text that is not on the slides is the reader
        # inventing work; an unquoted one cannot be fixed. Either way, drop it.
        if not q or _norm(q) not in seen:
            continue
        if d.get("severity") != "blocks":
            continue
        doubts.append(f"A first-time reader stopped at {q!r}: {d.get('my_doubt', '').strip()}")
    # "no" alone is not trusted: on a correct post the reader once answered
    # "no" and then wrote "I don't think I missed anything". Only a stated
    # misreading counts.
    missed = (data.get("what_i_got_wrong_or_missed") or "").strip()
    if data.get("matches_intended_message") == "no" and missed and not re.search(
            r"(nothing|did not miss|didn't miss|don't think i missed|no misread)", missed, re.I):
        doubts.append("A first-time reader came away with the WRONG message: "
                      f"{missed}")
    return doubts, data


def main():
    import accounts
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        post = json.load(f)
    acct = accounts.get(path.replace("\\", "/").split("/")[1]
                        if path.replace("\\", "/").startswith("accounts/") else None)
    doubts, rep = read(post, None, acct, label=post.get("slug", ""))
    print(f"about:  {rep.get('i_understood_it_is_about')}")
    print(f"for:    {rep.get('i_think_it_is_for')}")
    print(f"do now: {rep.get('what_i_would_do_now')}")
    print(f"\n{len(doubts)} doubt(s)" + (":" if doubts else " - clear"))
    for d in doubts:
        print(f"  - {d}")
    return 1 if doubts else 0


if __name__ == "__main__":
    sys.exit(main())
