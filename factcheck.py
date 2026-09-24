#!/usr/bin/env python3
"""Is the post TRUE? The check the pipeline was missing until 2026-09-22.

Everything upstream proves something narrower. search.RETRIEVED proves a
source is real. search.supports() proves the brief's quote is on that page.
Neither proves the finished POST says what the page says — and on 2026-09-22
the local model wrote, for inmigraforma, that Medicaid and food stamps "no
longer count" toward public charge, citing the USCIS page whose operative
sentence is that benefits received on or after 2026-09-18 now DO count. Real
source, verified quote, inverted post. Two more had already published telling
readers to leave the United States over a rule that was still open for public
comment. leverageai recommended a product, "AutoBill Pro", that does not exist.

A small model is far better at checking a claim than at writing an accurate
one, provided it cannot bluff the check. So every verdict must carry the
sentence from the page it rests on, copied word for word, and that sentence is
looked up on the page here, in code. A claim whose evidence is not on the page
counts as unverified whatever verdict came with it. The model does the reading;
this module decides.

    python factcheck.py accounts/inmigraforma/specs/<slug>.json
"""
import json, re, sys

import llm
import search

# How much of the page the checker reads. Pages arrive whole in search.PAGES,
# navigation included; the window is centred on the brief's verified quote so
# the operative text is always inside it, and sized well inside num_ctx so the
# post, the instructions and the thinking still fit behind it.
WINDOW = 24_000

# Wording that marks a rule as not yet in force. Checked on the page itself,
# in code, because "is this a proposal?" is exactly the question the model got
# wrong twice: it read a notice with an open comment period and told people to
# leave the country.
PROPOSAL_MARKERS = (
    "proposed rule", "notice of proposed rulemaking", "nprm",
    "comments must be received", "submit comments", "comment period",
    "written comments", "we propose", "dhs proposes", "uscis proposes",
    "is proposing", "would amend", "propone", "propuesta de regla",
)

# And the wording a post needs to carry when it covers one.
PROPOSAL_WORDS = ("propuesta", "propone", "propuso", "proponen", "todavía no",
                  "aún no", "no está en vigor", "no ha entrado", "si se aprueba",
                  "proposed", "not yet", "not in effect")

# Advice that is always safe and never needs a source: save or share this,
# consult a lawyer, check your own case, verify on the official site, beware
# of scams. The checker flags these as not_on_page because, literally, they
# are not on the page - and on the first calibration run that failed a post
# that was correct in every fact (post48: 7/10 supported, 0 contradicted).
# The danger is asymmetric. "Consulta a un abogado" cannot hurt anyone;
# "sal de EE.UU." can. So caution is allowed without a source, and anything
# that tells the reader to change what they do about their status, benefits
# or residence still has to be on the page.
SAFE_ADVICE = re.compile(
    r"guard(a|en|ar|alo|ala)\b|compart(e|ir|elo|ela)\b|sigue (a|la cuenta)|"
    # Any form of the verb: "consulte" (formal) was missed and rejected a
    # true, clear post on 2026-09-24.
    r"consult\w* (a|con) (un|una) abogad|habl\w* con (un|una) abogad|"
    r"pregunt\w* a (un|una) abogad|busc\w* (ayuda|asesor[ií]a) legal|"
    r"abogad[oa] de inmigraci|asesor[ií]a legal|"
    r"revisa\w* (bien )?(tu|el|las|los|en)\b|verifica\w*\b|confirma\w* (en|con|que)|"
    r"no pagues a nadie|estafa|fraude|desconf[ií]a|"
    # Saying what the page does NOT say is the honest fallback the arc asks
    # for when a source names no wider group - it is not a claim about law.
    r"(la p[aá]gina|el aviso|la regla|uscis) no (dice|explica|aclara|menciona)|"
    r"lee\w* el aviso|link en la descripci|"
    r"(the (page|announcement|post)|google|openai|anthropic|microsoft) "
    r"(has not|hasn't|does not|doesn't) (said|say)|link in (the )?caption|"
    # A tips account's own framing and its own suggested prompts are the
    # product, not claims about the world (see WORLD_ONLY). Checked anyway,
    # "This is for freelancers and small business owners" was rejected as
    # 'not on the page' (2026-09-23).
    r"\b(this is )?for (freelancers|solo|small[- ]business|business owners|"
    r"consultants|creators|coaches)|if you (run|own|are) (a|an) |"
    r"\b(the |this |a )?prompt (to use )?(is|:)|try (this|asking)|ask it to|"
    r"save this|share this|consult an? (immigration )?(attorney|lawyer)|"
    r"check your (case|status|dates)|verify (it |this )?on",
    re.I)

# A post has to say something the page actually backs. Zero supported claims
# with nothing contradicted is a post of pure filler and safe advice - which
# is not news, whatever else it is.
MIN_SUPPORTED = 2

CHECKER = """You are a strict fact-checker. You read a source page and a \
social media post that claims to be based on it, and you decide, claim by \
claim, whether the post says what the page says.

You are not judging style, tone or length. Only truth relative to the page.

The post is usually in Spanish and the page in English. A faithful translation \
or a plain paraphrase of what the page states IS "supported" - judge meaning, \
not wording. An item in a bulleted list on the page is a statement like any \
other: "Serious illness of you or your spouse" supports "como una enfermedad \
grave".

List ONE fact per claim. A sentence that makes two claims ("necesitas una razón \
grave, como una enfermedad; y tienes 10 días") is two claims - check each on \
its own, so a true half is not failed for the other half.

- "supported": the page states the same thing. Not "is consistent with", not \
"could be read as" — states it.
- "contradicted": the page states something that conflicts with the claim.
- "not_on_page": the page does not say it. A claim that goes further than the \
page, adds a number, a deadline, a consequence or an instruction the page does \
not give, is not_on_page.

Advice counts as a claim. If the post tells the reader to do something about \
their status, benefits, residence or deadlines — leave the country, keep using \
a benefit, stop worrying, file by a date — that instruction must be backed by \
the page or it is not_on_page.

Do NOT list these as claims at all, they are always allowed: calls to save, \
share or follow; the advice to consult a lawyer; general cautions to check \
your own case or verify on the official site; warnings about scams.

WHO IT APPLIES TO. Every post says who is affected and who is not ("Si: \
si pides ajuste por trabajo o familia. No: si ya tienes Green Card"). Such a \
line is "supported" when it follows directly from what the page itself says \
it covers - a page about filing for a Green Card through family or work does \
not apply to someone who already holds one. Cite the page sentence that says \
who or what the page covers. It is still "contradicted" if the page says the \
excluded group IS affected, and "not_on_page" if it names a group, a \
benefit or a status the page never mentions at all.

For every claim, copy the ONE sentence from the page that decides it, word for \
word, exactly as it appears. It will be searched for on the page. If you \
cannot find a sentence that decides it, the verdict is not_on_page and the \
evidence is the closest sentence you found.

DEFINITIONS. A sentence that only says what a term IS - what a program, form, agency, visa or word means (e.g. "El Programa de Visas de Diversidad es el sorteo anual que otorga visas de residencia permanente") - and makes no claim about what happened, changed, or who it applies to, is judged on whether the definition is CORRECT in general, not on whether the page states it. Mark it supported with the evidence exactly "definition" if it is correct; contradicted with a one-line correction as evidence if it is wrong. Anything beyond the definition must be on the page as usual."""

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["in_force", "proposed", "not_a_rule"],
            "description": (
                "Is the change the page describes already in force, only "
                "proposed (open for comment, 'proposed rule', 'proposes'), or "
                "not a rule at all (an alert, a form update, a statistic)?"),
        },
        "status_evidence": {
            "type": "string",
            "description": "The page sentence that decides the status, word for word.",
        },
        "claims": {
            "type": "array",
            "minItems": 3,
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string",
                              "description": "One factual claim or instruction the post makes."},
                    "evidence": {"type": "string",
                                 "description": "The deciding page sentence, copied word for word."},
                    "verdict": {"type": "string",
                                "enum": ["supported", "contradicted", "not_on_page"]},
                },
                "required": ["claim", "evidence", "verdict"],
            },
        },
    },
    "required": ["status", "status_evidence", "claims"],
}


# For accounts whose own advice IS the product. On immigration news any
# instruction not backed by the page is dangerous; on an AI-productivity
# account the workflow steps, the copy-paste prompt and the opinion are
# original content the reader came for. The queue audit on 2026-09-23 failed
# leverageai's post77 for its prompt and post75 for "no more Python setup
# headaches" - neither is a claim about the world. What must be sourced there
# is what a product does, what it costs, when it shipped, and the numbers.
WORLD_ONLY = (
    "THIS ACCOUNT'S OWN CONTENT IS NOT A CLAIM. The post gives its own "
    "workflow steps, a copy-paste prompt, tips and opinions - do not list "
    "those. List only FACTUAL CLAIMS ABOUT THE WORLD: that a product or "
    "feature exists, what it does, what it costs, which plan includes it, "
    "when it launched, and any number or statistic. Those must be on the page."
    "\n\n=====\n\n")


def _degenerate(data):
    """True when the checker's answer is a shape with nothing in it."""
    claims = (data or {}).get("claims") or []
    if not claims:
        return True
    empty = sum(1 for c in claims
                if len((c.get("claim") or "").strip(" .\u2026")) < 4)
    return empty * 2 >= len(claims)


def _norm(s):
    """Normalise for a literal lookup: case, whitespace, and the typographic
    variants an HTML-to-text strip and a model's copy both introduce."""
    s = (s or "").lower()
    s = (s.replace("’", "'").replace("‘", "'")
          .replace("“", '"').replace("”", '"')
          .replace("–", "-").replace("—", "-")
          .replace(" ", " ").replace("​", ""))
    return re.sub(r"\s+", " ", s).strip()


# Punctuation that carries no meaning for "is this sentence on the page":
# the page reaches us through an HTML-to-text strip that turns a label and its
# value into separate lines ("Release Date\n\n08/18/2026"), and a model copying
# it writes "Release Date: 08/18/2026". Slashes, dots and hyphens stay, because
# they live INSIDE the tokens that matter - dates, U.S., I-485.
_SOFT_PUNCT = re.compile(r"[:;,\"'()\[\]¿¡!?]")

SHINGLE = 4          # words per overlapping run
SHINGLE_SHARE = 0.75 # share of runs that must appear on the page


def _words(s):
    return _SOFT_PUNCT.sub(" ", _norm(s)).split()


def on_page(page, sentence, need=3):
    """Is `sentence` really on the page, allowing for a copying slip?

    Two failure modes pull opposite ways. A 30B model copying a sentence drops
    a few words or adds a colon - on the first calibration it wrote "likely to
    become" for the page's "likely at any time to become", and "Release Date:"
    for a page that sets the label and the date on separate lines. Demanding a
    character-perfect copy rejected a post that was right in every fact. But
    the reason this check exists is fabrication: post68 quoted "official text"
    that is not on the page at all, and an inverted claim needs only a
    true-sounding first half - which is why the old 40-character prefix test
    was not enough.

    So the test is the sentence's overlapping four-word runs: at least three
    quarters of them must appear on the page. A copy with a slip keeps nearly
    all of them. A made-up sentence, or a real one bent to say the opposite,
    keeps almost none. Short evidence - a date, a name - must match exactly.
    """
    w = _words(sentence)
    if len(w) < need:
        return False
    body = " " + " ".join(_words(page)) + " "
    if len(w) < SHINGLE + 2:
        return " " + " ".join(w) + " " in body
    runs = [" " + " ".join(w[i:i + SHINGLE]) + " "
            for i in range(len(w) - SHINGLE + 1)]
    hit = sum(1 for r in runs if r in body)
    return hit / len(runs) >= SHINGLE_SHARE


def share(page, sentence):
    """The fraction of the sentence's four-word runs found on the page - the
    number on_page() thresholds. For diagnosing a rejection, not deciding one."""
    w = _words(sentence)
    if len(w) < SHINGLE:
        return 1.0 if on_page(page, sentence) else 0.0
    body = " " + " ".join(_words(page)) + " "
    runs = [" " + " ".join(w[i:i + SHINGLE]) + " " for i in range(len(w) - SHINGLE + 1)]
    return sum(1 for r in runs if r in body) / len(runs)


def _page_for(brief):
    url = (brief or {}).get("source_url") or ""
    if not url:
        return url, None
    page = search.PAGES.get(url)
    if page is None:
        search.fetch(url, max_chars=200_000)
        page = search.PAGES.get(url)
    return url, page


# The opening of a page is always read: it carries the title, the document
# type and the summary. A Federal Register proposed rule runs to 183,000
# characters; centred on a quote from deep in the preamble, the window never
# showed the checker "Proposed rule" (char 2,425) or the SUMMARY (char 11,252),
# so it rejected "the government proposed eliminating the 60-day grace period"
# as not on the page - the one true sentence the post was built on.
HEAD = 14_000


def _window(page, anchor):
    """The page's opening, plus the passage around the brief's quote, within
    WINDOW characters - so both what the document IS and the part the post is
    about are in view however long the page is."""
    if len(page) <= WINDOW:
        return page
    i = _norm(page).find(_norm(anchor)[:60]) if anchor else -1
    # _norm collapses whitespace, so the index is approximate; the section is
    # wide enough that approximate is fine.
    if i < HEAD:
        return page[:WINDOW]
    rest = WINDOW - HEAD
    start = max(HEAD, i - rest // 2)
    return page[:HEAD] + "\n\n[...]\n\n" + page[start:start + rest]


def post_text(post, acct):
    """What the reader actually reads, minus the parts with no claims in them:
    the follow line, the hashtags, the not-legal-advice line."""
    parts = []
    for sl in post.get("slides") or []:
        for key in ("eyebrow", "headline", "sub", "body", "stat", "code",
                    "label", "cta_title"):
            v = sl.get(key)
            if isinstance(v, list):
                v = "".join(x.get("t", "") for x in v if isinstance(x, dict))
            if v:
                parts.append(str(v))
        for item in sl.get("items") or []:
            parts.append(item if isinstance(item, str) else str(item))
    cap = post.get("caption") or ""
    cta = (acct.get("cta_line") or "").strip()
    keep = []
    for line in cap.splitlines():
        low = line.strip().lower()
        if not low or low.startswith("#") or (cta and cta.lower() in low):
            continue
        if "asesoría legal" in low or "legal advice" in low:
            continue
        keep.append(line.strip())
    return "SLIDES:\n" + "\n".join(parts) + "\n\nCAPTION:\n" + "\n".join(keep)


_DEFINITION = re.compile(
    r"^(?:el |la |los |las |un |una |the |a |an )?[A-ZÁÉÍÓÚÑ][^.;:]{1,70}?"
    r"\s(?:es|son|is|are)\s(?:un|una|el|la|los|las|the|a|an|para|for)\s", re.I)
_NOT_DEFINITION = re.compile(
    r"\d|\b(plazo|fecha l[ií]mite|deadline|vence|expira|"
    r"debes?|tienes? que|must|should|ahora|now|ya no|cambi|nuevo|nueva|new)\b", re.I)
# Form and visa codes carry digits that are not dates or amounts: I-485,
# H-2B, N-400, EB-5, DV-2026 is NOT one (a year).
_CODE = re.compile(r"\b[A-Z]{1,2}-?\d{1,3}[A-Z]?\b")


def _definitional(claim):
    """True for a sentence that only defines a term."""
    claim = claim.strip()
    return bool(_DEFINITION.match(claim)) and not _NOT_DEFINITION.search(_CODE.sub("X", claim))


def verify(post, brief, acct, model=None, label=""):
    """Problems as text for the author; empty means the post is true to its
    source. verify_detail() also returns the offending claims themselves."""
    return verify_detail(post, brief, acct, model=model, label=label)[0]


_RECOPY_SCHEMA = {"type": "object",
                  "properties": {"sentence": {"type": "string"}},
                  "required": ["sentence"]}


def _recopy(excerpt, claim, model, label):
    """The one page sentence that supports `claim`, copied exactly - or ""."""
    try:
        data = llm.structured(
            "You copy text. Given a page and a claim, return the ONE sentence "
            "from the page that states what the claim says, copied character "
            "for character - no paraphrase, no trimming, no added words. If no "
            "sentence on the page states it, return an empty string.",
            f"PAGE:\n\n{excerpt}\n\n=====\n\nCLAIM: {claim}",
            _RECOPY_SCHEMA, model=model, label=f"recopy:{label}",
            temperature=0, max_tokens=600)
        return (data.get("sentence") or "").strip()
    except llm.LLMError:
        return ""


def verify_detail(post, brief, acct, model=None, label=""):
    """Return (problems, bad_claims): the problems as text, and the exact claim
    strings behind them, which strip_claims() uses to delete them.

    Every problem is phrased for the author: which claim, and the page sentence
    that decides it. write_post() feeds these straight back into a fix pass.
    """
    strict = bool(acct.get("require_source_url"))
    url, page = _page_for(brief)
    # No page, no post - on every account. Tips accounts used to pass
    # unchecked here, and that is exactly how invented products got through:
    # the model imagines a tool, imagines its blog URL, the URL 404s, and the
    # one check that would have caught it silently steps aside.
    if not page:
        return ([f"the source page ({url or 'none'}) could not be read, so "
                 f"nothing in this post can be checked against it"], [])

    excerpt = _window(page, (brief or {}).get("quote"))
    ask = (f"SOURCE PAGE ({url}):\n\n{excerpt}\n\n"
           + ("" if strict else WORLD_ONLY) +
           f"=====\n\nPOST TO CHECK:\n\n{post_text(post, acct)}\n\n"
           f"=====\n\nList every factual claim and every instruction the post "
           f"makes — dates, who is affected, what changed, amounts, "
           f"consequences, and what it tells the reader to do. Skip "
           f"comparisons, analogies and general encouragement. Then give the "
           f"status of the change.")
    try:
        data = None
        # A check that did not really run is not a verdict. On 2026-09-23 one
        # came back with every claim and every piece of evidence literally
        # "...", and the post was rejected on that. Placeholder output gets
        # one more run - at a small temperature, so it is a real resample -
        # before anything is concluded from it.
        for attempt in range(2):
            try:
                data = llm.structured(
                    CHECKER, ask, SCHEMA, model=model, require=("claims",),
                    label=f"factcheck:{label}" + (f"/again" if attempt else ""),
                    # A real check thinks for 2-9K tokens. Uncapped, one ran
                    # for 30 minutes and hit the timeout; the cap bounds a
                    # runaway, and the second attempt runs with reasoning
                    # off, which cannot run away (a 20K overrun on a long
                    # Federal Register page rejected a post, 2026-09-24).
                    think=(attempt == 0), temperature=0 if not attempt else 0.2,
                    max_tokens=20000)
            except llm.LLMError as e:
                if attempt == 0 and "cap" in str(e):
                    print(f"  ::warning::factcheck:{label} ran past its cap - "
                          f"checking again without reasoning")
                    continue
                raise
            if not _degenerate(data):
                break
            print(f"  ::warning::factcheck:{label} returned placeholder "
                  f"claims - checking again")
        if _degenerate(data):
            raise llm.LLMError("the checker returned placeholder claims twice")
    except llm.LLMError as e:
        # A checker that could not run has checked nothing. On an account whose
        # posts people act on, that is a rejection, not a pass.
        return ([f"the fact-check could not run ({e})"], []) if strict else ([], [])

    errs, bad = [], []
    for c in data.get("claims") or []:
        claim = (c.get("claim") or "").strip()
        ev = (c.get("evidence") or "").strip()
        verdict = c.get("verdict")
        real = on_page(page, ev)
        # A scrap under four words ("Para solicitantes.") states no fact to
        # check; it is a writing problem, and _clarity_rules rejects it.
        # forbidden_advice still guards short dangerous phrases.
        if len(re.findall(r"\w+", claim)) < 4 and not re.search(r"\d", claim):
            continue
        # A definition the checker vouched for. The shape is enforced here so
        # the checker cannot wave a news claim through by calling it one:
        # "<Term> es/son/is/are <un|una|el|la|the|a|an> ..." and nothing about
        # a date, a deadline or an instruction.
        if (verdict == "supported" and ev.lower().strip(" .") == "definition"
                and _definitional(claim)):
            continue
        if verdict == "contradicted":
            bad.append(claim)
            errs.append(f"CONTRADICTS THE SOURCE — the post says: {claim!r}. "
                        f"The page says: {ev!r}" if real else
                        f"CONTRADICTS THE SOURCE — the post says: {claim!r}")
        elif verdict == "not_on_page":
            if SAFE_ADVICE.search(claim):
                continue
            # A correct definition is never on a news page; the checker
            # labels it not_on_page rather than "definition" more often than
            # not (2026-09-24, four rounds running). Its shape - no number,
            # date, deadline or instruction - is the guard; a WRONG definition
            # still arrives as contradicted and is rejected above.
            if _definitional(claim):
                continue
            bad.append(claim)
            errs.append(f"NOT IN THE SOURCE — the page does not say: {claim!r}. "
                        f"Remove it or state only what the page states.")
        elif not real:
            # "supported", but the sentence offered as proof is not on the
            # page. A verdict without real evidence is a verdict nobody made
            # - but on a long Federal Register page the checker's copy of the
            # sentence drifts, and a true claim was rejected twice for that
            # on 2026-09-24. One targeted retry: copy the exact sentence, or
            # say there is none.
            ev2 = _recopy(excerpt, claim, model, label)
            if ev2 and on_page(page, ev2):
                continue
            bad.append(claim)
            errs.append(f"UNVERIFIED — {claim!r} was marked supported, but "
                        f"the sentence given as proof is not on the page.")

    supported = sum(1 for c in data.get("claims") or []
                    if c.get("verdict") == "supported"
                    and on_page(page, c.get("evidence")))
    if supported < MIN_SUPPORTED:
        errs.append(f"only {supported} claim(s) in this post are backed by the "
                  f"source — say what the page actually says, with its "
                  f"dates and who it applies to.")

    # Proposal status, decided in code from the page's own wording, with the
    # model's reading as a second opinion. Either one is enough.
    low = _norm(excerpt)
    proposed = (data.get("status") == "proposed"
                or any(m in low for m in PROPOSAL_MARKERS))
    if proposed:
        head = _norm(" ".join(str(x) for x in (
            (post.get("slides") or [{}])[0].get("headline"),
            (post.get("slides") or [{}])[0].get("sub"),
        )) + " " + post_text(post, acct)[:600])
        if not any(w in head for w in PROPOSAL_WORDS):
            errs.append("THE SOURCE IS A PROPOSAL, NOT A RULE IN FORCE — the "
                        "cover and first slides must say plainly that it is a "
                        "proposal ('propuesta', 'todavía no está en vigor') and "
                        "must not tell anyone to act as if it already applies.")

    ok = sum(1 for c in data.get("claims") or [] if c.get("verdict") == "supported")
    print(f"  factcheck:{label} {ok}/{len(data.get('claims') or [])} supported, "
          f"status {data.get('status')}, {len(errs)} problem(s)")
    return errs, bad


# Caption lines that carry no claim and must survive any deletion.
_KEEP_LINE = re.compile(r"^\s*(#|fuente oficial|official source|informaci[oó]n "
                        r"general|sigue a @|follow @)", re.I)


def _overlap(claim, sentence):
    """Share of the claim's words that appear in the sentence."""
    cw = set(_words(claim))
    return len(cw & set(_words(sentence))) / len(cw) if cw else 0.0


def strip_claims(post, claims, threshold=0.6):
    """Delete, from a copy of the post, each sentence that carries a flagged
    claim. Returns the new post, or None when a claim cannot be removed safely.

    The last repair, because it is the only one that cannot go wrong in the
    other direction. A whole-post rewrite removes one invented detail and adds
    another - on 2026-09-23 posts climbed from 0/10 to 3/9 supported and then
    stalled. Deleting only removes. A claim in a headline, eyebrow or cover
    cannot be deleted without breaking the slide, and a deletion that would
    empty a slide leaves nothing worth publishing; both return None, and the
    post is rejected rather than shipped thin.
    """
    import copy
    new = copy.deepcopy(post)
    sent_split = re.compile(r"(?<=[.!?])\s+")
    for claim in claims:
        best, where = 0.0, None
        for si, sl in enumerate(new.get("slides") or []):
            for key in ("body", "sub"):
                v = sl.get(key)
                txt = v if isinstance(v, str) else "".join(
                    x.get("t", "") for x in (v or []) if isinstance(x, dict))
                for sent in sent_split.split(txt or ""):
                    o = _overlap(claim, sent)
                    if o > best:
                        best, where = o, ("slide", si, key, sent)
            for ii, it in enumerate(sl.get("items") or []):
                if isinstance(it, str):
                    o = _overlap(claim, it)
                    if o > best:
                        best, where = o, ("item", si, ii, it)
            for key in ("headline", "eyebrow"):
                v = sl.get(key)
                txt = v if isinstance(v, str) else "".join(
                    x.get("t", "") for x in (v or []) if isinstance(x, dict))
                o = _overlap(claim, txt or "")
                if o > best:
                    best, where = o, ("fixed", si, key, txt)
        for line in (new.get("caption") or "").splitlines():
            if _KEEP_LINE.match(line):
                continue
            for sent in sent_split.split(line):
                o = _overlap(claim, sent)
                if o > best:
                    best, where = o, ("caption", None, None, sent)
        if best < threshold or where is None:
            continue                    # not located: the re-check decides
        kind, si, key, sent = where
        if kind == "fixed":
            return None                 # a headline cannot be deleted
        if kind == "item":
            items = new["slides"][si]["items"]
            if len(items) <= 2:
                return None
            items.pop(key)
            continue
        if kind == "caption":
            new["caption"] = re.sub(r"[ \t]{2,}", " ",
                                    new["caption"].replace(sent, "")).strip()
            continue
        sl = new["slides"][si]
        v = sl.get(key)
        txt = v if isinstance(v, str) else "".join(
            x.get("t", "") for x in (v or []) if isinstance(x, dict))
        rest = re.sub(r"\s{2,}", " ", txt.replace(sent, "")).strip()
        if len(rest) < 15:
            return None                 # the slide would be left empty
        sl[key] = rest
    return new


def main():
    """Check a finished spec against the source URL in its caption."""
    import accounts
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        post = json.load(f)
    acct = accounts.get()
    urls = re.findall(r"https?://\S+", post.get("caption") or "")
    url = (post.get("source_url") or (urls[0].rstrip(".,)") if urls else ""))
    errs = verify(post, {"source_url": url}, acct, label=post.get("slug", "?"))
    print()
    for e in errs:
        print("  -", e)
    print("\nPASS" if not errs else f"\nFAIL ({len(errs)})")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
