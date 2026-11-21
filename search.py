#!/usr/bin/env python3
"""Web search and page reading for the generator, free and keyless.

This replaces Anthropic's server-side `web_search` tool, which vanished with
the API on 2026-09-11. That tool ran inside the model; this one runs here, so
the generator's search loop is now something we can see, log and — the part
that matters — hold the model to.

RETRIEVED is the point of this module. A local model invents plausible URLs
far more readily than a hosted one did, and inmigraforma's whole promise is
that a reader can open the official document. So every URL this module hands
back is recorded, and generate_batch.validate() rejects any caption citing a
URL that never came back from a real search. The old prompt asked the model
not to invent sources; this makes inventing one fail the batch.

DuckDuckGo needs no key and no account, which is why it is here. It also rate
limits, so searches back off and retry rather than dying.

    python search.py "uscis fee increase 2026"
"""
import json, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

# Every URL a search returned or a page read succeeded on, this process.
# generate_batch reads it after authoring; nothing else writes to it.
RETRIEVED = set()

# The same URLs with the title they arrived under, for showing the model what
# it is allowed to cite. Titles are the searcher's, not ours, and are only
# ever displayed — never matched on.
TITLES = {}

# The text of every page actually READ this run, keyed by URL. This is what
# makes a citation checkable rather than merely real: the generator asks the
# model to quote the page it is citing and looks the quote up in here. A URL
# that was only ever seen in a result list is not in this dict, and cannot be
# cited as a source.
PAGES = {}

# Pages that already have a post, keyed by canonical() URL, valued by the post
# id. generate_batch.research() fills it before the first sweep. Until
# 2026-11-16 nothing here knew about them, so the model searched for, read and
# briefed the same four posted stories every sweep - on the 15th and 16th
# inmigraforma lost more than half its research rounds that way, and the pages
# it re-read are what filled the context window. A covered page is now never
# read into the conversation, and never shown as a search result.
COVERED = {}

_FR_DOC = re.compile(r"^/(?:documents/\d{4}/\d{2}/\d{2}/|d/|public-inspection/)"
                     r"(\d{4}-\d{4,6})(?:/|$)")
# The Government Publishing Office prints the same Federal Register documents
# at govinfo.gov: /content/pkg/FR-2026-11-02/html/2026-22320.htm (and /pdf/),
# or /app/details/FR-2026-11-02/2026-22320. Same document number, same key,
# so a posted rule found through its govinfo copy is still the posted rule.
_GOVINFO_FR = re.compile(r"^/(?:content/pkg|app/details)/FR-\d{4}-\d{2}-\d{2}/"
                         r"(?:(?:html|pdf)/)?(\d{4}-\d{4,6})(?:\.(?:htm|html|pdf))?$", re.I)


def canonical(url):
    """One key per page, however the URL is spelt.

    The covered check compared raw strings, so a trailing slash, an http://,
    a #fragment or a utm_ tag made a posted page look new. The Federal
    Register serves one document at /documents/<date>/<number>/<slug> and at
    /d/<number>; both are keyed by the document number.
    """
    url = (url or "").strip()
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parts.path) or "/"
    if host == "federalregister.gov":
        m = _FR_DOC.match(path + "/")
        if m:
            return f"federalregister.gov/d/{m.group(1)}"
    if host == "govinfo.gov":
        m = _GOVINFO_FR.match(path)
        if m:
            return f"federalregister.gov/d/{m.group(1)}"
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
         if not k.lower().startswith("utm_")])
    return host + path + (f"?{query}" if query else "")


# Where a URL really landed, for the ones that redirected: a covered page
# reached through a different address is still the covered page.
LANDED = {}


def covered_by(url):
    """The post id already built on this page, or None."""
    if not COVERED or not url:
        return None
    return (COVERED.get(canonical(url))
            or COVERED.get(canonical(LANDED.get(url, ""))) or None)


def supports(url, quote, need=40):
    """Does the page at `url` actually contain this sentence?

    The URL guard proves a source is real. It does not prove the source is
    about the claim, and the model will happily attach whichever official URL
    it has to hand: a post about a visa pause cited a Federal Register notice
    on a refugee questionnaire, and one about asylum holds cited the Visa
    Bulletin. Both URLs were genuine, allow-listed and retrieved. Neither
    discussed its topic. Quoting the page is the check that catches that.

    Whitespace and case are normalised because the page arrives through an
    HTML-to-text strip; the comparison is otherwise literal.
    """
    page = PAGES.get(url)
    if page is None:
        # The model cited a page it only saw in a result list. That is not a
        # reason to throw the topic away — we have the URL, so open it and
        # check properly. Seven of twelve briefs died on this in one run
        # before the fetch was added, and the guarantee is unchanged: the
        # quote still has to be on the page.
        fetch(url, max_chars=200_000)
        page = PAGES.get(url)
    if page is None:
        # Name the page: four briefs died on this on 2026-11-21 and the log
        # could not say whether the URL was invented or the site was down.
        return (f"the page could not be opened, so the citation cannot be "
                f"checked: {url[:100]}")
    if len(re.sub(r"\s+", " ", quote or "").strip()) < need:
        return f"the quote is too short to check ({len((quote or '').strip())} chars)"
    # This check answers ONE question: is the page about this topic? It is
    # not the truth check - factcheck.verify() is, on the finished post, claim
    # by claim, with evidence looked up in code. On 2026-09-23 this was made
    # as strict as that one and research starved: nine topics dropped in one
    # run, TPS, EAD, a scam alert, asylum and an I-485 fee among them, because
    # a 30B model copying a sentence at the research stage drifts after the
    # first clause. The opening of the quote on the page is enough to prove
    # the page discusses the topic; whatever the post later claims from it
    # still has to survive the fact-check.
    from factcheck import _norm, on_page, share   # here: factcheck imports us
    if on_page(page, quote) or _norm(quote)[:need] in _norm(page) \
            or share(page, quote) >= 0.5:
        return None
    # Say how close it came. 0% is an invented quote; 30% is a loose
    # paraphrase; those need different fixes, and a bare message cannot tell.
    return (f"that sentence is not on that page "
            f"({share(page, quote):.0%} of it found): {quote[:90]!r}")


def since(mark):
    """URLs retrieved since `mark`, a set captured with `set(RETRIEVED)`.

    The generator shows the model this list and tells it to cite from it.
    Asking a model to remember a URL across a dozen tool calls does not work —
    it reconstructs one that looks right — so it is given the real ones to
    choose between instead.
    """
    return sorted(RETRIEVED - mark)

# A bare urllib request gets 403 from most government and news sites. These
# are the headers a browser sends; without the Accept and Accept-Language
# lines uscis.gov in particular refuses outright.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/128.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "close",
    "Upgrade-Insecure-Requests": "1",
}

MAX_PAGE_CHARS = 6000

_TAGS = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANK = re.compile(r"\n\s*\n\s*\n+")


def _ddgs():
    try:
        from ddgs import DDGS
    except ImportError:
        sys.exit("pip install ddgs   (web search for the generator)")
    return DDGS


def search(query, max_results=6, recent=None):
    """DuckDuckGo results as [{title, url, snippet}].

    `recent` is 'd', 'w', 'm' or 'y' to restrict to the last day/week/month/
    year — the news accounts need this far more than a general query does.
    Rate limiting is the normal failure here, so it backs off and retries;
    an empty list is returned rather than raised, because a batch grounded on
    five of six searches is worth far more than no batch.
    """
    DDGS = _ddgs()
    last = None
    for attempt in range(3):
        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results,
                                     timelimit=recent))
            out = []
            for r in raw:
                url = r.get("href") or r.get("url") or ""
                if not url.startswith("http"):
                    continue
                RETRIEVED.add(url)
                TITLES.setdefault(url, r.get("title", ""))
                out.append({"title": r.get("title", ""), "url": url,
                            "snippet": r.get("body", "")})
            return out
        except Exception as e:                          # noqa: BLE001
            # ddgs raises rather than returning [] when a query matches
            # nothing. That is an answer, not a failure — retrying it three
            # times with backoff just burns fourteen seconds to be told the
            # same thing, and the model should hear it and reword.
            if "no results" in str(e).lower():
                return []
            last = e
            time.sleep(2 ** attempt * 2)
    print(f"  ::warning::search {query!r} failed after 3 tries ({last})")
    return []


def _get_urllib(url):
    req = urllib.request.Request(url, headers=dict(BROWSER_HEADERS))
    with urllib.request.urlopen(req, timeout=30) as r:
        ctype = (r.headers.get("Content-Type") or "").lower()
        return r.read(2_000_000), ctype, r.geturl()


def _get_curl(url):
    """curl gets pages urllib cannot.

    uscis.gov and dhs.gov sit behind bot detection that reads the TLS
    handshake, not the headers: identical headers from urllib get 403 and
    from curl get 200. Those two domains are half of inmigraforma's allowed
    sources, so this fallback is what keeps that account able to cite anything
    at all. curl ships with Windows 11 and with every CI image, so it costs no
    new dependency.
    """
    # --fail: an HTTP error is not a page. Without it a 404 body came back as
    # readable text, went into PAGES and, worse, into RETRIEVED - so a URL
    # the model invented counted as one a tool had returned (2026-09-23,
    # xero.com/blog/ai-expense-automation).
    out = subprocess.run(
        ["curl", "-sSL", "--fail", "--max-time", "40", "--compressed",
         "-A", BROWSER_HEADERS["User-Agent"],
         "-H", "Accept: " + BROWSER_HEADERS["Accept"],
         "-H", "Accept-Language: " + BROWSER_HEADERS["Accept-Language"],
         "-w", "\n__CURL_URL__%{url_effective}\n__CURL_TYPE__%{content_type}",
         url],
        capture_output=True, timeout=60)
    if out.returncode != 0:
        raise OSError(out.stderr.decode("utf-8", "replace")[:200] or
                      f"curl exit {out.returncode}")
    body = out.stdout
    final, ctype = url, "text/html"
    m = re.search(rb"\n__CURL_URL__(.*?)\n__CURL_TYPE__(.*)$", body, re.S)
    if m:
        body = body[:m.start()]
        final = m.group(1).decode("utf-8", "replace").strip() or url
        ctype = m.group(2).decode("utf-8", "replace").strip().lower()
    return body, ctype, final


def fetch(url, max_chars=MAX_PAGE_CHARS):
    """A page as plain text, so the model reads the source instead of the
    snippet. Reading is also what earns a URL its place in RETRIEVED for the
    accounts that must cite the exact official page.
    """
    if not url.startswith(("http://", "https://")):
        return f"refused: {url!r} is not an http(s) URL"
    try:
        body, ctype, final = _get_urllib(url)
    except Exception as first:                          # noqa: BLE001
        try:
            body, ctype, final = _get_curl(url)
        except Exception as second:                     # noqa: BLE001
            return f"could not open {url}: {first}; curl also failed: {second}"

    if "html" not in ctype and "text" not in ctype and "json" not in ctype:
        return f"{url} is {ctype or 'an unknown type'}, not readable text"

    # Honour the declared charset. Half this pipeline publishes in Spanish, so
    # a latin-1 page silently decoded as UTF-8 turns every accent into a
    # replacement character and the model quotes the damage.
    m = re.search(r"charset=([\w-]+)", ctype)
    try:
        text = body.decode(m.group(1) if m else "utf-8", "replace")
    except LookupError:
        text = body.decode("utf-8", "replace")
    text = _TAGS.sub(" ", text)
    text = _ANY_TAG.sub("\n", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))
    text = _BLANK.sub("\n\n", _WS.sub(" ", text)).strip()

    # Record the URL the server actually served, redirects included — that is
    # the one the model will be quoting, and the one a reader would open.
    RETRIEVED.add(url)
    RETRIEVED.add(final)
    PAGES[url] = PAGES[final] = text
    if final != url:
        LANDED[url] = final
    post = covered_by(final)
    if post:
        # Asked for one address, served a page that already has a post.
        return (f"Not shown: {url} leads to a page already posted ({post}). "
                f"Choose a different story.")
    clipped = text[:max_chars]
    if len(text) > max_chars:
        clipped += f"\n\n[...truncated at {max_chars} characters]"
    return f"URL: {final}\n\n{clipped}"


# ---------------------------------------------------------------- tool wiring

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web. Use this before writing anything, for every "
                "fact that could have changed. Returns titles, URLs and "
                "snippets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "The search query."},
                    "recent": {
                        "type": "string",
                        "enum": ["d", "w", "m", "y"],
                        "description": ("Restrict to the last day, week, month "
                                        "or year. Use it for anything current."),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": (
                "Open one URL from a search result and read its text. Do this "
                "before citing a page as a source — a snippet is not the "
                "document, and a URL you have not opened may not exist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string",
                            "description": "A URL from an earlier search result."},
                },
                "required": ["url"],
            },
        },
    },
]


def dispatch(name, args):
    """Run one tool call. Never raises — llm.tool_loop reports the text back
    to the model, which can then try a different query.
    """
    if name == "web_search":
        hits = search(args.get("query", ""), recent=args.get("recent"))
        if not hits:
            return "No results. Try different wording."
        fresh = [h for h in hits if not covered_by(h["url"])]
        if not fresh:
            return ("Every result is a story this account has already posted. "
                    "Search for a different story.")
        note = ("" if len(fresh) == len(hits) else
                f"\n\n({len(hits) - len(fresh)} result(s) left out: already posted.)")
        return "\n\n".join(
            f"{h['title']}\n{h['url']}\n{h['snippet']}" for h in fresh) + note
    if name == "read_page":
        post = covered_by(args.get("url", ""))
        if post:
            # Not fetched: the page text is what fills the window, and a
            # covered page can never become a brief.
            return (f"Not opened: this page is already posted ({post}). "
                    f"Choose a different story.")
        return fetch(args.get("url", ""))
    return f"unknown tool {name!r}"


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python search.py <query>")
    hits = search(" ".join(sys.argv[1:]))
    print(json.dumps(hits, indent=2, ensure_ascii=False))
    if hits:
        print("\n--- first result ---\n")
        print(fetch(hits[0]["url"])[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
