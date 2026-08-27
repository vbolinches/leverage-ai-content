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
import hooks
import render_slides

# Authoring model. Per-account override via "model" in account.json. The
# owner standardised both accounts on Sonnet 5 (2026-08-27). The generator's
# value is in careful source verification and honest strategy reasoning - if a
# batch's quality slips, this is the first knob to revisit.
DEFAULT_MODEL = "claude-sonnet-5"

# One account per invocation (ACCOUNT env / --account). Everything the
# generator reads and writes — queue, specs, strategy, brand voice — belongs
# to this account and no other.
ACCT = accounts.get()
render_slides.configure(ACCT)
QUEUE = ACCT.queue
SPEC_DIR = ACCT.spec_dir
MODEL = ACCT.get("model", DEFAULT_MODEL)

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

SCHEMA = """Return ONLY a JSON array of post objects. No prose, no markdown fence.

Each post:
{
  "slug": "postNN-short-kebab-topic",   // NN is provided to you
  "caption": "full Instagram caption",
  "hook_candidates": [ ...exactly 5 alternative covers... ],
  "slides": [ ...4 to 7 slide objects... ]
}

hook_candidates are five OTHER ways to open the same post — {"headline":...,"sub":...},
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
  headline <= 40 chars   sub <= 90 chars   body <= 260 chars
  code <= 9 lines, each <= 46 chars       stat <= 22 chars
  items: 3-4, each <= 44 chars            eyebrow <= 16 chars"""

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
SCHEMA = (SCHEMA.replace("__CTA_SUB__", ACCT["cta_line"].rstrip("."))
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


# Forcing a tool call guarantees well-formed JSON. Parsing free text failed on
# literal newlines inside the `code` field, which are invalid inside a JSON string.
RICH_TEXT = hooks.RICH_TEXT

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
                        "hook_candidates": {
                            "type": "array",
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


_CLIENT = None


def client():
    """One Anthropic client for the run — authoring and hook grading share it."""
    global _CLIENT
    if _CLIENT is None:
        try:
            import anthropic
        except ImportError:
            sys.exit("pip install anthropic")
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            sys.exit("ANTHROPIC_API_KEY is not set — cannot author a batch.")
        _CLIENT = anthropic.Anthropic(api_key=key)
    return _CLIENT


def author(count, start_index, avoid):
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
        + "\n\n" + hooks.ban_list(ACCT)
        + "\n\n" + strategy_context()
    )

    messages = [{"role": "user", "content": prompt}]

    # tool_choice stays "auto": forcing submit_posts would stop the model
    # searching first. Server-side search can hit its own iteration limit and
    # return stop_reason "pause_turn" — resend to resume, bounded.
    # Server-side refusal fallback is documented for the Opus/Fable tier only.
    # Haiku rejects the parameter outright (400 "does not support the
    # `fallbacks` parameter"), and it is not documented for Sonnet - so send it
    # to the models known to take it rather than to everything-but-Haiku.
    extra = {}
    if MODEL.startswith(("claude-opus", "claude-fable", "claude-mythos")):
        extra = {"betas": ["server-side-fallback-2026-07-01"],
                 "fallbacks": "default"}

    # Both tools are direct-call-only. This is not just a Haiku workaround
    # (Haiku has no programmatic tool calling at all): we read the batch out of
    # submit_posts in the FINAL message, so a call the model makes from inside
    # code execution is invisible to us — and leaves a pending tool use that
    # the next request cannot resume without a container id (Sonnet 5 did
    # exactly that on 2026-08-27, 400 "container_id is required").
    search_tool = dict(WEB_SEARCH_TOOL, allowed_callers=["direct"])
    submit_tool = dict(SUBMIT_TOOL, allowed_callers=["direct"])

    for _ in range(6):
        with client().beta.messages.stream(
            model=MODEL,
            # 128K is the streaming ceiling on Sonnet 5 / Opus 5. Sonnet
            # needs the headroom: a 7-post inmigraforma batch (8 slides
            # plus 5 hook candidates each) overran 64K on 2026-08-27.
            max_tokens=128000,
            system=BRAND,
            tools=[search_tool, submit_tool],
            messages=messages,
            **extra,
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
    # Candidates must meet the stated headline limit; the authored cover stays
    # in the pool whatever its length, because it is also the fallback and
    # validate() is what judges it.
    for c in post.get("hook_candidates") or []:
        if len(hooks.flatten(c.get("headline"))) > hooks.MAX_HEADLINE:
            continue
        pool.append({"headline": c["headline"], "sub": c.get("sub", ""),
                     "authored": False})

    if len(pool) < 2:
        return "  (no usable alternatives — hook test skipped)"

    try:
        scored = hooks.grade(client(), ACCT, post["slug"], pool)
        retried = False

        # "Be blunt, a 6 out of 10 hook is a wasted video." One targeted retry:
        # fresh hooks join the pool rather than replace it, so a retry can lose.
        if scored[0]["score"] < min_score:
            extras = [c for c in hooks.retry(client(), ACCT, BRAND, post, scored)
                      if len(hooks.flatten(c.get("headline"))) <= hooks.MAX_HEADLINE]
            if extras:
                scored = hooks.grade(client(), ACCT, post["slug"], pool + extras)
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
            body = "".join(x.get("t", "") for x in (sl.get("body") or []))
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
            def _host(u):
                h = re.sub(r"^https?://", "", u).split("/")[0].lower()
                return re.sub(r"^www\.", "", h)
            bad = [u for u in urls
                   if not any(_host(u) == d or _host(u).endswith("." + d)
                              for d in allowed)]
            if bad:
                errs.append(f"source URL is not an official domain: {bad[0]}")
            shallow = [u for u in urls if u not in bad
                       and len(u.rstrip("/").split("/")) <= 3]
            if shallow:
                errs.append(f"source URL is a site home page, not the specific "
                            f"notice: {shallow[0]}")

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
    ratio = int(ACCT.get("reel_ratio", 1))
    period = ratio + 1

    def is_reel(slug):
        m = re.match(r"post(\d+)", slug)
        n = int(m.group(1)) if m else 0
        return n % period != 0

    # The hook test runs before validation, because it rewrites the cover
    # headline that validation measures. On a dry run its log lands beside the
    # rendered slides, like the strategy file above.
    hook_log = None if live else os.path.join(out, "hooks.json")

    dupes = repeated_openers(posts)
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
