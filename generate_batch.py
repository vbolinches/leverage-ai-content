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
__REQUIRED_SLIDE__  {"kind":"step","eyebrow":"STEP 1","headline":"Short imperative.","body":[{"t":"explanation "},{"t":"key point.","c":"green","b":true}]}
  {"kind":"prompt","eyebrow":"STEP 2","headline":"Short.","sub":"one line","label":"COPY THIS PROMPT","code":"literal prompt\\nwith newlines"}
  {"kind":"stat","eyebrow":"THE PAYOFF","headline":"Framing question:","stat":"~big phrase"}
  {"kind":"recap","eyebrow":"RECAP","headline":"The system","items":["step","step","step"],"cta_title":"Save this for later","cta_sub":"__CTA_SUB__","footer_right":"SAVE THIS ↓"}

Hard limits (text overflows the canvas otherwise):
__LIMITS__"""

# The schema examples must show this account's CTA and eyebrow series, not
# placeholders — the model copies examples far more reliably than instructions.
_REQ = ACCT.get("required_eyebrow")
_REQ_LINE = ""
if _REQ:
    _REQ_LINE = (
        '  {"kind":"step","eyebrow":"' + _REQ + '","headline":"Qué significa esto.",'
        '"body":[{"t":"2-3 frases de todos los días, sin jerga. "},'
        '{"t":"Una comparación concreta de la vida diaria.","c":"green","b":true}]}'
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
    dates = [date.fromisoformat(p["date"]) for p in sched["posts"]]
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
                    "required": ["slug", "art", "caption", "slides",
                                 "hook_candidates"],
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
                    "facts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("Every concrete claim the post may use — "
                                        "dates, numbers, names — each one taken "
                                        "from the source above."),
                    },
                },
                "required": ["topic", "angle", "why_now", "source_url",
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


def _sweep(want, avoid, signals):
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
    import ideas
    signals = ideas.digest(ACCT)

    want = count + 2
    briefs, taken, calls = [], list(avoid), 0
    for sweep in range(4):
        if len(briefs) >= want:
            break
        # The ideas digest goes in once. It is large, it is the same every
        # sweep, and a later sweep needs the avoid list far more than leads.
        found, n = _sweep(want - len(briefs), taken, signals if not sweep else "")
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
            if ACCT.get("require_source_url"):
                if not _url_seen(url):
                    print(f"  dropped a brief citing a URL no search "
                          f"returned: {url}")
                    continue
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
            if any(b.get("topic", "").lower() == x.lower() for x in taken):
                continue
            briefs.append(b)
            taken.append(b.get("topic", ""))
        if not found:
            break

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
    return re.sub(r"([.!?])([A-ZÁÉÍÓÚÑ¿¡])", r"\1 \2", text or "")


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


def _shorten(text, cap):
    """Rewrite one string to fit, or return None. Meaning must survive.

    Asked for "at most 110 characters" the model returns 112, every time, for
    the same reason the whole-post repair plateaued: it cannot count what it
    is writing. So it is asked for a fraction of the limit and judged against
    the real one — the overshoot that made the strict target fail is what
    makes a margin land.

    One margin was not enough either. Dense Spanish legal sentences came back
    at 115, 131 and 139 against a 110 limit while the instruction said to keep
    every fact; the model was obeying, and the text would not compress that
    far without losing something. So each attempt asks for less and says more
    plainly what may go — and names what may not, because dropping WHO a rule
    applies to turns a true sentence into a false one.
    """
    protect = (
        "Never drop a date, a number, a form name, or WHO is affected. "
        "Never invent an abbreviation for an agency either — compressing "
        "'Oficina Ejecutiva de Revision de Inmigracion' to 'OEIR' produced an "
        "acronym that does not exist. Use the agency's real short name or "
        "write around it. Getting one of these wrong is worse than being "
        "too long."
    )
    plans = [
        (0.85, "Same facts, same meaning, just fewer words."),
        (0.75, "Cut adjectives, hedges and repetition. " + protect),
        (0.65, "You may drop the least important clause entirely — the slide "
               "carries the news, the caption carries the detail. " + protect),
        (0.55, "Reduce it to its single most important sentence. " + protect),
        (0.45, "One short clause only: what changed, and for whom. Nothing "
               "else — no background, no procedure, no consequences. " + protect),
    ]
    return _squeeze(text, cap, plans)


def _squeeze(text, cap, plans, rounds=5):
    """Run the plans, and if none fits, run them again on the best result.

    Returns the shortest rewrite it reached, WHICH MAY STILL BE TOO LONG. The
    caller keeps it if it is shorter than what it had; validate() is what
    decides whether the post ships. Returning None on a near miss was the
    expensive mistake here — twice. A 175-character sentence came back at
    149, failed the 110 test, and the 149 was discarded, so the slide stayed
    at 175 and the post died with it. Compression works in steps: feed the
    shorter version back in and it keeps giving, where one pass from the
    original never gets there.
    """
    original = text
    for _ in range(rounds):
        got = _one_pass(text, cap, plans)
        if got and len(got) <= cap:
            return got
        if not got or len(got) >= len(text):
            break
        text = got
    if text == original:
        return None
    print(f"  could not fit {cap} chars (best {len(text)}, keeping it anyway)")
    return text


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
                _TEXT_SCHEMA, model=MODEL, require=("text",),
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
    changed = False
    for s in post.get("slides") or []:
        for key, cap in fields:
            cur = hooks.flatten(s.get(key))
            if not cur or len(cur) <= cap:
                continue
            new = _shorten(cur, cap)
            # Take any shortening, even one that did not reach the limit. It
            # still helps the whole-post budget, and validate() remains the
            # thing that decides whether the post ships.
            if new and len(new) < len(cur):
                s[key] = new
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
            new = _shorten(cur, max(40, want))
            if not new:
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
    ask = (
        f"Write ONE post.\n\n"
        f"TOPIC: {brief.get('topic')}\n"
        f"ANGLE FOR THE READER: {brief.get('angle')}\n"
        f"WHY NOW: {brief.get('why_now')}\n"
        f"SOURCE: {brief.get('source_title') or ''} {brief.get('source_url')}\n"
        f"VERIFIED FACTS — use only these, invent nothing:\n{facts}\n\n"
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

    best = llm.structured(
        BRAND, ask, POST_SCHEMA,
        model=MODEL, require=("slides", "caption"),
        label=f"write:{slug_prefix}", temperature=0.8,
    )
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
                model=MODEL, require=("slides", "caption"),
                label=f"fix{rnd + 1}:{slug_prefix}", temperature=0.5,
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
        fresh = validate(candidate)
        if len(fresh) <= len(errs):
            best, errs = candidate, fresh
        else:
            break

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
    return best


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
    llm.ensure(MODEL)

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
        except llm.LLMError as e:
            print(f"::warning::post{n:02d} could not be written ({e}) — skipping "
                  f"this topic")
            continue
        # The model names its own slug and drifts from the prefix it was given;
        # the queue keys on post number, so the prefix is not negotiable.
        slug = str(post.get("slug") or "")
        if not slug.startswith(f"post{n:02d}"):
            topic = re.sub(r"[^a-z0-9]+", "-",
                           (brief.get("topic") or "post").lower()).strip("-")
            post["slug"] = f"post{n:02d}-{topic[:40].rstrip('-')}"
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
        if total > budget:
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
        for key, cap in (("sub", _LIM["sub"]), ("stat", _LIM["stat"])):
            if len(s.get(key) or "") > cap:
                errs.append(f"slide {i}: {key} {len(s[key])} chars > {cap}, "
                            f"cut {len(s[key]) - cap}")
        body_len = len(hooks.flatten(s.get("body")))
        if body_len > _LIM["body"]:
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
            print(hook_test(post, a.min_hook_score, log_path=hook_log))

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
