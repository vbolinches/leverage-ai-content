#!/usr/bin/env python3
"""Author the next batch of posts, render them, and append them to the queue.

Writes post specs with a local model, renders them through render_slides.py,
and extends queue/schedule.json so the daily publisher picks them up.

    python generate_batch.py --count 7
    python generate_batch.py --count 7 --dry-run    # author + render, don't queue

Requires a local Ollama with the authoring model pulled (see llm.py). Nothing
here touches Instagram — output lands in the queue and the existing publish
workflow does the posting.

NOTE ON REVIEW: anything this produces publishes unreviewed unless someone reads
it. --dry-run renders to specs/ and queue/ without scheduling, which is the
intended way to keep a human in the loop.
"""
import argparse, json, os, re, sys
from datetime import date, timedelta

import accounts
import hooks
import llm
import render_slides
import clarity
import factcheck
import search

# Authoring model. Per-account override via "model" in account.json, and a
# machine-wide override via OLLAMA_MODEL. Runs locally through Ollama since
# 2026-09-11; see llm.py for why the default is a 30B mixture-of-experts.
# The generator's value is in careful source verification and honest strategy
# reasoning - if a batch's quality slips, this is the first knob to revisit.
DEFAULT_MODEL = llm.DEFAULT_MODEL

# One account per invocation (ACCOUNT env / --account). Everything the
# generator reads and writes — queue, specs, strategy, brand voice — belongs
# to this account and no other.
ACCT = accounts.get()
render_slides.configure(ACCT)
QUEUE = ACCT.queue
SPEC_DIR = ACCT.spec_dir
MODEL = ACCT.get("model", DEFAULT_MODEL)
# The WRITER may differ from the checker. qwen3:30b-a3b checks well and
# writes badly (point 14): 0 of 14 posts met the owner's clarity bar. A
# dense model writes; the cheap one keeps reading, fact-checking and grading
# - and a writer judged by a reader it did not train with is the point.
WRITER = ACCT.get("writer_model") or MODEL

# account.json used to name an Anthropic model. Leaving a stale "claude-*" in
# there would send every request to a model Ollama has never heard of, and the
# error (a 404 from /api/chat) says nothing about why — so name the problem.
if MODEL.startswith("claude-"):
    print(f"::warning::{ACCT['slug']}'s account.json still names {MODEL!r}, "
          f"which is an Anthropic model — this pipeline runs on Ollama now. "
          f"Using {DEFAULT_MODEL} instead; update the account's \"model\" key.")
    MODEL = DEFAULT_MODEL

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

# Accounts that report news owe the reader the original document: a named
# source nobody can open is not a citation. But an invented URL is far worse
# than none, so the rule is bound to what web search actually returned - and
# validate() re-checks the domain before anything is queued.
if ACCT.get("require_source_url"):
    BRAND += ("\n\nSOURCES ARE MANDATORY AND MUST BE REAL. Every caption ends "
              "with a 'Fuente oficial:' line carrying the full https:// URL of "
              "the exact official page you consulted - the specific notice, rule "
              "or page, never the site's home page. Use ONLY URLs that came back "
              "from your web searches in this session and that you actually "
              "read. Never guess, shorten, reconstruct or 'fix' a URL: a link "
              "that 404s destroys the credibility of an account whose whole "
              "promise is that the news is verifiable. If you cannot produce a "
              "real URL for a claim, write about something else.")

SCHEMA = """Return ONE post object as JSON. No prose, no markdown fence.

{
  "slug": "postNN-short-kebab-topic",   // NN is provided to you
  "art": "one of the listed illustrations",
  "caption": "full Instagram caption",
  "hook_candidates": [ ...exactly 5 alternative covers... ],
  "slides": [ ...4 to 7 slide objects... ]
}

hook_candidates are five OTHER ways to open the post — {"headline":...,"sub":...},
same shape as the cover's own. Write the cover you believe in, then five real
alternatives built on different hook shapes. They are graded blind against your
cover by a separate reader and the winner replaces it, so a lazy candidate is a
wasted slot, and your own cover can lose.

Slide kinds and their fields:
  {"kind":"cover","eyebrow":"__SERIES__ NNN","headline":[{"t":"Plain "},{"t":"accent.","c":"blue"}],"sub":"one line","footer_right":"SWIPE →"}
__REQUIRED_SLIDE__  {"kind":"step","eyebrow":"STEP 1","headline":"Open the right place first.","body":[{"t":"One complete sentence: which app, where in it, and what to click or paste. "},{"t":"The result, in plain words.","c":"green","b":true}]}
  {"kind":"prompt","eyebrow":"STEP 2","headline":"Paste this into the chat.","sub":"A full sentence saying where to paste it.","label":"COPY THIS PROMPT","code":"literal prompt\\nwith newlines"}
  {"kind":"stat","eyebrow":"THE PAYOFF","headline":"A full sentence that sets up the number.","stat":"the number from the source"}
  {"kind":"recap","eyebrow":"RECAP","headline":"Do this today","items":["A complete short sentence a stranger understands.","The second thing to do, as a full sentence.","What you get, as a full sentence."],"cta_title":"Save this for later","cta_sub":"__CTA_SUB__","footer_right":"SAVE THIS ↓"}

Hard limits (text overflows the canvas otherwise):
__LIMITS__"""

# The schema examples must show this account's CTA and eyebrow series, not
# placeholders — the model copies examples far more reliably than instructions.
_REQ = ACCT.get("required_eyebrow")
_REQ_LINE = ""
if _REQ:
    _REQ_LINE = (
        '  {"kind":"step","eyebrow":"' + _REQ + '","headline":"La noticia, en una frase completa.",'
        '"body":[{"t":"2-3 frases de todos los días, sin jerga. "},'
        '{"t":"La idea más importante, en una frase.","c":"green","b":true}]}'
        '   <- MANDATORY as slide 2 of EVERY post' + chr(10)
    )
_GLOSS = ACCT.get("legal_gloss_eyebrow")
if _GLOSS:
    _REQ_LINE += (
        '  {"kind":"step","eyebrow":"' + _GLOSS + '","headline":"Qué dice ese texto.",'
        '"body":[{"t":"Qué dice en palabras normales, qué significa para ti en el "},'
        '{"t":"día a día, y qué pasa si lo ignoras.","c":"green","b":true}]}'
        '   <- MANDATORY whenever a slide quotes official English text' + chr(10)
    )
# Slide text limits are per-account: an account whose Reels must land in 30
# seconds needs a far tighter budget than the canvas alone would impose,
# because on a Reel the binding constraint is reading time, not pixels.
_LIM = {"headline": 40, "sub": 90, "body": 260, "items": 44, "stat": 22,
        "eyebrow": 16}
_LIM.update(ACCT.get("slide_limits") or {})
_LIMITS = (f"  headline <= {_LIM['headline']} chars   "
           f"sub <= {_LIM['sub']} chars   body <= {_LIM['body']} chars" + chr(10) +
           f"  code <= 9 lines, each <= 46 chars       "
           f"stat <= {_LIM['stat']} chars" + chr(10) +
           f"  items: 3-4, each <= {_LIM['items']} chars            "
           f"eyebrow <= {_LIM['eyebrow']} chars")
if _LIM.get("slides_max"):
    _LIMITS += chr(10) + f"  AT MOST {_LIM['slides_max']} slides per post."

SCHEMA = (SCHEMA.replace("__CTA_SUB__", ACCT["cta_line"].rstrip("."))
                .replace("__LIMITS__", _LIMITS)
                .replace("__SERIES__", SERIES)
                .replace("__REQUIRED_SLIDE__", _REQ_LINE))


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
    # Only posts that hold a slot on the calendar. A retired post keeps its old
    # date as a record, and counting it here meant retiring a week of bad posts
    # pushed every replacement a week out - a silent gap on both accounts.
    dates = [date.fromisoformat(p["date"]) for p in sched["posts"]
             if p.get("status") in ("queued", "published")]
    start = max(dates) if dates else date.today()
    return max(start + timedelta(days=1), date.today() + timedelta(days=1))


def existing_topics(sched):
    return [p["id"] for p in sched["posts"]]


# Constrained decoding guarantees well-formed JSON. Parsing free text failed on
# literal newlines inside the `code` field, which are invalid inside a JSON string.
RICH_TEXT = hooks.RICH_TEXT

# ONE post, not a batch. The Anthropic version asked for all seven in a single
# 128K response; a local model cannot hold that shape and loses the whole batch
# when it slips. Authoring post by post costs more calls (they are free) and
# degrades gracefully — a post that comes out malformed costs that post, not
# the run. main() still receives the same {"posts": [...], "strategy": ...}.
POST_SCHEMA = {
                    "type": "object",
                    "properties": {
                        # FIRST, on purpose: the sampler fills fields in order,
                        # so the model has said the idea plainly before it
                        # writes a single slide. Without it the slides came out
                        # as fragments and the clarity rewrites plateaued at
                        # the same 4-5 doubts (2026-09-23): a model cannot
                        # clarify a thought it never finished. Dropped before
                        # the spec is saved.
                        "explain_it_to_a_friend": {
                            "type": "string",
                            "description": (
                                "3-5 plain sentences, in the slides' language, "
                                "as you would say it to a friend who has never "
                                "heard of this: what it is, who it is for, what "
                                "changes for them, and how to start - only what "
                                "the source says. The slides are cut from this."
                            ),
                        },
                        "slug": {"type": "string"},
                        "hook_candidates": {
                            "type": "array",
                            # Same reasoning as slides: the hook test needs a
                            # pool to grade, and "exactly 5" in a description
                            # is a suggestion the sampler can ignore.
                            "minItems": 5,
                            "maxItems": 5,
                            "description": (
                                "Exactly 5 alternative covers for this post, "
                                "each built on a DIFFERENT hook shape from the "
                                "cover slide and from each other. Graded blind "
                                "against your cover; the winner replaces it."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "headline": RICH_TEXT,
                                    "sub": {"type": "string"},
                                },
                                "required": ["headline", "sub"],
                            },
                        },
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
                            # Enforced by the sampler, not by asking. Told in
                            # plain words to write five slides, and shown the
                            # arc, the model still closed the array after two
                            # every single time — constrained decoding lets it,
                            # so it does. minItems makes the short answer
                            # unreachable rather than merely discouraged.
                            "minItems": _LIM.get("slides_min", 4),
                            "maxItems": _LIM.get("slides_max", 7),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {
                                        "type": "string",
                                        "enum": ["cover", "step", "prompt",
                                                 "stat", "recap"],
                                    },
                                    "eyebrow": {
                                        "type": "string",
                                        "description": (
                                            "Slide label. "
                                            + (f"Slide 2 of every post MUST have "
                                               f"eyebrow '{ACCT['required_eyebrow']}'. "
                                               if ACCT.get("required_eyebrow") else "")
                                        ),
                                    },
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
                    "required": ["explain_it_to_a_friend", "slug", "art",
                                 "caption", "slides", "hook_candidates"],
}

# What the research pass hands the writing pass: a verified subject, and the
# URL it was verified against. source_url is the load-bearing field — an
# account with require_source_url cannot publish a post whose brief has no
# real URL behind it, and validate() checks that URL against what search
# actually returned.
BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "briefs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string",
                              "description": "What the post is about, one line."},
                    "angle": {"type": "string",
                              "description": ("The workflow or takeaway for the "
                                              "reader — not the news itself.")},
                    "why_now": {"type": "string",
                                "description": "What changed, and when."},
                    "quote": {
                        "type": "string",
                        "description": (
                            "One sentence copied word for word from the page "
                            "at source_url, which states the thing this brief "
                            "is about. It is looked up on that page; if it is "
                            "not found the brief is thrown away. Do not "
                            "paraphrase and do not quote a different page."
                        ),
                    },
                    "published": {
                        "type": "string",
                        "description": (
                            "The date this page carries, as YYYY-MM-DD. A page "
                            "with no date on it is not news — an index, a "
                            "form list or a how-to-use-this-site guide. Do not "
                            "submit one."
                        ),
                    },
                    "source_title": {"type": "string"},
                    "source_url": {
                        "type": "string",
                        "description": ("The exact page you read, copied from a "
                                        "search result or read_page. Never typed "
                                        "from memory, never a home page."),
                    },
                    "audience": {
                        "type": "string",
                        "enum": ["broad", "narrow", "niche"],
                        "description": (
                            "Who this touches among THIS account's readers. "
                            "broad = a large share of them; narrow = a specific "
                            "but sizeable group; niche = a small specialist "
                            "group (diplomats, investors, physicians, one rare "
                            "visa category, developer-only tooling, enterprise "
                            "software). Niche topics are dropped."),
                    },
                    "facts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("Every concrete claim the post may use — "
                                        "dates, numbers, names — each one taken "
                                        "from the source above."),
                    },
                },
                "required": ["topic", "angle", "why_now", "audience", "source_url",
                             "quote", "published", "facts"],
            },
        }
    },
    "required": ["briefs"],
}

# The strategy file is EDITED, not rewritten. Anthropic's model returned the
# whole of strategy.md rebuilt, which worked because it could reliably
# reproduce 15KB of markdown with a few lines changed. A 30B local model
# answers that request with a summary of the request — the first local run
# offered 327 characters to replace 15,027 — so instead it is asked only for
# what is new, and update_strategy() splices that into the real file. Small
# ask, mechanical edit, no way to lose the accumulated findings.
STRATEGY_SCHEMA = {
    "type": "object",
    "properties": {
        # Two narrow fields instead of one open one. Asked for "3-6 sentences
        # of markdown" the model wrote about the task it had been given —
        # "This is a detailed request that asks me to act as a content
        # strategist" — on every run of both accounts, so the log entry was
        # discarded every time and the loop's memory stopped moving. A
        # question small enough to have one right answer gets answered.
        "bet": {
            "type": "string",
            "description": ("One sentence, starting with a verb: what this "
                            "batch tried. Example: \"Led with deadlines "
                            "rather than explanations on three of four "
                            "posts.\""),
        },
        "evidence": {
            "type": "string",
            "description": ("One or two sentences on what the performance "
                            "brief above actually shows about earlier posts. "
                            "If it is still too thin to show anything, write "
                            "exactly that and nothing more."),
        },
        "promotions": {
            "type": "array",
            "description": (
                "Hypotheses the performance brief now settles, and NOTHING "
                "else. Return an empty list unless the numbers in the brief "
                "actually decide one — an unproven hypothesis stays unproven, "
                "and inventing a result poisons every future batch."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "finding": {"type": "string",
                                "description": "One line, stated as a rule."},
                    "section": {"type": "string",
                                "enum": ["Confirmed", "Disproven"]},
                    "evidence": {"type": "string",
                                 "description": ("The numbers from the brief "
                                                 "that settle it.")},
                },
                "required": ["finding", "section", "evidence"],
            },
        },
    },
    "required": ["bet", "evidence"],
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
            "and stop repeating what the worst ones did, and say which "
            "hypotheses the numbers now move into Confirmed or Disproven."
        )
    else:
        perf = (f"PERFORMANCE: not yet meaningful — {stats.get('reason')}.\n"
                f"({stats.get('posts', 0)} posts, "
                f"{stats.get('total_engagement', 0)} total engagement.)")
        note = (
            "There is not enough data to optimise against. Do NOT invent "
            "conclusions or claim a hypothesis is confirmed. Write the best "
            "posts you can on the working hypotheses, and confirm or disprove "
            "nothing — note what is still unproven instead."
        )

    return f"{perf}\n\nCURRENT STRATEGY FILE:\n{strategy}\n\n{note}"


RESEARCHER = f"""You are researching Instagram post topics for {ACCT.handle}, \
an account publishing {ACCT['theme']}.

TODAY IS {date.today().isoformat()}. Your training data ends well before this,
so anything you remember about "current" policy is old. Put THIS year in your
queries, and treat a result dated more than a few months ago as background
rather than news.

You have two tools and you must use them. web_search finds candidate stories;
read_page opens one and shows you what it actually says. A search snippet is
not a source — open the page before you believe it.

Rules that are not negotiable:
  - Never state a fact you did not read on a page in this session.
  - read_page EVERY page you intend to cite, before you cite it. You will be
    asked to quote each source word for word, and the quote is looked up on
    that page — a search snippet will not get you there.
  - Copy every URL from a tool result, character for character. Do not type a
    URL from memory, do not shorten one, do not repair one that looks wrong.
    A URL you did not receive from a tool is an invented URL.
  - Prefer the primary document over coverage of it: the agency notice, the
    rule, the release. Cite the specific page, never a site's home page.
  - If you cannot verify a topic, drop it and research a different one.

Work quickly and concretely. Search, read, move on."""

# Naming the allow-list beats filtering silently afterwards. Handed an ideas
# digest full of Google News and newspaper links, the model cited those as
# sources and lost a whole research sweep to the filter — it had no way to
# know which domains count. Told which ones do, it goes looking for them.
if ACCT.get("official_domains"):
    RESEARCHER += (
        "\n\nONLY these domains count as a source for this account:\n  "
        + ", ".join(ACCT["official_domains"]) +
        "\nA newspaper, a law firm or a news aggregator is a LEAD, never a "
        "source. When one tells you something happened, search the agency's "
        "own site for the notice and cite that page instead. A Google News "
        "link is not a source even though it is a real URL, and neither is "
        "an agency's front page — cite the specific notice, rule or alert."
    )


def _sweep(want, avoid, signals, office=("", [])):
    """One self-contained research conversation: search, read, hand back briefs.

    Deliberately short-lived. Every page read costs a couple of thousand
    tokens, so a single loop hunting seven topics runs the window out and
    Ollama silently drops the oldest turns — which are the instructions.
    Starting fresh per sweep keeps each conversation well inside the window
    however large the batch is; the avoid list is what carries memory across.
    """
    ask = (
        f"Find {want} DIFFERENT topics worth a post right now: "
        f"{ACCT['search_brief']}.\n\n"
        + (f"WHAT THIS ACCOUNT'S READERS CARE ABOUT:\n{ACCT['topic_priorities']}\n\n"
           if ACCT.get("topic_priorities") else "")
        + (office[0] + "\n\n" if office[0] else "")
        + (signals + "\n\n" if signals else "")
        + ("Those leads are suggestions about what is being discussed. They "
           "are not facts and some will be wrong — verify anything you use.\n\n"
           if signals else "")
        + "Already covered or already chosen, so do not pick these again:\n"
        + "\n".join(f"- {t}" for t in avoid)
        + "\n\nSearch now. For each topic: find it, open the best source, and "
          "note what changed, when, and the exact URL you read."
    )

    mark = set(search.RETRIEVED)
    convo, calls = llm.tool_loop(
        RESEARCHER, ask, search.TOOLS, search.dispatch,
        model=MODEL, label="research", max_rounds=10,
    )

    # Hand the model the URLs its own tools returned and make it choose from
    # them. Asking it to remember one across a dozen tool calls does not work:
    # it writes a URL that looks exactly right and 404s — uscis.gov/news for a
    # page that was really uscis.gov/newsroom/all-news. Every brief in the
    # first local run on this account was dropped for that. Selection from a
    # list is a task it can do; recall is not.
    found = [u for u in search.since(mark) if _official(u) and _specific(u)]
    # The official listing's URLs were recorded before this sweep began, so
    # since(mark) cannot see them - and they are the best URLs it has.
    found += [u for u in office[1]
              if u not in found and _official(u) and _specific(u)]
    if not found:
        print("  ::warning::this sweep retrieved no usable URLs")
        return [], calls
    menu = "\n".join(f"  {u}   {search.TITLES.get(u, '')[:80]}" for u in found)

    try:
        data = llm.structured(
            RESEARCHER, None, BRIEF_SCHEMA,
            model=MODEL, require=("briefs",), label="briefs",
            messages=convo + [{
                "role": "user",
                "content": (
                    f"Now write up what you found as structured briefs, up to "
                    f"{want} of them.\n\nThese are the pages your tools "
                    f"actually returned:\n\n{menu}\n\nEvery source_url must be "
                    f"copied EXACTLY from that list — character for character, "
                    f"nothing added, nothing trimmed. A brief citing any other "
                    f"URL is discarded. Skip any topic you cannot point at a "
                    f"page in the list for."),
            }],
        )
    except llm.LLMError as e:
        print(f"::warning::a research sweep produced no usable briefs ({e})")
        return [], calls
    return data.get("briefs") or [], calls


def research(count, avoid):
    """Find and verify enough topics before a word of any post is written.

    Anthropic's web_search ran inside the model, so grounding was something we
    asked for and hoped for. Here the loop is ours: search.dispatch executes
    every query, search.RETRIEVED records every URL that came back, and
    validate() later refuses any caption citing a URL that is not in that set.
    The instruction not to invent a source is now a check, not a request.

    Two spare topics, because briefs get dropped: a made-up URL fails the
    check below, and a post can still fail to author afterwards.
    """
    import ideas, newsroom
    signals = ideas.digest(ACCT)
    # What the official sources themselves published, read in code: real,
    # dated, exact URLs to choose from. Shown on EVERY sweep - unlike the
    # leads, it is the part of the prompt most likely to become a true post.
    office = newsroom.digest(ACCT)
    if office[1]:
        print(f"  newsroom: {len(office[1])} official item(s) offered to research")

    # Three spares: posts are now dropped for being untrue or still broken
    # after repair, and each dropped post is a spare consumed.
    want = count + 3
    briefs, taken, calls = [], list(avoid), 0
    for sweep in range(4):
        if len(briefs) >= want:
            break
        # The ideas digest goes in once. It is large, it is the same every
        # sweep, and a later sweep needs the avoid list far more than leads.
        found, n = _sweep(want - len(briefs), taken, signals if not sweep else "",
                          office)
        calls += n

        for b in found:
            url = b.get("source_url", "")
            # Asked for more topics than it found, the model fills the array
            # rather than returning fewer — one sweep handed back a brief
            # whose topic was literally "No hay más temas". A brief with
            # nothing in it produces a post with nothing in it.
            if len(b.get("facts") or []) < 2 or len(b.get("topic", "")) < 15:
                print(f"  dropped an empty brief: {b.get('topic', '')!r}")
                continue
            # Relevance before accuracy: a true post nobody it is for cares
            # about still reaches nobody. The month the local model took over,
            # inmigraforma covered a Federal Register index, civil-surgeon
            # designations, diplomats' children and $800,000 investor visas.
            if b.get("audience") == "niche":
                print(f"  dropped {b.get('topic', '')[:44]!r}: niche audience")
                continue
            past = _past_deadline(f"{b.get('topic', '')} {b.get('why_now', '')}")
            if past:
                print(f"  dropped {b.get('topic', '')[:44]!r}: built on a "
                      f"deadline that has passed ({past})")
                continue
            stale = _not_news(b.get("published"))
            if stale:
                print(f"  dropped {b.get('topic', '')[:44]!r}: {stale}")
                continue
            # The URL is real and official. Is it about this topic? Only a
            # quote found on the page answers that, and without it the model
            # attaches whichever allow-listed URL it happens to hold.
            if ACCT.get("require_source_url"):
                why = search.supports(url, b.get("quote", ""))
                if why:
                    print(f"  dropped {b.get('topic', '')[:44]!r}: "
                          f"source does not support it — {why}")
                    continue
            # A brief whose URL the model typed rather than received is the
            # exact failure this pass exists to prevent, and dropping it now
            # is cheaper than having validate() reject the finished post.
            # EVERY account, not only the ones that print their source: on
            # 2026-09-23 seven of leverageai's ten briefs cited URLs no tool
            # returned - blog.google/products/gemini/contract-review,
            # xero.com/blog/ai-expense-automation - all 404, all products the
            # model imagined. Their posts could not be fact-checked, because
            # there was no page to check them against.
            if not _url_seen(url):
                print(f"  dropped a brief citing a URL no search "
                      f"returned: {url}")
                continue
            if not search.PAGES.get(url):
                search.fetch(url, max_chars=200_000)
            if not search.PAGES.get(url):
                print(f"  dropped a brief whose source cannot be read, so "
                      f"nothing written from it could be checked: {url}")
                continue
            # A topic that promises a result its page never states - "3x
            # faster", "50% cheaper", "cut drafting time by 50%" - is a post
            # the fact-check will reject after the writing is paid for. On
            # 2026-09-23 four of six leverageai topics arrived like that.
            invented = _invented_metric(b.get("topic", ""), search.PAGES[url])
            if invented:
                print(f"  dropped {b.get('topic', '')[:44]!r}: promises "
                      f"{invented!r}, which its page never says")
                continue
            if ACCT.get("require_source_url"):
                # A link shortener or a news write-up is a real URL and still
                # fails validate(), so it is worth catching here: a brief
                # rejected now costs one sweep, the same brief rejected after
                # authoring costs a post out of the batch.
                if not _official(url):
                    print(f"  dropped a brief whose source is not an official "
                          f"domain: {url}")
                    continue
                if not _specific(url):
                    print(f"  dropped a brief citing a home page rather than "
                          f"a notice: {url}")
                    continue
                if _paperwork(url):
                    print(f"  dropped {b.get('topic', '')[:44]!r}: a paperwork "
                          f"notice about a form's approval, not news: {url}")
                    continue
                if _evergreen(url):
                    print(f"  dropped a brief citing a page that explains "
                          f"rather than reports: {url}")
                    continue
            if any(b.get("topic", "").lower() == x.lower() for x in taken):
                continue
            # One post per source page. The model reuses whichever page it has
            # to hand: four queued posts cited the same Federal Register index,
            # and a public-charge brief cited uscis.gov/avoid-scams. A page
            # that genuinely covers two topics can carry the second next batch.
            if any(url == x.get("source_url") for x in briefs):
                print(f"  dropped {b.get('topic', '')[:44]!r}: its source is "
                      f"already used by another topic in this batch")
                continue
            briefs.append(b)
            taken.append(b.get("topic", ""))
        # One unlucky sweep - search came back with nothing official, or the
        # model searched badly - used to end research for the whole night,
        # and an unattended run then queued nothing. The loop is already
        # capped at four sweeps, so a genuinely broken search still stops.
        if not found:
            continue

    if not calls:
        print("::warning::Research ran ZERO searches — the topics below come "
              "from training data, not current sources. Review before publishing.")
    else:
        print(f"grounded on {calls} search/read call(s), "
              f"{len(search.RETRIEVED)} distinct URLs")

    for b in briefs:
        print(f"  brief: {b.get('topic', '?')[:70]}  <- {b.get('source_url', '')}")
    return briefs


def _not_news(published, back=180, ahead=365):
    """Why this brief is not news, or None if it is.

    A publication date is the cheapest test for "is this an article or is it
    furniture". Every structural guard passed on briefs titled "Federal
    Register Index" and "Using FederalRegister.Gov" — official domain, real
    retrieved URL, specific path — and two of them became posts, because
    nothing asked whether the page was ever published. Index pages, form
    lists and site guides carry no date; notices, rules and alerts do.

    The forward window is wide on purpose: a rule announced today with an
    effective date next year is exactly what this account reports.
    """
    if not published:
        return "the page carries no date, so it is not news"
    # Some sources are dated to the month, not the day — the Visa Bulletin is
    # "September 2026" and nothing finer, and rejecting it as unreadable threw
    # away one of this account's most reliable recurring stories.
    raw = (published or "").strip()
    for fmt in (raw[:10], raw[:7] + "-01" if len(raw) >= 7 else "",
                raw[:4] + "-01-01" if len(raw) >= 4 else ""):
        try:
            when = date.fromisoformat(fmt)
            break
        except ValueError:
            continue
    else:
        return f"unreadable date {published!r}"
    age = (date.today() - when).days
    if age > back:
        return f"dated {published}, {age} days old"
    if age < -ahead:
        return f"dated {published}, too far ahead to be real"
    return None


def _host(url):
    h = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    return re.sub(r"^www\.", "", h)


def _official(url):
    """Is this URL on one of the account's allow-listed source domains?

    An account with no allow list accepts anything, which is how every
    account except inmigraforma behaves.
    """
    allowed = ACCT.get("official_domains") or []
    if not allowed:
        return True
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in allowed)


def _specific(url):
    """A page, not a site. `https://www.state.gov/` cites nothing.

    Same test validate() applies to a finished caption, applied to the brief
    instead — a home page caught here costs one research sweep, and caught
    after authoring costs a post out of the batch.
    """
    return len((url or "").rstrip("/").split("/")) > 3


def _url_seen(url):
    """Did a search or a page read actually hand us this URL?

    Compared without the trailing slash and without the scheme, because the
    model rewrites http to https and drops or adds a final slash constantly
    and neither changes which document a reader would open.
    """
    def norm(u):
        return re.sub(r"^https?://(www\.)?", "", (u or "").strip()).rstrip("/").lower()
    target = norm(url)
    return bool(target) and any(norm(seen) == target for seen in search.RETRIEVED)


# Pages that describe how something works rather than report that something
# changed: a form's own page (/i-730, /forms/i-129), and hub pages for a whole
# program. Given one, the model manufactures the news - on 2026-09-23 the I-730
# page became "ahora hay nuevos pasos para traer a tu esposa e hijos ... no
# años", and the TPS hub page became an Ethiopia deadline that does not exist.
# Both were caught by the fact-check, but a topic dropped here costs one brief
# instead of three minutes of writing and fixing.
_EVERGREEN = re.compile(
    r"^/(forms/)?[a-z]{1,2}-\d{1,4}[a-z]?/?$"          # /i-730, /forms/i-129
    r"|^/policy-manual/?$"
    r"|^/humanitarian/temporary-protected-status/?$"
    r"|^/humanitarian/refugees-and-asylum/asylum/?$"
    r"|^/green-card/?$|^/citizenship/?$|^/working-in-the-united-states/?$", re.I)


_MONTH_EN = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
     "nov", "dec"), 1)}
# ACT-BY words only. "Until", "ends", "expires", "termina" describe a status,
# and a status change is often the news itself: "TPS Extension Until New
# Announcement" was dropped on 2026-09-23 for naming the old 9 September date,
# when the news was that protection CONTINUES past it. Only a deadline to ACT
# that has already gone makes a topic stale.
_DEADLINE_WORDS = re.compile(
    r"deadline|re-?regist|register by|file by|apply by|submit by|"
    r"fecha l[ií]mite|plazo para|reg[ií]strate|antes del|manda\w* antes", re.I)


_CONTINUES = re.compile(
    r"not terminated|remains? in effect|retain|continu|still valid|extended|"
    r"extensi[oó]n|pr[oó]rroga|sigue[n]? vigente|mantiene[n]?|no termina", re.I)


def _past_deadline(text):
    """The date, if a topic is ABOUT a deadline and every date it names has
    passed. "TPS expiration date: September 9, 2026" reached the writer on the
    23rd and came back as "regístrate antes del 9 septiembre". Recent events
    are still news - a court order dated the 12th is kept, because a topic is
    only dropped when it is about a deadline."""
    if not _DEADLINE_WORDS.search(text or ""):
        return None
    # Protection that CONTINUES past its old date is news, not a stale
    # deadline. "El Salvador TPS Not Terminated After Sept 9" was dropped on
    # 2026-09-23 because its why_now named the old re-registration window -
    # while the USCIS page said Salvadorans keep TPS and work permits until a
    # new announcement. That is the story these readers most need.
    if _CONTINUES.search(text):
        return None
    today, found = date.today(), []
    for m in re.finditer(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?", text):
        mon = _MONTH_EN.get(m.group(1).lower())
        if mon:
            found.append((int(m.group(3) or today.year), mon, int(m.group(2))))
    for m in re.finditer(r"\b(\d{1,2})\s+de\s+([a-záéíóú]+)(?:\s+de\s+(\d{4}))?", text, re.I):
        mon = _MONTHS.get(m.group(2)[:3].lower())
        if mon:
            found.append((int(m.group(3) or today.year), mon, int(m.group(1))))
    dates = []
    for y, mo, d in found:
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass
    if dates and max(dates) < today:
        return max(dates).isoformat()
    return None


# Paperwork Reduction Act notices: OMB extending or revising the approval of a
# FORM. They read like news and are not. On 2026-09-23 one titled "Extension
# ... Currently Approved Collection" for Form I-821 became a post telling TPS
# holders DHS had proposed extending TPS for several countries - it extends no
# country's TPS. The fact-check rejected it; this stops the topic earlier.
_PAPERWORK = ("omb control number", "currently approved collection",
              "information collection", "paperwork reduction act")


def _paperwork(url):
    page = search.PAGES.get(url)
    if page is None:
        search.fetch(url, max_chars=200_000)
        page = search.PAGES.get(url) or ""
    low = page[:6000].lower()
    return sum(m in low for m in _PAPERWORK) >= 2


_METRIC = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|x\b|×|times\b|hours?\b|minutes?\b|"
                     r"mins?\b|days?\b)", re.I)


def _invented_metric(topic, page):
    """The first number-with-a-unit in a topic that its page never states."""
    low = re.sub(r"\s+", " ", (page or "").lower())
    for m in _METRIC.finditer(topic or ""):
        num = re.match(r"\d+(?:\.\d+)?", m.group(0)).group(0)
        # The number itself must appear on the page near a matching unit;
        # "50" anywhere on a long page proves nothing, so look for the pair.
        unit = m.group(0)[len(num):].strip().lower()[:1]
        if not re.search(rf"\b{re.escape(num)}\s?(?:{re.escape(unit)}|%|x|×|times|hour|minute|day|percent|per cent)",
                         low):
            return m.group(0)
    return None


def _evergreen(url):
    from urllib.parse import urlparse
    return bool(_EVERGREEN.match(urlparse(url).path or "/"))


# qwen3 is a reasoning model, and until 2026-09-23 the writer ran with its
# reasoning switched off while the fact-checker ran with it on. The checker
# was precise all day; the writer copied English source text onto Spanish
# slides, ignored the date it had been given, and reached for a public-charge
# rule from its training data. Thinking roughly doubles the minutes per post,
# and the nightly run has hours. Per-account override: "writer_think": false.
WRITER_THINK = bool(ACCT.get("writer_think", True))


def _slide_plan():
    """The slide count and the mandated slides, stated as an instruction.

    All of this is already in the brand system prompt, via the account's
    post_arc. A hosted model read it there and complied; the local one writes
    two slides and calls it a post, because a system prompt is background and
    the immediate ask is foreground. So the binding parts are repeated in the
    ask, where they get followed.
    """
    lo, hi = _LIM.get("slides_min", 4), _LIM.get("slides_max", 7)
    count = (f"EXACTLY {lo} slides" if lo == hi
             else f"between {lo} and {hi} slides")
    plan = (f"Write {count}, following the slide arc above in order. Fewer is "
            f"not safer: a short post is rejected outright. The character "
            f"budget is met by writing each slide tighter, never by dropping "
            f"one.")
    if ACCT.get("required_eyebrow"):
        plan += (f"\nSlide 2's eyebrow must be exactly "
                 f"\"{ACCT['required_eyebrow']}\".")

    # The arithmetic the model cannot be left to do. The per-slide limits and
    # the reading-time budget are ceilings from two different directions and
    # they do not agree: inmigraforma's five slides at their per-field maxima
    # come to 763 characters against a 470 budget. A model writing "up to 110
    # characters of body" on every slide therefore overruns the Reel every
    # time, which is exactly what happened — 640 characters, three repair
    # rounds, no improvement, because nothing ever told it the total.
    target = ACCT.get("reel_target_seconds")
    if target:
        budget = int(target * 11 * 0.95)
        plan += (
            f"\nBUDGET: {budget} characters of slide text IN TOTAL across all "
            f"{hi} slides — about {budget // hi} per slide, counting its "
            f"eyebrow and headline, not just its body. The per-field limits "
            f"above are ceilings, not targets; writing to them on every slide "
            f"overruns the Reel and the post is rejected. Slides carry the "
            f"news in the fewest words that are still accurate. Everything "
            f"else — the detail, the quote, the source URL — goes in the "
            f"caption, which has no such budget."
        )
    return plan


_TEXT_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}},
                "required": ["text"]}

# Function words, not vocabulary: a Spanish sentence is full of these and an
# English one has none of them, whatever it is about.
_ES_MARKERS = {"de", "la", "el", "que", "para", "con", "los", "las", "una",
               "del", "por", "tu", "tus", "si", "no", "es", "en", "se", "su",
               "desde", "hasta", "pero", "ya", "mas", "más"}


def _space_sentences(text):
    """Put back the space the model drops when it compresses.

    "afectar tu visa.Si trabajas" reached a rendered slide. Purely
    typographic, and deliberately narrow: only between a sentence-ending mark
    and a following capital letter, so decimals, URLs and abbreviations are
    left alone.
    """
    # A lowercase letter before the mark is what makes it a sentence end:
    # without that guard "EE.UU." became "EE. UU." and "U.S." "U. S.".
    return re.sub(r"([a-záéíóúñ0-9)])([.!?])([A-ZÁÉÍÓÚÑ¿¡])", r"\1\2 \3",
                  text or "")


def _strip_meta(text):
    """Drop a running commentary the model appended to its own answer.

    Told to rewrite in at most 93 characters, it returned the rewrite plus
    " 71 chars" — and that tail counted toward the limit it was measured
    against, on a slide a reader would have seen.
    """
    text = (text or "").strip().strip('"“”')
    text = re.sub(r"[\s(\[]*\b\d+\s*(chars?|characters|caracteres)\b[.\)\]]*$",
                  "", text, flags=re.I)
    return _space_sentences(text.strip())


def _same_language(before, after):
    """Did a rewrite quietly translate itself?

    Deliberately crude and one-directional: it only fires when the original
    was clearly Spanish and the rewrite has none of the same function words.
    That is the failure actually seen, and a looser test would start
    rejecting good Spanish rewrites for using different words.
    """
    def marks(s):
        return sum(1 for w in re.findall(r"[a-záéíóúñü]+", s.lower())
                   if w in _ES_MARKERS)
    return marks(before) < 3 or marks(after) > 0


def _fit_sentences(text, cap):
    """Make text fit by REMOVING, never rewriting. The last resort.

    The model cannot land under a character limit on dense legal Spanish
    without dropping something, and when every rewrite it tries still misses,
    _shorten gives up and the post is lost - a USCIS scam warning went on
    2026-09-23 for one slide 25 characters over. Removing text cannot turn a
    true sentence into a false one the way a rewrite can, and anything this
    returns still goes through the fact-check afterwards.

    Whole sentences first, from the end: the first sentence of a slide is the
    news, the last is the elaboration. A single sentence that is itself too
    long is cut at its last clause break that fits. A stub shorter than 40% of
    the limit is not worth shipping, so that returns None instead.
    """
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    sents = re.split(r"(?<=[.!?])\s+", text)
    kept = []
    for sent in sents:
        trial = " ".join(kept + [sent])
        if len(trial) > cap:
            break
        kept.append(sent)
    if kept:
        return " ".join(kept)
    first = sents[0]
    cuts = [m.start() for m in re.finditer(r"[,;:]\s|\s[-\u2014]\s", first)
            if m.start() <= cap - 1]
    if not cuts or cuts[-1] < cap * 0.4:
        return None
    return first[:cuts[-1]].rstrip(" ,;:-\u2014") + "."


def _one_pass(text, cap, plans):
    """One sweep of the plans. Returns the shortest usable rewrite, or None."""
    best = None
    for attempt, (ratio, how) in enumerate(plans):
        target = max(30, int(cap * ratio))
        try:
            out = llm.structured(
                "You tighten microcopy. You never add anything that was not "
                "there. You answer with the rewritten text only, IN THE SAME "
                "LANGUAGE it was given to you — Spanish in, Spanish out. "
                "Translating is not shortening.",
                f"Rewrite this in AT MOST {target} characters — shorter is "
                f"fine. It is currently {len(text)}. Same language.\n\n{how}"
                f"\n\n{text}",
                _TEXT_SCHEMA, model=WRITER, require=("text",),
                label="tighten", temperature=0.2 + 0.15 * attempt, attempts=1,
                # One sentence in, one sentence out. Without a cap the model
                # once spent eighty seconds emitting 13,000 tokens here.
                max_tokens=200,
            )
        except llm.LLMError:
            continue
        new = _strip_meta(out.get("text") or "")
        if not new:
            continue
        # Asked to shorten a Spanish sentence, the model's first answer was a
        # shorter ENGLISH one. It fit, it kept every fact, and it would have
        # shipped an English slide to a Spanish audience. Length is not the
        # only thing worth checking about a rewrite.
        if not _same_language(text, new):
            print("  tighten came back in the wrong language — discarded")
            continue
        if best is None or len(new) < len(best):
            best = new
        if len(new) <= cap:
            return new
    # The shortest thing it managed, even though it is still too long. The
    # caller feeds it back in; a post is only rejected once squeezing stops
    # helping, because truncating an immigration sentence mid-clause is worse
    # than skipping the post.
    return best


def _to_spanish(text):
    """One slide string in plain Spanish, or None.

    The whole-post repair was told "this slide reads as English" and handed
    back the same slide, round after round - "USCIS: 'No needs rep for your
    application.'" survived five of them. Handed one sentence and one job,
    the model translates reliably, the same way _shorten lands a length the
    whole-post pass could not.
    """
    # Asked in English for a field called "text", the model echoed the input
    # back unchanged. Asked in Spanish, for a field named in Spanish, it
    # translated all three calibration cases cleanly: the schema key steers a
    # local model harder than the instruction does (see CLAUDE.md conventions).
    try:
        out = llm.structured(
            "Eres un traductor del inglés al español. Respondes solo en español.",
            "Traduce este texto de una diapositiva al español sencillo, para "
            "inmigrantes sin formación legal. Deja en inglés solo los nombres "
            "oficiales (USCIS, Green Card, TPS, EAD, I-485, Visa Bulletin, "
            "Final Action Dates, Dates for Filing, parole). Mantén cada dato, "
            "fecha y número. No agregues nada.\n\nTEXTO EN INGLÉS:\n" + text,
            {"type": "object",
             "properties": {"texto_en_espanol": {
                 "type": "string",
                 "description": "The same message, written in Spanish."}},
             "required": ["texto_en_espanol"]},
            model=MODEL, require=("texto_en_espanol",), label="to_spanish",
            temperature=0.2, max_tokens=400)
    except llm.LLMError:
        return None
    new = _space_sentences((out.get("texto_en_espanol") or "").strip())
    return new if new and not _reads_english(new) else None


def _spanishify(post):
    """Translate every slide field that reads as English. True if any changed."""
    if ACCT.get("slide_language") != "es":
        return False
    changed = False
    for sl in post.get("slides") or []:
        for key in ("headline", "sub", "body"):
            cur = hooks.flatten(sl.get(key))
            if cur and _reads_english(cur):
                new = _to_spanish(cur)
                if new:
                    sl[key] = new
                    changed = True
        items = sl.get("items")
        if isinstance(items, list):
            for i, it in enumerate(items):
                if isinstance(it, str) and _reads_english(it):
                    new = _to_spanish(it)
                    if new:
                        items[i] = new
                        changed = True
    return changed


def _tighten(post):
    """Shorten only the fields that overflow, instead of rewriting the post.

    The whole-post repair pass plateaus: asked to lose seven characters it
    regenerates everything and lands in the same place, five rounds running,
    because a model cannot count what it is writing. Handing it one sentence
    and one number is a task it can actually do.

    Rich-text segments become a plain string here. The schema has always
    allowed either, and losing an accent colour is worth a post that ships.
    """
    fields = (("headline", _LIM["headline"]), ("sub", _LIM["sub"]),
              ("body", _LIM["body"]), ("stat", _LIM["stat"]))
    changed = _spanishify(post)
    for s in post.get("slides") or []:
        for key, cap in fields:
            cur = hooks.flatten(s.get(key))
            if not cur or len(cur) <= _slack(cap):
                continue
            # Removal only, no model. The model shortener (_shorten) was the
            # single biggest cost of a run - 64-74 calls, a quarter of the
            # night - and the thing that turned sentences into fragments
            # ("Para abril: antes del 4 sep."). Whole sentences are dropped;
            # a field that still does not fit is rewritten by the writer,
            # with validate()'s message, or the post is dropped.
            fit = _fit_sentences(cur, cap)
            if fit and len(fit) < len(cur):
                s[key] = fit
                changed = True

    # Whole-post reading budget, once every field is individually legal.
    # Trim the longest body each pass: it is the one with the most slack and
    # the one a viewer is most likely to be still reading when the slide cuts.
    target = ACCT.get("reel_target_seconds")
    if target:
        budget = int(target * 11 * 0.95)
        for _ in range(4):
            over = _slide_chars(post) - budget
            if over <= 0:
                break
            bodies = [s for s in post.get("slides") or []
                      if hooks.flatten(s.get("body"))]
            if not bodies:
                break
            longest = max(bodies, key=lambda s: len(hooks.flatten(s["body"])))
            cur = hooks.flatten(longest["body"])
            # Never above the field's own ceiling. Asking only for "enough to
            # fit the budget" let this rewrite a 200-character body to 164,
            # which met the total and still broke the 110-character body
            # limit — so the post was rejected by the very pass that had just
            # improved it.
            want = min(_LIM["body"], len(cur) - over)
            # Remove whole sentences, never rewrite (see above).
            new = _fit_sentences(cur, max(40, want))
            if not new or len(new) >= len(cur):
                break
            longest["body"] = new
            changed = True
    return changed


def _slide_chars(post):
    """Every character a viewer has to read, which is what the budget counts."""
    total = 0
    for s in post.get("slides") or []:
        for key in ("eyebrow", "sub", "stat", "label"):
            total += len(s.get(key) or "")
        total += len(hooks.flatten(s.get("headline")))
        total += len(hooks.flatten(s.get("body")))
        total += sum(len(hooks.flatten(x)) for x in (s.get("items") or []))
    return total


EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {"type": "array", "minItems": 5, "maxItems": 8,
                      "items": {"type": "string"}},
    },
    "required": ["sentences"],
}


def _explainer_system():
    lang = "Spanish" if ACCT.get("slide_language") == "es" else "English"
    who = ACCT.get("clarity_reader") or "someone new to the subject"
    cap = _LIM["body"]
    return (
        f"You explain one piece of news to one person: {who} They know little "
        f"about the subject and stop reading the moment they have to guess. "
        f"Your job is that they finish informed - knowing what happened, what "
        f"it means for them and what to do - not feeling they wasted their "
        f"time.\n\n"
        f"Write 5-8 sentences in {lang}.\n"
        f"- Each sentence is complete and makes sense ALONE, because it will "
        f"be shown alone on a slide. Never 'this', 'it', 'they' or 'esto' "
        f"pointing back to an earlier sentence - name the thing again.\n"
        f"- Everyday words. The first time a term, program, form, product or "
        f"acronym appears, say what it is in the same sentence.\n"
        f"- Never name an internal policy, memo or docket code (PM-602-0193, "
        f"FR 5953, 26-cv-6332): no reader knows them. Say what the rule or "
        f"order DID instead. A form a reader files (I-485) may be named, "
        f"explained.\n"
        f"- 'Temporarily', 'proposed', 'for the plaintiffs' and other limits "
        f"the source states are part of the fact - keep them.\n"
        f"- In this order: (1) what happened, with who did it and when; (2) "
        f"what the key term means; (3) what it does or changes, with one "
        f"everyday example; (4) who it affects - and who it does not - only as "
        f"the source says; (5) what the reader can do now, only as the source "
        f"says, or else where to read more or whom to ask.\n"
        f"- At most {cap} characters per sentence. Two short sentences beat "
        f"one long one.\n"
        f"- Only what the facts and source lines below say. Invent nothing: "
        f"no number, date, step, price, plan, consequence or promise that is "
        f"not there. Name the agency or company the facts name - never a "
        f"different one.\n"
        f"- Dates: only a date the facts give, attached to the same event the "
        f"facts attach it to (a date something starts is not the date it was "
        f"announced). Today's date is NOT the date anything happened; if the "
        f"facts give no date, write no date. The post is read AFTER today: a "
        f"date already past is described as what applies now ('desde el 18 "
        f"de septiembre USCIS rechaza la versión vieja'), never as something "
        f"still ahead ('antes del 18 de septiembre').\n"
        f"- The first sentence names the concrete thing that changed - the "
        f"form, program, fee or rule by name - not 'a new version' or 'a "
        f"change'."
        + ("" if ACCT.get("require_source_url") else
           f"\n- A use YOU suggest is a suggestion, never a fact about the "
           f"product: 'you could ask it to draft a client contract' is yours; "
           f"'it drafts client contracts' is a claim the page must make. The "
           f"page's own examples may be stated as facts.")
        + f"\n- Every product, feature or plan you name is explained in the "
        f"same sentence, in plain words ('Cowork, the part of Claude that "
        f"works on your files'; 'the paid Pro and Max subscriptions').")


def _explain(brief, slug_prefix, rounds=6):
    """Plain-prose explanation, read by a first-time reader until clear.

    The writer was asked for slides directly - headline, fragment, arrows -
    and compressing an idea into slide pieces is exactly where it lost the
    meaning: on 2026-09-23 every draft on both accounts left a first-time
    reader with 4-6 blocking doubts, and rewriting the slides never brought
    that down. Prose is what a small model writes well. So the explaining
    happens first, as sentences, checked by the same reader; the slides are
    then built from those sentences (_snap) instead of composed from scratch.
    Returns the sentences, or [] if no explanation could be made.
    """
    facts = "\n".join(f"  - {f}" for f in brief.get("facts") or [])
    ask = (f"TODAY IS {date.today().isoformat()}.\n"
           f"THE NEWS: {brief.get('topic')}\n"
           f"WHY NOW: {brief.get('why_now')}\n"
           f"SOURCE: {brief.get('source_url')}\n"
           f"VERIFIED FACTS:\n{facts}\n\n" + _how_block(brief)
           + "Write the explanation.")
    msgs = [{"role": "user", "content": ask}]
    best, best_doubts = None, None
    # Sentences that drew no objection in an earlier round. The checkers are
    # not stable: on 2026-09-24 three sentences copied verbatim between rounds
    # were passed in round 1 and marked CONTRADICTED in round 2, and the
    # "best round" selection was measuring that noise. A sentence's truth does
    # not change while its text does not, so a cleared sentence stays cleared.
    cleared = set()

    _about = _mentions

    for rnd in range(rounds):
        data = None
        # Thinking first; on a runaway past the cap (four times on
        # 2026-09-24), the same round again with reasoning off, which cannot
        # run away and which a dense model writes acceptably without.
        for think in ([WRITER_THINK, False] if WRITER_THINK else [False]):
            try:
                data = llm.structured(_explainer_system(), None, EXPLAIN_SCHEMA,
                                      model=WRITER, require=("sentences",),
                                      label=f"explain{rnd + 1}:{slug_prefix}"
                                            + ("" if think else "/nothink"),
                                      temperature=0.3, think=think,
                                      max_tokens=12000, messages=msgs)
                break
            except llm.LLMError as e:
                print(f"  {slug_prefix}: explanation round {rnd + 1} failed ({e})")
                if "cap" not in str(e):
                    break
        if data is None:
            continue
        sents = [re.sub(r"\s+", " ", s).strip() for s in data["sentences"]
                 if s and s.strip()]
        long = [s for s in sents if len(s) > _slack(_LIM["body"])]
        probe = {"slides": [{"kind": "step", "body": s} for s in sents]}
        doubts, _ = clarity.read(probe, brief, ACCT, model=MODEL,
                                 label=f"{slug_prefix}/explain{rnd + 1}")
        # True as well as clear: every slide is built from these sentences,
        # so an error here is an error on every slide. On 2026-09-24 the first
        # explanation dated a court order to the day it was written and named
        # the wrong agency; the post was then rejected, after all its work.
        untrue, _ = factcheck.verify_detail(probe, brief, ACCT, model=MODEL,
                                            label=f"{slug_prefix}/explain{rnd + 1}")
        problems = doubts + untrue + [
            f"too long for one slide ({len(s)} characters, limit "
            f"{_LIM['body']}) - split it: {s!r}" for s in long]
        # Drop objections to sentences already cleared, and whole-post
        # messages that quote no sentence (the "only N claims backed" total)
        # once anything has been cleared - they swing with the same noise.
        problems = [p for p in problems
                    if not any(_about(p, s) for s in cleared)
                    and not (cleared and p.startswith("only "))]
        doubts = [p for p in doubts if p in problems]
        untrue = [p for p in untrue if p in problems]
        cleared |= {s for s in sents if not any(_about(p, s) for p in problems)}
        print(f"  {slug_prefix}: explanation {rnd + 1} - {len(doubts)} "
              f"doubt(s), {len(untrue)} untrue, {len(long)} too long")
        # The counts alone cannot tell a real error from an over-strict
        # checker; the sentences and the objections are what a review needs.
        for s in sents:
            print(f"      | {s}")
        for pr in problems[:8]:
            print(f"      ! {pr[:260]}")
        if best is None or len(problems) < len(best_doubts):
            best, best_doubts = sents, problems
        if not problems:
            break
        msgs = msgs + [
            {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
            {"role": "user", "content":
                "A first-time reader and a fact-checker read these sentences "
                "and objected ONLY to the ones quoted below. Return the same "
                "list with every other sentence copied EXACTLY as it is - do "
                "not rephrase, reorder, merge or improve a sentence nobody "
                "objected to; a full rewrite has been measured to break more "
                "than it fixes. For each quoted sentence: where the checker "
                "quotes the page, say what the page says; where it says "
                "something is not on the page, delete that part or the whole "
                "sentence; where the reader had a doubt, add the missing "
                "words to that sentence:\n"
                + "\n".join(f"  - {p}" for p in problems)}]
    if best and len(best_doubts) > CLARITY_MAX:
        # A body sentence the reader still doubts cannot be repaired later:
        # the slide repair rewrites it and _snap restores the checked one,
        # so the same doubt returns (3 -> 3, 2026-09-24). Ten more minutes
        # of slides cannot save this topic; a spare one can use the time.
        raise llm.LLMError(f"explanation still unclear after {rounds} rounds "
                           f"({len(best_doubts)} problem(s))")
    return [s for s in (best or []) if len(s) <= _slack(_LIM["body"])], cleared


def _mentions(problem, sentence):
    """Is this objection about that sentence? The reader quotes the slide
    verbatim, but the fact-checker quotes its own paraphrase of a claim, so a
    substring test missed most of them (2026-09-24 review). Word overlap:
    at least 60% of the sentence's content words appear in the objection."""
    sw = {w for w in re.findall(r"\w+", clarity._norm(sentence)) if len(w) > 3}
    if not sw:
        return False
    pw = set(re.findall(r"\w+", clarity._norm(problem)))
    return len(sw & pw) / len(sw) >= 0.6


def _uncleared(doubts, cleared):
    """Doubts about sentences the explanation stage already cleared are the
    reader's noise, not new problems - the final read judges only what the
    writer added on top: cover, headlines, recap."""
    return [d for d in doubts if not any(_mentions(d, s) for s in cleared)]


def _snap(post, sentences):
    """Put the checked sentences back where the writer paraphrased them.

    The writer is told to copy them word for word and mostly does; when it
    trims one into a fragment, the body is restored to the whole sentence it
    came from. Only when the match is unambiguous - half the words shared -
    and never on the cover, whose hook is the writer's own.
    """
    def words(t):
        return set(re.findall(r"\w+", (t or "").lower()))
    pool = [(s, words(s)) for s in sentences]
    if not pool:
        return
    for sl in post.get("slides") or []:
        if sl.get("kind") != "step":
            continue
        body = hooks.flatten(sl.get("body"))
        if not body or body in sentences:
            continue
        w = words(body)
        if not w:
            continue
        best, score = max(((s, len(w & sw) / len(w | sw)) for s, sw in pool),
                          key=lambda x: x[1])
        if score >= 0.5 and best != body:
            sl["body"] = best


# Sentences on a source page that say HOW to use something or WHO gets it.
_HOWTO = re.compile(
    r"\b(to get started|get started|go to|click|tap|open the|select|choose|"
    r"type @|type the|turn on|enable|settings|sign in|log in|download|install|"
    r"available (?:in|on|for|to|now|starting|today)|rolling out|roll out|"
    r"you can now|you can (?:use|try|find|access|connect)|plans?\b|subscribers?|"
    r"workspace (?:business|enterprise)|pro and ultra|free for|at no cost|"
    r"cómo|para empezar|visite|ingrese|presente|envíe|llame)\b", re.I)


def _page_steps(url, limit=8):
    """The page's own instructions and availability lines, verbatim.

    The clarity reader asks "where do I click, do I have this?", and a writer
    pushed to answer it invents the answer: "Type @QuickBooks", "Requires
    QuickBooks Online plan" (2026-09-23, both rejected by the fact-check, both
    nowhere on the page). So the answer is lifted from the page in code, and
    the writer may use only that.
    """
    page = search.PAGES.get(url) or ""
    # A link's text arrives on its own line ("connect your favorite apps in\n
    # Gemini settings\n, or ..."); rejoin single line breaks so the sentence
    # keeps the part that says where.
    page = re.sub(r"[ \t]*\n(?![ \t]*\n)[ \t]*", " ", page)
    out, seen = [], set()
    for sent in re.split(r"(?<=[.!?:])\s+|\n+", page):
        sent = re.sub(r"\s+", " ", sent).strip()
        if not 30 <= len(sent) <= 300 or not _HOWTO.search(sent):
            continue
        key = sent.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(sent)
        if len(out) >= limit:
            break
    return out


def _how_block(brief):
    steps = _page_steps(brief.get("source_url", ""))
    if steps:
        return ("WHAT THE SOURCE PAGE ITSELF SAYS ABOUT HOW TO USE IT AND WHO "
                "GETS IT, copied from the page. Any step, menu, button, plan, "
                "price or availability in the post must come from these lines "
                "and nowhere else:\n" + "\n".join(f"  - {x}" for x in steps)
                + "\n\n")
    return ("THE SOURCE PAGE GIVES NO STEPS AND DOES NOT SAY WHO GETS IT. So "
            "do not write a how-to and do not name a menu, button, plan or "
            "price. Write it as news a stranger understands: what it is, what "
            "it does in one plain everyday example, and - where a step would "
            "go - that the announcement has not said how or who yet.\n\n")


def write_post(brief, slug_prefix, series_no):
    """Author ONE post from a verified brief, then fix what validate() catches.

    The repair pass is new, and it is what makes a local model workable here.
    validate() already encoded every rule worth enforcing — overflowing
    slides, a caption that promises a reply, a missing source line — but it
    only ever ran after authoring, so a broken post was simply discarded. A
    weaker model breaks those rules far more often and the errors are
    mechanical, so it gets told exactly what it broke and up to three chances
    to fix it. That turns rejections into posts instead of into gaps.
    """
    facts = "\n".join(f"  - {f}" for f in brief.get("facts") or [])
    today = date.today()
    ask = (
        f"Write ONE post.\n\n"
        # The researcher was always told the date; the writer never was, and a
        # model without it cannot know a deadline has passed. It wrote "renueva
        # tu EAD antes del 9 de septiembre" on the 23rd, for a post that would
        # publish days later still.
        f"TODAY IS {today.isoformat()}. This post will publish between "
        f"{(today + timedelta(days=1)).isoformat()} and "
        f"{(today + timedelta(days=10)).isoformat()}. "
        f"Any date shown on the cover or a sub is the date of the EVENT from "
        f"the facts (when the rule, order or release happened) - never today's "
        f"date. The cover may not use an acronym or code (DV, EAD, PM-602) that "
        f"the explanation only defines later: say it in plain words there. "
        f"The cover headline NAMES the thing that changed - the form, program, "
        f"fee or rule ('USCIS cambia el formulario I-485 de la Green Card'), "
        f"never just 'una nueva versión' or 'un cambio'. "
        f"Never tell the reader to "
        f"act by a date that falls before then. If the source's deadline has "
        f"already passed, say what that means NOW - what happens next, or what "
        f"someone who missed it can still do - or leave the date out.\n\n"
        f"TOPIC: {brief.get('topic')}\n"
        f"ANGLE FOR THE READER: {brief.get('angle')}\n"
        f"WHY NOW: {brief.get('why_now')}\n"
        f"SOURCE: {brief.get('source_title') or ''} {brief.get('source_url')}\n"
        f"VERIFIED FACTS — use only these, invent nothing:\n{facts}\n\n"
        + _how_block(brief) +
        f"{SCHEMA}\n\n"
        f'Its "slug" must start with "{slug_prefix}-", followed by a short '
        f"kebab-case description of the topic.\n"
        f"The cover eyebrow is exactly \"{SERIES} {series_no:03d}\".\n"
        f"{_slide_plan()}\n\n"
        + hooks.ban_list(ACCT)
        # Last, because the last instruction is the one a small model
        # actually follows, and these are the two it drops most often.
        + f"\n\nBefore you answer, check the caption ends with these two "
          f"things, in this order:\n"
          f"  1. the line \"{ACCT['cta_line']}\"\n"
          f"  2. 8-10 lowercase hashtags, on their own line\n"
          f"A caption missing either one is discarded unread."
    )

    # Explain first, in plain prose checked by a first-time reader; the slides
    # are then built from those sentences. See _explain().
    sentences, cleared = _explain(brief, slug_prefix)
    if not sentences:
        # Every round failed (an Ollama restart did this on 2026-09-24).
        # Writing slides with no checked sentences is the pre-explanation
        # path that produced the unreadable posts; a spare topic is cheaper.
        raise llm.LLMError("no explanation could be produced for this topic")
    if sentences:
        ask += ("\n\nTHE EXPLANATION - already read and understood by a "
                "first-time reader. The slides must carry these sentences WORD "
                "FOR WORD: every step slide's body is one of them (or two, if "
                "they fit), copied exactly, in this order. You write the "
                "headlines, the cover, the recap arrows and the caption; the "
                "explaining is already done - do not shorten, merge or "
                "paraphrase these sentences. Headlines, the cover and the "
                "recap arrows may use ONLY terms these sentences already use, "
                "in the same plain words - no new agency name, acronym, legal "
                "term or synonym (not 'DHS' if the sentences say 'el "
                "gobierno', not 'residentes legales permanentes' if they say "
                "'Green Card'):\n"
                + "\n".join(f"  {i}. {s}" for i, s in enumerate(sentences, 1)))

    best = llm.structured(
        BRAND, ask, POST_SCHEMA,
        model=WRITER, require=("slides", "caption"),
        label=f"write:{slug_prefix}", temperature=0.8, think=WRITER_THINK,
        # A post with thinking runs 8-11K tokens; one repair round ran to
        # 27K (2026-09-24). The cap bounds a runaway; a cut-off answer is
        # an LLMError the caller already handles.
        max_tokens=20000,
    )
    if sentences:
        _snap(best, sentences)
        # validate() holds headlines, subs and recap arrows to these words
        # (_off_script). Kept on the post until main() has validated the
        # graded cover too; dropped before the spec is saved.
        best["_sentences"] = sentences
    errs = validate(best)

    # Five rounds, because three was not enough. A single pass typically
    # halves the errors rather than clearing them, and posts have been lost
    # eighteen characters over budget with rounds still on the table. The
    # calls are free and local; a gap in the queue is not.
    rounds = 5
    for rnd in range(rounds):
        if not errs:
            break
        print(f"  {slug_prefix}: {len(errs)} rule(s) broken — "
              f"fix {rnd + 1}/{rounds}")
        try:
            candidate = llm.structured(
                BRAND, None, POST_SCHEMA,
                model=WRITER, require=("slides", "caption"),
                label=f"fix{rnd + 1}:{slug_prefix}", temperature=0.5, think=WRITER_THINK,
                max_tokens=16000,
                messages=[
                    {"role": "user", "content": ask},
                    {"role": "assistant",
                     "content": json.dumps(best, ensure_ascii=False)},
                    {"role": "user", "content":
                        "That post breaks rules the publisher enforces, so it "
                        "cannot ship as written:\n"
                        + "\n".join(f"  - {e}" for e in errs) +
                        "\n\nReturn the whole post again with exactly those "
                        "problems fixed and nothing else changed — same topic, "
                        "same facts, same source. Where a length limit is "
                        "exceeded, cut words, never a slide. If the slide "
                        "count is wrong, add the missing slides from the arc "
                        "rather than padding the ones you have.\n\n"
                        + _slide_plan()},
                ],
            )
        except llm.LLMError as e:
            print(f"  {slug_prefix}: fix pass failed ({e})")
            break
        # A repair can make things worse, so never take one that breaks more.
        # Equal is taken and the loop continues: a rewrite that trades one
        # overlong slide for another is a fresh sample of the same attempt,
        # and the round cap is what stops this, not the first non-improvement.
        # Several posts have sat one error from shipping when the loop gave up.
        # A fresh candidate carries no checked sentences: without them the
        # off-script rule is silent on it and _snap later crashes on an empty
        # list (2026-09-24, lost a post that had reached one doubt).
        candidate["_sentences"] = sentences
        if sentences:
            _snap(candidate, sentences)
        fresh = validate(candidate)
        if len(fresh) <= len(errs):
            best, errs = candidate, fresh
        # A worse round is not a signal to stop: every round samples with a
        # fresh seed, so one regression is noise. Stopping at the first one
        # abandoned a post over a one-word filler headline with three of five
        # rounds unused (2026-09-24). The best candidate is kept either way.

    # Last resort, and the one that actually lands the close ones: stop
    # rewriting the post and just shorten the fields that overflow.
    if errs:
        import copy
        trimmed = copy.deepcopy(best)
        if _tighten(trimmed):
            after = validate(trimmed)
            # Take it when it breaks fewer rules, and ALSO when it breaks the
            # same number with less text on the slides. Requiring the count to
            # drop threw away a post whose every overlong field had just been
            # fixed, because one budget error survived and the arithmetic came
            # out equal — so the original, longer version was kept and
            # rejected. Shorter slide text cannot make these rules harder.
            shorter = _slide_chars(trimmed) < _slide_chars(best)
            if len(after) < len(errs) or (len(after) <= len(errs) and shorter):
                print(f"  {slug_prefix}: tightened {len(errs)} -> {len(after)} "
                      f"rule(s), {_slide_chars(best)} -> "
                      f"{_slide_chars(trimmed)} chars")
                best, errs = trimmed, after

    _respace(best)
    best, unclear = _clarity_pass(best, ask, brief, slug_prefix, cleared=cleared)
    before_truth = json.dumps(best, sort_keys=True)
    best, truth = _truth_pass(best, ask, brief, slug_prefix, cleared=cleared)
    # A truth fix or a deletion can leave a gap a reader trips on. Read again
    # only if the post changed - the check costs about a minute.
    if json.dumps(best, sort_keys=True) != before_truth and not validate(best):
        unclear, _ = clarity.read(best, brief, ACCT, model=MODEL,
                                  label=f"{slug_prefix}/final")
        unclear = _uncleared(unclear, cleared)
    best["_factcheck"] = truth
    best["_clarity"] = unclear
    return best


# At most this many blocking doubts may survive the rewrites. The reader is a
# 30B model and never reaches zero on a real post - calibrated on the posts
# the hosted model wrote, it still asked what USCIS was - while the posts the
# owner held for being unreadable left it 4-5 blockers each.
CLARITY_MAX = int(ACCT.get("clarity_max_doubts", 1))


def _clarity_pass(post, ask, brief, slug_prefix, rounds=2, cleared=()):
    """Would a first-time reader understand this post, with no doubt left?

    The reader (clarity.py) sees only the slides, as a Reel viewer does, and
    quotes every place it stopped. The bodies are sentences the reader has
    already passed (_explain) and are exempt; what is left is the writer's
    own layer - cover, headlines, subs, recap arrows - and a vague headline
    made of allowed words ('La guía aclara las categorías') passes every
    mechanical rule and still stops a reader. So the repair is TARGETED:
    only the quoted fields change, everything else is copied verbatim, and
    the bodies are restored from the checked sentences. Whole-post rewrites
    were measured not to converge (2026-09-23); targeted repair is what made
    the explanation stage converge (2026-09-24).
    """
    if validate(post):
        return post, []
    doubts, rep = clarity.read(post, brief, ACCT, model=MODEL, label=slug_prefix)
    doubts = _uncleared(doubts, cleared)
    print(f"  clarity:{slug_prefix} {len(doubts)} blocking doubt(s)")
    for d in doubts[:4]:
        print(f"    - {d[:200]}")
    # The writer's own fields get the same stability the bodies have: a
    # headline, sub or arrow that drew no doubt stays cleared while its text
    # is unchanged. Without it the reader re-flagged passed fields and a
    # targeted round went 3 -> 4 (2026-09-24).
    stable = {v for _, _, v in _slide_strings(post)
              if not any(_mentions(d, v) for d in doubts)}
    for rnd in range(rounds):
        if len(doubts) <= 0:
            break
        try:
            cand = llm.structured(
                BRAND, None, POST_SCHEMA,
                model=WRITER, require=("slides", "caption"),
                label=f"clear{rnd + 1}:{slug_prefix}", temperature=0.4,
                max_tokens=16000,
                think=WRITER_THINK,
                messages=[
                    {"role": "user", "content": ask},
                    {"role": "assistant",
                     "content": json.dumps(post, ensure_ascii=False)},
                    {"role": "user", "content": _clarity_fix_note()
                        + "\n".join(f"  - {d}" for d in doubts)
                        + "\n\n" + _slide_plan()},
                ],
            )
        except llm.LLMError as e:
            print(f"  {slug_prefix}: clarity fix failed ({e})")
            break
        _respace(cand)
        # The candidate must carry the checked sentences (validate() holds
        # its headlines to them) and get its bodies restored from them.
        cand["_sentences"] = post.get("_sentences")
        _snap(cand, post.get("_sentences") or [])
        if validate(cand):
            _tighten(cand)
        if validate(cand):
            print(f"  {slug_prefix}: clarity fix broke the format - not kept")
            continue
        again, _ = clarity.read(cand, brief, ACCT, model=MODEL,
                                label=f"{slug_prefix}/clear{rnd + 1}")
        again = _uncleared(again, set(cleared) | stable)
        print(f"  clarity:{slug_prefix} {len(doubts)} -> {len(again)} blocking doubt(s)")
        if len(again) < len(doubts):
            post, doubts = cand, again
            stable |= {v for _, _, v in _slide_strings(cand)
                       if not any(_mentions(d, v) for d in again)}
    return post, doubts


def _clarity_fix_note():
    return ("A first-time reader - someone who sees ONLY these slides, with no "
            "caption and no context - read this post and stopped ONLY at the "
            "fields quoted below. Return the same post with every other field "
            "copied EXACTLY as it is: do not touch a body, a headline, a sub, "
            "an arrow or the caption that is not quoted below. For each quoted "
            "field, rewrite that field alone so the reader understands it on "
            "first read: a complete sentence with a subject and a verb, the "
            "term it uses explained in the same words the step bodies use, no "
            "new term, number, date, consequence or instruction. A headline "
            "must say what its slide tells the reader, not name a category "
            "('La guía aclara las categorías' says nothing; 'Qué ayudas cuentan "
            "para la carga pública desde el 18 de septiembre' says something)."
            "\n\nWHERE THE READER STOPPED:\n")


def _respace(post):
    """Put back the spaces the model drops between sentences, on every slide.

    _space_sentences only ever ran on text that went through the shortener,
    so "no documentos.Si no lo haces" reached a published slide untouched.
    """
    for sl in post.get("slides") or []:
        for key in ("headline", "sub", "body"):
            v = sl.get(key)
            if isinstance(v, str):
                sl[key] = _space_sentences(v)
            elif isinstance(v, list):
                for seg in v:
                    if isinstance(seg, dict) and isinstance(seg.get("t"), str):
                        seg["t"] = _space_sentences(seg["t"])
                # A segment boundary is where the space went missing: the
                # schema example split slide 2 into "the explanation" and "the
                # comparison", and "Usa tarjeta o transferencia." + "Igual que
                # ..." rendered as "transferencia.Igual" on a slide.
                segs = [x for x in v if isinstance(x, dict)
                        and isinstance(x.get("t"), str)]
                for a, b in zip(segs, segs[1:]):
                    if a["t"] and b["t"] and not a["t"][-1].isspace() \
                            and not b["t"][0].isspace():
                        a["t"] += " "
        if isinstance(sl.get("items"), list):
            sl["items"] = [_space_sentences(x) if isinstance(x, str) else x
                           for x in sl["items"]]


def _truth_fix_note():
    note = ("A fact-checker compared this post with its source page and found "
            "the problems below. Fix every one by saying only what the source "
            "page says: where the post contradicts the page, say what the page "
            "says instead; where the post says something the page does not, "
            "remove it. Do not add any fact, number, deadline, consequence or "
            "instruction that is not on the page.")
    if ACCT.get("require_source_url"):
        note += (" This account publishes immigration news that people act "
                 "on, and a wrong fact can cost a reader their status. Never "
                 "tell the reader to leave the country, never tell them not to "
                 "worry, never tell them to keep or stop using a benefit unless "
                 "the page says exactly that. If the source is a proposal, the "
                 "cover and slide 2 must say it is a proposal that is not yet "
                 "in force.")
    return note + (" Keep the same topic, the same source and every slide; "
                   "change only what the problems require.\n\nPROBLEMS:\n")


def _verify(post, brief, label, cleared=()):
    """factcheck.verify_detail, minus objections to sentences the explanation
    stage already verified. The checker is not stable (see _explain), and on
    2026-09-24 it re-judged five verified bodies as unsupported and failed
    the post on 'only 1 claim backed'. With cleared sentences on the post,
    that total is meaningless and is dropped too."""
    errs, bad = factcheck.verify_detail(post, brief, ACCT, model=MODEL,
                                        label=label)
    if not cleared:
        return errs, bad
    errs = [e for e in _uncleared(errs, cleared) if not e.startswith("only ")]
    bad = [b for b in bad if not any(_mentions(b, s) for s in cleared)]
    return errs, bad


def _truth_pass(post, ask, brief, slug_prefix, rounds=2, cleared=()):
    """Is the post TRUE to its source? If not, show the model exactly where it
    is not, give it two chances to fix that, and hand back what remains.

    Runs last, on a post that already satisfies every mechanical rule - a
    format problem is cheaper to fix and cheaper to find, and fact-checking a
    post that will be rejected anyway is wasted minutes. A truth fix that
    breaks the format is not kept. The errors that survive are returned, and
    author() treats any as a rejection: a spare topic takes the slot.
    """
    if validate(post):
        return post, []
    errs, bad = _verify(post, brief, slug_prefix, cleared)
    for rnd in range(rounds):
        if not errs:
            break
        print(f"  {slug_prefix}: {len(errs)} factual problem(s) — "
              f"truth fix {rnd + 1}/{rounds}")
        try:
            cand = llm.structured(
                BRAND, None, POST_SCHEMA,
                model=WRITER, require=("slides", "caption"),
                label=f"truth{rnd + 1}:{slug_prefix}", temperature=0.3, think=WRITER_THINK,
                max_tokens=16000,
                messages=[
                    {"role": "user", "content": ask},
                    {"role": "assistant",
                     "content": json.dumps(post, ensure_ascii=False)},
                    {"role": "user", "content": _truth_fix_note()
                        + "\n".join(f"  - {e}" for e in errs)
                        + "\n\n" + _slide_plan()},
                ],
            )
        except llm.LLMError as e:
            print(f"  {slug_prefix}: truth fix failed ({e})")
            break
        _respace(cand)
        cand["_sentences"] = post.get("_sentences")
        _snap(cand, post.get("_sentences") or [])
        if validate(cand):
            _tighten(cand)
        if validate(cand):
            print(f"  {slug_prefix}: truth fix broke the format — not kept")
            continue
        again, again_bad = _verify(cand, brief, f"{slug_prefix}/fix{rnd + 1}",
                                   cleared)
        if len(again) < len(errs):
            post, errs, bad = cand, again, again_bad
        else:
            break

    # Last: delete what is still untrue instead of asking for another rewrite.
    # A rewrite trades one invented detail for another; a deletion only
    # removes, so it is the one repair that cannot make the post less true.
    # The result is re-checked in full - deletion is a repair, not a pass.
    if errs and bad:
        cut = factcheck.strip_claims(post, bad)
        if cut is not None and not validate(cut):
            left, left_bad = _verify(cut, brief, f"{slug_prefix}/cut", cleared)
            print(f"  {slug_prefix}: deleted {len(bad)} untrue claim(s) — "
                  f"{len(errs)} -> {len(left)} problem(s)")
            if len(left) < len(errs):
                post, errs = cut, left
        else:
            print(f"  {slug_prefix}: untrue claims sit in a headline or would "
                  f"empty a slide — cannot delete them safely")
    return post, errs


STRATEGIST = """You maintain the strategy notes for an Instagram account.

You are not writing a post and not talking to a person. Write the finished \
note itself, in English, in the third person, as if it were already in the \
file — never a description of what you are about to write, never "I need to", \
never a restatement of the request.

Say only what the numbers support. "Not enough data to tell" is a complete and \
useful note; an invented conclusion is read by every future batch as fact."""

# Signatures of a model narrating its task instead of doing it. The first
# local run wrote 1,160 characters of "This is a detailed request that asks me
# to act as a content strategist..." straight into strategy.md — which then
# feeds back into every future prompt as if it were an earned finding.
_META = ("i need to", "i'll ", "i will ", "let me ", "my task", "the user",
         "this is a detailed request", "i should ", "as an ai", "i am asked",
         "the request", "i have been asked")


def _splice(text, heading_prefix, bullets):
    """Insert bullets directly under the first heading starting with a prefix."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith(heading_prefix):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            return "\n".join(lines[:j] + bullets + [""] + lines[j:])
    return text + "\n\n" + heading_prefix + "\n\n" + "\n".join(bullets) + "\n"


def _append_log(text, span, notes, today):
    """Add this batch's entry where the file already keeps them.

    The two accounts use different conventions and both are the owner's:
    inmigraforma nests '### Batch N' entries inside a '## Batch log'
    container, leverageai writes one top-level '## Batch notes — ...' section
    per batch. Match whichever the file uses rather than imposing one, and
    put the entry next to its siblings rather than at the end of the file —
    leverageai keeps a '## Next brief' section last, on purpose.
    """
    lines = text.split("\n")

    def section_end(start):
        return next((i for i in range(start + 1, len(lines))
                     if lines[i].startswith("## ")), len(lines))

    container = next((i for i, l in enumerate(lines)
                      if l.startswith("## Batch log")), None)
    if container is not None:
        entry = f"### Batch — posts {span}, drafted {today}\n\n{notes}"
        end = section_end(container)
        body = "\n".join(lines[container:end]).rstrip()
        return "\n".join(lines[:container] + [body, "", entry, ""] + lines[end:])

    entry = f"## Batch notes — posts {span} ({today})\n\n{notes}"
    siblings = [i for i, l in enumerate(lines) if l.startswith("## Batch notes")]
    if siblings:
        end = section_end(siblings[-1])
        return "\n".join(lines[:end] + [entry, ""] + lines[end:])
    return text.rstrip() + "\n\n" + entry + "\n"


def update_strategy(posts, start_index):
    """The feedback loop's memory, edited against this batch's evidence.

    Its own call now, and an EDIT rather than a rewrite. Two changes from the
    Anthropic version, both forced by what actually happened:

    Separate call, because the strategy used to come back in the same tool
    call as the posts — so a batch that failed to parse lost the strategy
    reasoning with it.

    An edit, because a 30B model asked to reproduce a 15KB markdown file with
    a few lines changed returns a summary of the request instead. The model is
    asked only for what is new — a log entry, and any hypothesis the numbers
    now settle — and the splicing is done here, where nothing can be lost.
    """
    covered = "\n".join(f"- {p.get('slug')}: "
                        f"{hooks.flatten((p.get('slides') or [{}])[0].get('headline'))}"
                        for p in posts)
    current = ""
    if os.path.exists(ACCT.strategy):
        with open(ACCT.strategy, encoding="utf-8") as f:
            current = f.read()

    ask = (f"{strategy_context()}\n\n"
           f"The batch just written covers:\n{covered}\n\n"
           f"Do not reproduce the strategy file. Return only the log entry for "
           f"this batch, plus any hypothesis the performance brief above now "
           f"actually settles. If the brief settles nothing, return an empty "
           f"promotions list — that is the normal answer.")
    # STRATEGIST, not BRAND: this is an analyst's note in English, and the
    # brand prompt would have it written in the account's publishing voice
    # and language, which for inmigraforma is Spanish.
    try:
        out = llm.structured(
            STRATEGIST, ask, STRATEGY_SCHEMA,
            model=MODEL, require=("bet", "evidence"), label="strategy",
            temperature=0.3,
            # Thinking ON, which is the opposite of what it looks like it
            # should be. With `think` off, qwen3 still reasons — and under
            # constrained decoding the only place its reasoning can go is the
            # first string field in the schema. That is literally what landed
            # in strategy.md: "The user is asking me to write a strategy note
            # based on the performance data provided..." Giving the model a
            # thinking channel keeps the answer out of the fields.
            think=True,
        )
    except llm.LLMError as e:
        print(f"::warning::strategy update failed ({e}) — leaving the file as "
              f"it was; the posts are unaffected")
        return None

    today = date.today().isoformat()
    span = f"{start_index:02d}-{start_index + len(posts) - 1:02d}"
    # The heading is ours; the model sometimes writes its own copy of the
    # previous batch's one into the body, which then sits under the real
    # heading looking like a second entry.
    def body_only(s):
        keep = [ln for ln in (s or "").split("\n")
                if not re.match(r"\s*(#+\s|Batch notes\b|### Batch\b)", ln)]
        return " ".join(" ".join(keep).split())

    notes = f"{body_only(out['bet'])} {body_only(out['evidence'])}".strip()

    low = notes.lower()
    hit = next((m for m in _META if m in low), None)
    if hit:
        print(f"::warning::strategy note is the model narrating the request "
              f"({hit!r}), not a finding — discarding it and keeping the file")
        return None
    if len(notes) > 1200:
        print(f"::warning::strategy note ran to {len(notes)} chars where a "
              f"paragraph was asked for — discarding it and keeping the file")
        return None

    if not current:
        return (f"# Strategy — {ACCT.handle}\n\n## Confirmed\n\n## Disproven\n\n"
                f"## Batch log\n\n### Batch — posts {span}, drafted {today}\n\n"
                f"{notes}\n")

    updated = _append_log(current, span, notes, today)
    for p in out.get("promotions") or []:
        section = p.get("section")
        if section not in ("Confirmed", "Disproven"):
            continue
        bullet = (f"- {p['finding'].strip()} "
                  f"({p['evidence'].strip()} — batch of {today})")
        updated = _splice(updated, f"## {section}", [bullet])
        print(f"  strategy: {section} <- {p['finding'][:70]}")

    # The file only ever grows here, so a shrink means the splice went wrong.
    # strategy.md is months of findings paid for with real reach; a bug in
    # this function must lose none of them.
    if len(updated) < len(current):
        print("::warning::strategy edit came out shorter than the file on disk "
              "— refusing it and keeping the original")
        return None
    print(f"  strategy: +{len(updated) - len(current)} chars")
    return updated


def author(count, start_index, avoid):
    """Research, then write post by post. Returns {"posts", "strategy"}.

    Three passes where there used to be one. A hosted frontier model could
    hold "search the web, write seven posts, and rewrite the strategy file"
    in a single 128K response; a 30B local model cannot, and the failure mode
    was all-or-nothing. Splitting it keeps every output main() consumes.
    """
    llm.ensure(MODEL); llm.ensure(WRITER)

    briefs = research(count, avoid)
    if not briefs:
        sys.exit("Research produced no usable topics — nothing to write. "
                 "Check that search works: python search.py <query>")

    posts = []
    for i, brief in enumerate(briefs):
        if len(posts) >= count:
            break
        n = start_index + len(posts)
        try:
            post = write_post(brief, f"post{n:02d}", n)
        # ANY failure on one post costs that post, never the batch. This is
        # the third time finished work was lost to a late crash: a bad opener
        # check (five batches, 2026-09-03..07), and a model timeout that took
        # three verified leverageai posts on 2026-09-23.
        except Exception as e:                             # noqa: BLE001
            import traceback
            print(f"::warning::post{n:02d} could not be written ({e}) — skipping "
                  f"this topic")
            if not isinstance(e, llm.LLMError):
                # A crash, not a model failure: the line that raised is the
                # only thing that makes it fixable.
                print("  " + traceback.format_exc().strip().replace("\n", "\n  ")[-1500:])
            continue
        # The model names its own slug and drifts from the prefix it was given;
        # the queue keys on post number, so the prefix is not negotiable.
        slug = str(post.get("slug") or "")
        if not slug.startswith(f"post{n:02d}"):
            topic = re.sub(r"[^a-z0-9]+", "-",
                           (brief.get("topic") or "post").lower()).strip("-")
            post["slug"] = f"post{n:02d}-{topic[:40].rstrip('-')}"
        # Not true to its source, after the truth pass had its chances: the
        # post is dropped and the next spare brief takes the slot. A gap in the
        # queue is recoverable; a published wrong fact on an account people act
        # on is not - post68 would have told readers to keep using Medicaid.
        untrue = post.pop("_factcheck", None) or []
        unclear = post.pop("_clarity", None) or []
        post.pop("explain_it_to_a_friend", None)
        # Same for a post that still breaks the mechanical rules after every
        # repair: drop it HERE, so the next spare brief takes the slot. It used
        # to be appended and rejected later in main(), where no spare could
        # replace it - one bad post meant one fewer post, every time.
        broken = validate(post)
        if broken:
            print(f"REJECTED {post.get('slug')} — still breaks "
                  f"{len(broken)} rule(s) after repair: "
                  + "; ".join(e[:120] for e in broken[:3]))
            continue
        if untrue:
            print(f"REJECTED {post.get('slug')} — not true to its source: "
                  + "; ".join(e[:160] for e in untrue[:3]))
            continue
        # Unclear to a first-time reader after the rewrites: posts are for
        # humans, and a true post nobody understands helps nobody (owner,
        # 2026-09-23).
        if len(unclear) > CLARITY_MAX:
            print(f"REJECTED {post.get('slug')} — a first-time reader is left "
                  f"with {len(unclear)} doubt(s): "
                  + "; ".join(e[:160] for e in unclear[:3]))
            continue
        post["source_url"] = brief.get("source_url", "")
        posts.append(post)
        print(f"  wrote {post['slug']} ({len(post.get('slides') or [])} slides)")

    if not posts:
        sys.exit("Every post failed to author — nothing to queue.")
    if len(posts) < count:
        print(f"::warning::asked for {count} posts, authored {len(posts)}")

    return {"posts": posts, "strategy": update_strategy(posts, start_index)}


def hook_test(post, min_score, log_path=None):
    """Grade the cover blind against its alternatives and splice the winner in.

    Mutates the post's cover slide. Never raises: a grader failure must not
    cost us the batch — the authored cover simply survives, which is exactly
    the behaviour before this existed. An empty queue is worse than an ungraded
    hook, same reasoning as the zero-web-search warning above.
    """
    slides = post.get("slides") or []
    cover = next((s for s in slides if s.get("kind") == "cover"), None)
    if not cover:
        return "  (no cover slide — hook test skipped)"

    pool = [{"headline": cover.get("headline", ""), "sub": cover.get("sub", ""),
             "authored": True}]
    # Candidates must meet the stated headline AND sub limits; the authored
    # cover stays in the pool whatever its length, because it is also the
    # fallback and validate() is what judges it. The sub check is not
    # cosmetic: hook_test runs BEFORE validate and splices the winner's sub
    # into the cover, so an overlong candidate sub fails a post that the
    # author had written correctly.
    for c in post.get("hook_candidates") or []:
        if len(hooks.flatten(c.get("headline"))) > hooks.MAX_HEADLINE:
            continue
        if len(c.get("sub") or "") > _LIM["sub"]:
            continue
        pool.append({"headline": c["headline"], "sub": c.get("sub", ""),
                     "authored": False})

    if len(pool) < 2:
        return "  (no usable alternatives — hook test skipped)"

    try:
        scored = hooks.grade(ACCT, post["slug"], pool)
        retried = False

        # "Be blunt, a 6 out of 10 hook is a wasted video." One targeted retry:
        # fresh hooks join the pool rather than replace it, so a retry can lose.
        if scored[0]["score"] < min_score:
            extras = [c for c in hooks.retry(ACCT, BRAND, post, scored,
                                             max_sub=_LIM["sub"])
                      if len(hooks.flatten(c.get("headline"))) <= hooks.MAX_HEADLINE
                      and len(c.get("sub") or "") <= _LIM["sub"]]
            if extras:
                scored = hooks.grade(ACCT, post["slug"], pool + extras)
                retried = True

        chosen = scored[0]
        cover["headline"] = chosen["headline"]
        if chosen.get("sub"):
            cover["sub"] = chosen["sub"]

        hooks.log(ACCT, {
            "slug": post["slug"],
            "graded": date.today().isoformat(),
            "threshold": min_score,
            "retried": retried,
            "chosen": {k: chosen[k] for k in
                       ("headline", "sub", "rank", "score", "shape", "stopping",
                        "reason", "authored")},
            "candidates": [{k: c[k] for k in
                            ("headline", "rank", "score", "shape", "stopping",
                             "reason", "authored")} for c in scored],
        }, path=log_path)

        out = hooks.report(post["slug"], scored, chosen)
        if chosen["score"] < min_score:
            out += (f"\n   ::warning::best hook still {chosen['score']:.1f} < "
                    f"{min_score} after a retry — publishing it anyway, but it "
                    f"is the weakest link in this post.")
        return out
    except Exception as e:
        return (f"  ::warning::hook test failed for {post['slug']} ({e}) — "
                f"keeping the authored cover ungraded.")


def repeated_openers(posts):
    """Comparison openers reused across a batch — the tic-forming pattern.

    Per-post validation cannot see this: each post is fine on its own, and
    only the batch reveals that every one of them opens the same way. Warns
    rather than rejects, because the fix is rewording, not discarding work.
    """
    seen = {}
    for post in posts:
        for sl in post.get("slides", []):
            # body is EITHER rich-text segments or a plain string - the
            # schema allows both. Assuming segments here crashed every batch
            # from 2026-09-03 to 09-07, after the model had already authored
            # the posts, so each failure burned a full batch.
            body = hooks.flatten(sl.get("body"))
            for opener in ("funciona igual que", "pasa lo mismo con",
                           "piensalo asi", "piénsalo así", "igual que en",
                           "es como", "imagina que"):
                if opener in body.lower():
                    seen.setdefault(opener, []).append(post.get("slug"))
    return {k: v for k, v in seen.items() if len(v) > 1}


_MONTHS = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
           "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11,
           "dic": 12}
# A date is only a DEADLINE when the reader is told to act by it. "Ayuda
# recibida antes del 18 de septiembre" describes a cutoff and is fine to
# repeat after the 18th; "manda el formulario antes del 14 de septiembre" is
# an instruction, and publishing it on the 26th - which happened - is wrong.
# Present-tense and infinitive forms only: "mandaste ... antes del 31 de
# agosto" is a condition about the past, not an order.
_ACTION = (r"(?:manda|mandar|mandes|mandas|mande|env[ií]a|env[ií]e|env[ií]as|"
           r"enviar|presenta|presentar|presentes|solicita|solicitar|aplica|"
           r"aplicar|reg[ií]strate|registrar|paga|pagar|renueva|renovar|llena|"
           r"llenar|completa|completar|comenta|comentar|opina|opinar)\b")
_DEADLINE = re.compile(
    r"(?:" + _ACTION + r"[^.]{0,50}?(?:antes del?|hasta el|a m[aá]s tardar el)"
    r"|tienes hasta el|fecha l[ií]mite:?|vence el|vencen el|plazo:? hasta el)"
    r"\s+(\d{1,2})\s*(?:de\s+)?([a-záéíóú]{3,10})\.?"
    r"(?:\s+(?:de\s+)?(\d{4}))?", re.I)

# Official names that stay in English by the account's own voice rule, and
# must not make a Spanish slide read as English: "Familia: Dates for Filing".
_OFFICIAL_EN = ("dates for filing", "final action dates", "final action date",
                "visa bulletin", "green card", "public charge",
                "federal register", "admit until date", "notice to appear",
                "parole in place", "adjustment of status", "request for evidence",
                "employment authorization", "priority date", "policy manual")

# Words that only Spanish uses. A slide sentence of four words or more with
# none of them is English - "USCIS 2026 changes listed, no notice search
# needed." published on inmigraforma's plain-language slide. "no", "a" and "o"
# are left out on purpose: English has them too.
_ES_WORDS = {"el", "la", "los", "las", "de", "del", "que", "y", "en", "un",
             "una", "por", "para", "con", "si", "tu", "tus", "es", "se", "al",
             "lo", "su", "sus", "más", "ya", "hay", "te", "le", "les", "esto",
             "este", "esta", "como", "pero", "cuando", "donde", "qué", "cómo",
             "sí", "está", "están", "son", "puedes", "debes", "tienes"}


_EN_FUNCTION = {"the", "and", "with", "your", "you", "is", "are", "for",
                "this", "that", "will", "of", "to", "it", "be", "have", "has",
                "not", "can", "should", "must", "need", "before", "after",
                "from", "by", "if", "or"}


def _reads_english(text):
    """Is this slide text English, on an account that publishes in Spanish?

    Calibrated on real posts in both directions. Two things prove Spanish
    outright and English never has either: an accent, a tilde or an opening
    ¿/¡ - "¿Me afecta a mí?" uses no word from any list and is plainly
    Spanish. Short text is left alone. Beyond that it must lack every Spanish
    function word AND show something English - a function word, or the -ed
    and -ing endings that gave "USCIS 2026 changes listed, no notice search
    needed." away. "Usa formulario I-130" has neither, and is Spanish.
    """
    if re.search(r"[áéíóúñü¿¡]", text.lower()):
        return False
    low = text.lower()
    for name in _OFFICIAL_EN:
        low = low.replace(name, " ")
    words = re.findall(r"[a-z]+", low)
    if len(words) < 4 or _ES_WORDS & set(words):
        return False
    return bool(_EN_FUNCTION & set(words)) or any(
        len(w) >= 5 and w.endswith(("ed", "ing")) for w in words)


def _slide_strings(post):
    for i, sl in enumerate(post.get("slides") or [], 1):
        for key in ("headline", "sub", "body"):
            v = hooks.flatten(sl.get(key))
            if v:
                yield i, key, v
        for item in sl.get("items") or []:
            if isinstance(item, str):
                yield i, "item", item


def _slack(cap):
    """A field cap plus 5% (at least 3 characters). See validate()."""
    return cap + max(3, cap // 20)


def _reader_safety(post, today=None):
    """Rules about what a reader is TOLD, which the fact-check backs up but
    should never be the only thing standing between a reader and harm.

    Each one is here because the local model did it on a published post:
    telling readers to leave the country over a rule still open for comment;
    "use Medicaid sin miedo" the week benefits started to count; English on a
    Spanish account's plain-language slide; "Embajadores' hijos"; a deadline
    that had already passed by the day the post went out.
    """
    errs = []
    text = " ".join(v for _, _, v in _slide_strings(post)) + " " + (post.get("caption") or "")
    low = re.sub(r"ee\.?\s*uu\.?", "eeuu", text.lower())

    for rule in ACCT.get("forbidden_advice") or []:
        if re.search(rule["pattern"], low):
            errs.append(f"tells the reader something this account never says: "
                        f"{rule['why']} (matched {rule['pattern']!r}). Remove it.")

    if ACCT.get("slide_language") == "es":
        for i, key, v in _slide_strings(post):
            if _reads_english(v):
                errs.append(f"slide {i}: this {key} reads as English on a "
                            f"Spanish account: {v[:60]!r}")
            # "Embajadores' hijos" - a capitalised plural, an apostrophe, then
            # the noun it owns. Deliberately narrow: an English phrase quoted
            # from the source ('strongly disagrees' con la orden) is allowed.
            if re.search(r"\b[A-ZÁÉÍÓÚ][a-záéíóúñ]+s'\s+[a-záéíóúñ]|\b[A-Za-z]+'s\b", v):
                errs.append(f"slide {i}: English possessive in Spanish text: "
                            f"{v[:60]!r}")
        for i, sl in enumerate(post.get("slides") or [], 1):
            hl = hooks.flatten(sl.get("headline")).strip().lower().rstrip(".")
            if hl in ("the system", "el sistema"):
                errs.append(f"slide {i}: the headline 'El sistema' is the "
                            f"template's placeholder, not a headline — write "
                            f"what this slide actually tells the reader")

    # A deadline has to still be ahead on the day the post publishes, and the
    # post is queued behind up to a week of others.
    today = today or date.today()
    for m in _DEADLINE.finditer(text):
        mon = _MONTHS.get(m.group(2)[:3].lower())
        if not mon:
            continue
        try:
            when = date(int(m.group(3) or today.year), mon, int(m.group(1)))
        except ValueError:
            continue
        if when < today + timedelta(days=7):
            errs.append(f"deadline {m.group(0)!r} will have passed, or nearly, "
                        f"by the time this post publishes — drop it or pick a "
                        f"topic that is still actionable")

    # "Acepta el formulario viejo antes del 18 de septiembre", written on the
    # 24th: the reader is told about a window that closed last week. A
    # past date is described as what applies now, never as still ahead.
    for m in re.finditer(r"\b(antes del|hasta el|before|until|by)\s+"
                         r"(?:(\d{1,2})\s+de\s+([a-záéíóú]+)|([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}))"
                         r"(?:,?\s+(?:de\s+)?(\d{4}))?", text, re.I):
        day = m.group(2) or m.group(5)
        mon = _MONTHS.get((m.group(3) or "")[:3].lower()) or _MONTH_EN.get((m.group(4) or "").lower())
        if not (day and mon):
            continue
        try:
            when = date(int(m.group(6) or today.year), mon, int(day))
        except ValueError:
            continue
        if when < today:
            errs.append(f"{m.group(0)!r} is already past - say what applies "
                        f"now ('desde el ...'), not a window that has closed")

    # A post is written days before it is seen. "La respuesta cambió hoy" went
    # into a queue on 2026-09-23 about a page last updated on the 1st, to
    # publish on the 24th: false on every one of those days. Name the date.
    for i, key, v in _slide_strings(post):
        m = _RELATIVE_DAY.search(v)
        if m:
            errs.append(f"slide {i}: {m.group(0)!r} — the reader sees this days "
                        f"after it is written, so it is not true then. Say the "
                        f"date the source gives instead")

    # Two explaining slides that say the same thing are one slide of content
    # and a wasted 8 seconds of Reel. The same post put "Familiares: Dates for
    # Filing. Trabajo: Final Action Dates." on slides 2 and 3.
    bodies = [(i, set(_words_of(hooks.flatten(sl.get("body")))))
              for i, sl in enumerate(post.get("slides") or [], 1)
              if sl.get("kind") == "step"]
    for a in range(len(bodies)):
        for b in range(a + 1, len(bodies)):
            (i, x), (j, y) = bodies[a], bodies[b]
            if len(x) >= 4 and len(y) >= 4 and len(x & y) / min(len(x), len(y)) >= 0.7:
                errs.append(f"slides {i} and {j} say the same thing — each "
                            f"explaining slide must add something the reader "
                            f"did not have yet: what it means day to day, who "
                            f"it applies to, what to do")
    return errs


# Only a CHANGE dated to the writing day. "Revisa hoy tu caso" is advice and
# true whenever it is read; "cambió hoy" is a news claim with a date on it.
_RELATIVE_DAY = re.compile(
    r"\b(?:cambi|anunci|public|lanz|actualiz)\w*\s+(?:\w+\s+)?(?:hoy|ayer|esta semana)\b"
    r"|\b(?:desde|a partir de)\s+hoy\b"
    r"|\bhoy\s+(?:cambi|anunci|public|entr|lanz)\w*"
    r"|\b(?:changed|announced|launched|released|shipped|updated|published|"
    r"rolling out|rolls out|roll out|available|starts|starting|begins|beginning)"
    r"(?:[^.,;!?]{0,40}?\s)?(?:today|yesterday|this week)\b"
    r"|\b(?:as of|starting|from) today\b"
    r"|\b(?:today|yesterday)\s+(?:\w+\s+)?(?:announced|launched|released|shipped|changed)\b",
    re.I)


def _words_of(text):
    return [w for w in re.findall(r"[a-záéíóúñü0-9]+", (text or "").lower())
            if len(w) > 2]


# Headlines that say nothing. Each was on real posts, most copied from the
# schema's own examples ("The system" sat on almost every leverageai recap).
_FILLER = {"the system", "system", "el sistema", "que significa esto",
           "qué significa esto", "what this means", "what it means", "recap",
           "resumen", "summary", "la noticia en una frase completa",
           "open the right place first", "do this today", "framing question",
           "short", "paste this into the chat",
           "a full sentence that sets up the number"}
# "H-2Bpara": a code glued to the next word by a shortening pass.
_GLUED = re.compile(r"\b[A-Z0-9]+(?:-[A-Z0-9]+)+[a-záéíóúñ]{2,}\b")


# Glue words of six letters or more that a headline may use freely.
_GLUE = set("""puede pueden podría podrían ahora antes después desde hasta porque
cuando también sobre entre mientras aunque donde todos todas mucho mucha muchos
muchas otros otras nuevo nueva nuevos nuevas mismo misma cómo quién quiénes dónde
cuándo tiene tienen tener hacer hacen hecho cambia cambio cambios significa afecta
afectan aplica aplican todavía siempre nunca además dentro fuera manera través
about after before could would should there their these those which while where
every other others still today thing things maybe might really change changes
changed means affects apply applies works using yours already without through
because inside outside always never""".split())


def _stem(word):
    import unicodedata
    w = unicodedata.normalize("NFD", word.lower())
    # Four letters: Spanish verb forms diverge after that ('propone' /
    # 'propuso'), and a lenient stem only ever lets a synonym through.
    return "".join(c for c in w if not unicodedata.combining(c))[:4]


def _off_script(post):
    """Headlines, subs and recap arrows may only say what the checked
    explanation says. Five posts in a row (2026-09-24) passed the explanation
    stage 0/0 and were then rejected for a benefit, an agency or a synonym
    the writer added on top ('los trámites etíopes pueden ir más rápido',
    'DHS', 'residentes legales permanentes'). Telling it not to did nothing;
    this makes it a rule the repair loop enforces. A content word of six or
    more letters whose stem the explanation never uses is the tell.
    """
    sents = post.get("_sentences")
    if not sents:
        return []
    script = " ".join(sents) + " " + (ACCT.get("username") or "") + " " + \
        (ACCT.get("cta_line") or "")
    known = {_stem(w) for w in re.findall(r"[a-záéíóúñü]+", script.lower())
             if len(w) >= 6} | {_stem(w) for w in _GLUE}
    # The cover is stricter: it paraphrases the FIRST sentence, the plain
    # 'what happened' line, and may not reach for a term the explanation
    # only defines later ('Nueva regla de carga pública' on a cover, with
    # 'carga pública' explained two slides on - 2026-09-24).
    first_script = ((sents[0] if sents else "") + " " + (ACCT.get("username") or "")
                    + " " + (ACCT.get("cta_line") or ""))
    known_cover = {_stem(w) for w in re.findall(r"[a-záéíóúñü]+", first_script.lower())
                   if len(w) >= 6} | {_stem(w) for w in _GLUE}
    errs = []
    # The cover is read first. An acronym or code on it ('DHS', 'H-1B') that
    # the explanation defines later stops a reader at the first frame; the
    # six-letter rule below cannot see a three-letter acronym. The cover
    # paraphrases the first sentence, so that sentence sets what it may use.
    # ...plus whatever the account's reader persona is said to know (USCIS,
    # ICE) and the account's own name.
    first = (sents[0] if sents else "") + " " + (ACCT.get("clarity_reader") or "") \
        + " " + (ACCT.get("username") or "")
    cover = (post.get("slides") or [{}])[0]
    for key in ("headline", "sub"):
        text = hooks.flatten(cover.get(key)) or ""
        # Three letters minimum: "AI" on an AI-tools account is not jargon.
        for tok in re.findall(r"\b[A-Z]{3,5}\b|\b[A-Z]{1,2}-\d{1,4}[A-Z]?\b", text):
            if tok not in first:
                errs.append(f"slide 1: the cover {key} uses '{tok}' before "
                            f"anything explains it - say it in plain words "
                            f"on the cover, as the explanation's first "
                            f"sentence does")
                break
    for i, sl in enumerate(post.get("slides") or [], 1):
        fields = [("headline", hooks.flatten(sl.get("headline"))),
                  ("sub", hooks.flatten(sl.get("sub")))]
        fields += [("recap arrow", hooks.flatten(x)) for x in sl.get("items") or []]
        for key, text in fields:
            # The always-allowed arrows (read the notice, consult a lawyer,
            # beware of scams) are never in the explanation, by design.
            if factcheck.SAFE_ADVICE.search(text or ""):
                continue
            allowed = known_cover if i == 1 else known
            for w in re.findall(r"[a-záéíóúñü]+", (text or "").lower()):
                if len(w) >= 6 and _stem(w) not in allowed:
                    where = ("the explanation's first sentence" if i == 1
                             else "the checked explanation")
                    errs.append(f"slide {i}: the {key} says '{w}', a word "
                                f"{where} never uses - "
                                + ("the cover may only say what the first "
                                   "sentence says, in its plain words"
                                   if i == 1 else
                                   "headlines, subs and recap arrows may only "
                                   "say what the explanation says, in its own "
                                   "words"))
                    break
    return errs


def _clarity_rules(post):
    """Fragments a first-time reader cannot use, caught without a model.

    Added 2026-09-23 when the owner held a week of posts that were true and
    meaningless: "Spot-check skills. No AI allowed", "Connect tools / Ask
    Gemini / to draft proposals", a recap of "1. Familiares: Dates". The model
    reader (clarity.py) judges meaning; these are the shapes that never carry
    one.
    """
    errs = []
    for i, sl in enumerate(post.get("slides") or [], 1):
        hl = re.sub(r"[^\w\s]", "", hooks.flatten(sl.get("headline"))).strip().lower()
        if hl in _FILLER:
            errs.append(f"slide {i}: the headline '{hooks.flatten(sl.get('headline'))}' "
                        f"says nothing - write what this slide tells the reader, "
                        f"as a short full sentence")
        sub = hooks.flatten(sl.get("sub"))
        if sub and len(sub.split()) < 5:
            errs.append(f"slide {i}: the sub '{sub}' is a fragment - write one "
                        f"complete sentence (what is new, or when it happened)")
        if sl.get("kind") == "step":
            body = hooks.flatten(sl.get("body"))
            if body and len(body.split()) < 6:
                errs.append(f"slide {i}: '{body}' is a fragment - write one "
                            f"complete sentence with a subject and a verb that "
                            f"says what to do, where, or what it means")
        for item in sl.get("items") or []:
            t = hooks.flatten(item)
            # "Lee la página oficial." - which page? The template's arrow
            # says where; the writer drops it and the reader asks.
            if re.search(r"\b(lee|revis[ae]|consult[ae]|read|check)\w*\b.{0,30}"
                         r"(p[aá]gina|aviso|sitio|notice|page|site)\b", t, re.I) \
                    and not re.search(r"descripci|caption|enlace|link", t, re.I):
                errs.append(f"slide {i}: recap arrow '{t}' says to read the "
                            f"official page without saying where - add "
                            f"'(link en la descripción)' or 'link in the caption'")
            if len(t.split()) < 4:
                errs.append(f"slide {i}: recap item '{t}' is a fragment - each "
                            f"arrow must be a short complete sentence that "
                            f"makes sense on its own")
        for _, key, v in [(i, k, hooks.flatten(sl.get(k))) for k in ("headline", "sub", "body")]:
            m = _GLUED.search(v or "")
            if m:
                errs.append(f"slide {i}: '{m.group(0)}' has two words glued "
                            f"together - put the space back")
    return errs


def validate(post):
    """Catch the failure modes that would silently ship a broken carousel."""
    errs = _reader_safety(post) + _clarity_rules(post) + _off_script(post)
    if not post.get("slug"):
        errs.append("missing slug")
    cap = post.get("caption", "")
    if not cap:
        errs.append("missing caption")
    if len(cap) > 2200:
        errs.append(f"caption {len(cap)} chars > 2200 Instagram limit")
    # The brand prompt has always demanded the follow line and 8-10 hashtags;
    # a hosted model simply complied and nothing checked. A local model drops
    # both about a third of the time, and a caption with no hashtags reaches
    # nobody on a discovery surface — so the requirement is a check now.
    cta = (ACCT.get("cta_line") or "").strip().rstrip(".")
    if cta and cta.lower() not in cap.lower():
        errs.append(f"caption is missing the follow line: {cta!r}")
    tags = re.findall(r"#\w+", cap)
    if len(tags) < 5:
        errs.append(f"caption carries {len(tags)} hashtags, needs 8-10")
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
    # an implicit promise of a hand-off. The keyword must be QUOTED or SHOUTED,
    # which is how these calls-to-action are always written. Matching a bare
    # word after "comment" flagged an immigration post for the phrase "comment
    # period for", which is the name of a real thing this account reports on.
    # Matched against the caption as written, not the lowercased copy: the
    # SHOUTED keyword is half of what identifies one of these.
    for pat in (r'(?i:comment)\s+(?:["“\'][\w ]+["”\']|\b[A-Z]{2,}\b)\s+'
                r'(?i:for|and|to get)\b',
                r'(?i:comenta)\s+(?:["“\'][\w ]+["”\']|\b[A-Z]{2,}\b)'
                r'\s+(?i:y\s+te)\b'):
        m = re.search(pat, cap)
        if m:
            errs.append(f"caption implies a hand-off nobody will make: {m.group(0)!r}")

    slides = post.get("slides", [])
    if not 2 <= len(slides) <= 10:
        errs.append(f"{len(slides)} slides, Instagram carousels allow 2-10")
    # Instagram's floor is 2; the arc's is 4, and they are not the same
    # requirement. A local model under a reading-time budget shortens a post
    # by deleting slides rather than by tightening sentences, and a two-slide
    # post is a cover and a sign-off with the content missing.
    floor = _LIM.get("slides_min", 4)
    if 2 <= len(slides) < floor:
        errs.append(f"only {len(slides)} slides — the arc needs at least "
                    f"{floor}; cut words, not slides")

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
    # Phrasing the owner ruled out (2026-08-25): "X es cuando..." is improper
    # as a definition, and "Es como cuando..." had become a formulaic tic.
    banned = ACCT.get("banned_phrases") or []
    if banned:
        blob = " ".join([cap] + [json.dumps(sl, ensure_ascii=False)
                                 for sl in slides]).lower()
        for phrase in banned:
            if phrase in blob:
                errs.append(f"uses banned phrasing {phrase!r} - define it directly, "
                            f"or write the comparison as a full sentence")

    # A source the reader cannot open is not a source.
    if ACCT.get("require_source_url"):
        urls = [u.rstrip('.,;:)"”’') for u in re.findall(r"https?://\S+", cap)]
        if "fuente oficial" not in cap.lower():
            errs.append("caption missing the 'Fuente oficial:' line")
        if not urls:
            errs.append("caption carries no source URL - the reader cannot reach "
                        "the official document")
        allowed = ACCT.get("official_domains") or []
        if urls and allowed:
            bad = [u for u in urls if not _official(u)]
            if bad:
                errs.append(f"source URL is not an official domain: {bad[0]}")
            shallow = [u for u in urls if u not in bad
                       and len(u.rstrip("/").split("/")) <= 3]
            if shallow:
                errs.append(f"source URL is a site home page, not the specific "
                            f"notice: {shallow[0]}")
        # The check the old prompt could only ask for. A local model composes
        # URLs that look exactly right — correct domain, plausible path — and
        # 404. search.RETRIEVED holds every URL a real search or page read
        # returned this run, so a caption citing anything else is citing
        # something the model made up.
        if search.RETRIEVED:
            invented = [u for u in urls if not _url_seen(u)]
            if invented:
                errs.append(f"source URL never came back from a search - the "
                            f"model invented it: {invented[0]}")
        else:
            errs.append("no search results were recorded this run, so no source "
                        "URL can be trusted")

    # Quoting law without explaining it leaves the reader no better informed.
    gloss = ACCT.get("legal_gloss_eyebrow")
    if gloss and any((sl.get("code") or "").strip() for sl in slides):
        eyes = [(sl.get("eyebrow") or "").upper() for sl in slides]
        if not any(gloss.upper() in e for e in eyes):
            errs.append(f"quotes official text but has no '{gloss}' slide "
                        f"explaining what it means")
        for sl in slides:
            if gloss.upper() in (sl.get("eyebrow") or "").upper():
                body = "".join(x.get("t", "") for x in (sl.get("body") or []))
                if len(body) < 150:
                    errs.append(f"'{gloss}' slide is only {len(body)} chars - too "
                                f"thin to actually explain the legal text")

    if _LIM.get("slides_max") and len(slides) > _LIM["slides_max"]:
        errs.append(f"{len(slides)} slides, but this account's Reels must fit "
                    f"{ACCT.get('reel_target_seconds', 30)}s — max "
                    f"{_LIM['slides_max']}")

    # Reading time, not pixels, is what caps a Reel. Budget the whole post.
    target = ACCT.get("reel_target_seconds")
    if target:
        total = 0
        for sl in slides:
            for key in ("eyebrow", "sub", "stat", "label"):
                total += len(sl.get(key) or "")
            total += len(hooks.flatten(sl.get("headline")))
            total += len(hooks.flatten(sl.get("body")))
            total += sum(len(hooks.flatten(x)) for x in (sl.get("items") or []))
        budget = int(target * 11 * 0.95)   # ~11 chars/sec readable pace
        # The 5% margin is what the WRITER aims for (it is in the message
        # below, and _tighten trims toward it). It is not the rejection line:
        # on 2026-09-23 a post about a court order lifting asylum holds was
        # thrown away at 633 characters against 627 - "needs about 58s" for a
        # 60-second target. A post that reads inside its target ships.
        if total > int(target * 11):
            # Say how much to cut. "502 chars against a 470 budget" leaves the
            # model to do the subtraction and it reliably overshoots the other
            # way; "cut at least 32 characters" it can act on.
            errs.append(f"{total} chars of slide text needs about "
                        f"{total / 11:.0f}s to read; this account's Reels must "
                        f"land near {target}s (budget {budget} chars) — cut at "
                        f"least {total - budget} characters of slide text")

    for i, s in enumerate(slides, 1):
        hl = s.get("headline")
        flat = hl if isinstance(hl, str) else "".join(x.get("t", "") for x in hl or [])
        if len(flat) > max(60, _LIM["headline"]):
            errs.append(f"slide {i}: headline {len(flat)} chars, will overflow")
        # The per-field caps only stop one slide eating the post; the TOTAL
        # reading-time budget above is what keeps the Reel short, and it stays
        # exact. So a field may run a few characters over its own cap: a post
        # was thrown away on 2026-09-23 for a body of 121 against 120, and the
        # model cannot count closely enough to land on the character.
        for key, cap in (("sub", _LIM["sub"]), ("stat", _LIM["stat"])):
            if len(s.get(key) or "") > _slack(cap):
                errs.append(f"slide {i}: {key} {len(s[key])} chars > {cap}, "
                            f"cut {len(s[key]) - cap}")
        body_len = len(hooks.flatten(s.get("body")))
        # When the whole post reads inside its target, one body may run up to
        # a quarter over: "one slide eats the post" is exactly what the total
        # budget measures. A court-order post was thrown away unchecked on
        # 2026-09-23 for a body of 128 against 120, inside its budget. The
        # canvas has rendered bodies past 300 characters.
        in_budget = bool(target) and total <= int(target * 11)
        body_cap = _LIM["body"] * 5 // 4 if in_budget else _slack(_LIM["body"])
        if body_len > body_cap:
            errs.append(f"slide {i}: body {body_len} chars > {_LIM['body']}, "
                        f"cut {body_len - _LIM['body']}")
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
    ap.add_argument("--no-hook-test", action="store_true",
                    help="skip blind hook grading; ship the authored cover")
    ap.add_argument("--min-hook-score", type=float, default=hooks.MIN_SCORE,
                    help=f"retry a post's hooks below this "
                         f"(default {hooks.MIN_SCORE})")
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
    # "all" = every post is a Reel. Measured on leverageai over 41 posts
    # (2026-09-07): carousels reached 1-2 accounts each, Reels 6-108 (median
    # ~40). A carousel slot on an account with no audience yet is a wasted
    # publishing day, so accounts still in discovery run Reels only.
    ratio = ACCT.get("reel_ratio", 1)
    all_reels = str(ratio).lower() == "all"
    period = 1 if all_reels else int(ratio) + 1

    def is_reel(slug):
        if all_reels:
            return True
        m = re.match(r"post(\d+)", slug)
        n = int(m.group(1)) if m else 0
        return n % period != 0

    # The hook test runs before validation, because it rewrites the cover
    # headline that validation measures. On a dry run its log lands beside the
    # rendered slides, like the strategy file above.
    hook_log = None if live else os.path.join(out, "hooks.json")

    # Never let a reporting-only check throw away work the API was already
    # paid for: a crash here on 2026-09-03..09-07 discarded five authored
    # batches (7 posts each) and drained inmigraforma's queue to one post.
    try:
        dupes = repeated_openers(posts)
    except Exception as e:
        print(f"::warning::opener check failed ({e}) — batch kept regardless")
        dupes = {}
    for opener, slugs in dupes.items():
        print(f"::warning::comparison opener {opener!r} reused in "
              f"{len(slugs)} posts ({', '.join(s for s in slugs if s)}) — "
              f"a repeated formula is the tic the owner asked us to drop")

    for post in posts:
        if not a.no_hook_test:
            import copy
            authored = copy.deepcopy(post["slides"][0])
            print(hook_test(post, a.min_hook_score, log_path=hook_log))
            # The winning candidate replaces the cover AFTER every check ran.
            # The cover is the most-read line; check it, and fall back to the
            # authored cover (which passed) rather than lose the post.
            if post["slides"][0] != authored and post.get("source_url"):
                probe = {"slides": [post["slides"][0]]}
                cover_errs, _ = factcheck.verify_detail(
                    probe, {"source_url": post["source_url"]}, ACCT,
                    model=MODEL, label=f"{post['slug']}/cover")
                cover_errs = [e for e in cover_errs if not e.startswith("only ")]
                if cover_errs or validate(post):
                    print(f"  {post['slug']}: graded cover fails a check - "
                          f"keeping the authored cover ({(cover_errs or validate(post))[0][:90]})")
                    post["slides"][0] = authored

        errs = validate(post)
        if errs:
            print(f"REJECTED {post.get('slug')}: {'; '.join(errs)}")
            continue

        post.pop("_sentences", None)
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

    # Only scheduled posts hold a slot. Retired ones keep their original date
    # as a record of when they would have run, and must not collide with the
    # posts that replaced them.
    dates = [p["date"] for p in sched["posts"]
             if p.get("status") in ("queued", "published")]
    assert len(set(dates)) == len(dates), "duplicate dates in queue"
    print(f"\nqueued {added} posts through {max(dates)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
