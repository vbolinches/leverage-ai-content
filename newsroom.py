#!/usr/bin/env python3
"""What the official sources published recently, read in code, not searched.

Research used to start from a web search. A 30B model then had to find the
official page for each lead itself, and that is where it failed: on
2026-09-23 two inmigraforma runs ended with one usable topic and then none -
quotes 0-30% on the page, URLs that 404, sweeps that "retrieved no usable
URLs". leverageai's research cited seven URLs no tool had returned, every one
an imagined product.

The model is good at choosing from a list and bad at recalling a URL (see
CLAUDE.md, point 10). So this module reads the sources' own listings - the
USCIS newsroom, the Federal Register API, a product's release feed - and
hands the researcher real, dated, exact URLs to choose from. Every URL here
came from a page this process read, so it is recorded in search.RETRIEVED
like any other.

Configured per account as "newsroom": a list of
    {"kind": "uscis"}
    {"kind": "dhs", "keep": "<regex a title must match>"}
    {"kind": "federal_register", "agencies": ["u-s-citizenship-..."]}
    {"kind": "rss", "url": "https://..."}
and "newsroom_skip", a regex for titles that are never this account's news.

    python newsroom.py <account>      # print what research would be shown
"""
import html, json, re, sys, urllib.parse, urllib.request
from datetime import date, timedelta

import search

DAYS = 45
MAX_ITEMS = 30
PER_SOURCE = 12

# Paperwork Reduction Act notices, meetings and privacy notices are the bulk
# of the Federal Register for these agencies - 40 of USCIS's last 42 entries.
_FR_NOISE = re.compile(
    r"information collection|agency information|paperwork|meeting|privacy act|"
    r"system of records|computer matching", re.I)


def uscis(days=DAYS):
    """The USCIS newsroom's own list: news releases and alerts."""
    url = "https://www.uscis.gov/newsroom/all-news?items_per_page=50"
    body, _, _ = search._get_curl(url)
    page = body.decode("utf-8", "replace")
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for row in re.findall(r'<div class="views-row">(.*?)(?=<div class="views-row">|</main>)',
                          page, re.S):
        a = re.search(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', row)
        d = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', row)
        if not a or not d or d.group(1) < cutoff:
            continue
        summary = re.sub(r"<[^>]+>", " ", row[a.end():])
        summary = re.sub(r"\s+", " ", html.unescape(summary)).strip()
        summary = re.sub(r"^\w+ \d{1,2}, \d{4}\s*", "", summary)
        out.append({"title": html.unescape(a.group(2)).strip(),
                    "url": urllib.parse.urljoin("https://www.uscis.gov", a.group(1)),
                    "date": d.group(1), "summary": summary[:240]})
    return out


DHS_URL = "https://www.dhs.gov/news-releases/press-releases?items_per_page=50"


def dhs(days=DAYS, keep=None, page=None):
    """DHS's own press-release list (added 2026-11-21, Vicente's call: the
    one new post the night before came from a DHS release).

    Most of DHS's releases are arrest round-ups and statements about
    sanctuary cities - 45 of the 50 on 2026-11-21. `keep` is a regex a title
    must match to be offered at all (the account's "keep" for this source), so
    research sees the few that change someone's paperwork: E-Verify, asylum,
    parole, TPS, fees, programs.
    """
    if page is None:
        body, _, _ = search._get_curl(DHS_URL)
        page = body.decode("utf-8", "replace")
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    keep_re = re.compile(keep, re.I) if keep else None
    out = []
    for row in re.findall(r'<li class="usa-collection__item">(.*?)</li>', page, re.S):
        a = re.search(r'usa-collection__heading">\s*<a href="([^"]+)"[^>]*>([^<]+)</a>',
                      row)
        d = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', row)
        if not a or not d or d.group(1) < cutoff:
            continue
        title = re.sub(r"\s+", " ", html.unescape(a.group(2))).strip()
        if keep_re and not keep_re.search(title):
            continue
        p = re.search(r"<p>(.*?)</p>", row[a.end():], re.S)
        summary = re.sub(r"<[^>]+>", " ", p.group(1)) if p else ""
        summary = re.sub(r"\s+", " ", html.unescape(summary)).strip()
        out.append({"title": title,
                    "url": urllib.parse.urljoin("https://www.dhs.gov", a.group(1)),
                    "date": d.group(1), "summary": summary[:240]})
    return out


def federal_register(agencies, days=DAYS):
    """Rules, proposed rules and real notices from these agencies."""
    q = [("per_page", "100"), ("order", "newest"),
         ("conditions[publication_date][gte]",
          (date.today() - timedelta(days=days)).isoformat())]
    q += [("conditions[agencies][]", a) for a in agencies]
    q += [("fields[]", f) for f in ("title", "type", "publication_date",
                                     "html_url", "abstract")]
    url = ("https://www.federalregister.gov/api/v1/documents.json?"
           + urllib.parse.urlencode(q))
    req = urllib.request.Request(url, headers=dict(search.BROWSER_HEADERS))
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    out = []
    for d in data.get("results") or []:
        if _FR_NOISE.search(d.get("title") or ""):
            continue
        out.append({"title": f"{d.get('type', '')}: {d['title']}",
                    "url": d["html_url"], "date": d["publication_date"],
                    "summary": (d.get("abstract") or "")[:240]})
    return out


def rss(url, days=DAYS):
    import ideas
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [{"title": i["title"], "url": i["url"], "date": (i.get("at") or "")[:10],
             "summary": ""}
            for i in ideas.fetch_rss([url])
            if i.get("url") and (i.get("at") or "9999")[:10] >= cutoff]


def items(acct):
    """Every recent official item for this account, newest first, deduped."""
    skip = re.compile(acct["newsroom_skip"], re.I) if acct.get("newsroom_skip") else None
    got = []
    for src in acct.get("newsroom") or []:
        try:
            if src["kind"] == "uscis":
                rows = uscis()
            elif src["kind"] == "dhs":
                rows = dhs(keep=src.get("keep"))
            elif src["kind"] == "federal_register":
                rows = federal_register(src["agencies"])
            elif src["kind"] == "rss":
                rows = rss(src["url"])
            else:
                continue
            # Per source, after the skip filter: OpenAI's feed alone carries
            # 100 items, and one loud source must not crowd out the rest.
            rows = [r for r in rows if not (skip and skip.search(r["title"]))]
            got += sorted(rows, key=lambda i: i["date"], reverse=True)[:PER_SOURCE]
        except Exception as e:                              # noqa: BLE001
            # One dead source must not cost the night; research still has
            # its search tools.
            print(f"  ::warning::newsroom {src}: {type(e).__name__}: {e}")
    seen, out = set(), []
    for it in sorted(got, key=lambda i: i["date"], reverse=True):
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        out.append(it)
        # A real listing handed us this URL: that is exactly what RETRIEVED
        # records, and it lets a brief cite it.
        search.RETRIEVED.add(it["url"])
        search.TITLES[it["url"]] = it["title"]
    return out[:MAX_ITEMS]


def digest(acct, covered=None):
    """The listing as research sees it, minus pages that already have a post.

    Returns (text, urls, hidden): hidden is [(url, post id)] for the rows left
    out. Until 2026-11-16 this took an `avoid` argument it never used and
    nobody passed, so the listing - which tells the model to choose from it
    first - offered posted stories every night. On the 16th all four of
    inmigraforma's items already had posts, and research briefed those four
    in every sweep.

    `covered` maps search.canonical(url) to a post id; by default it is
    search.COVERED, which research fills before calling this.
    """
    covered = search.COVERED if covered is None else covered
    rows, hidden = [], []
    for i in items(acct):
        post = covered.get(search.canonical(i["url"]))
        if post:
            hidden.append((i["url"], post))
        else:
            rows.append(i)
    if not rows:
        if hidden:
            return ("OFFICIAL NEWS: every item the official listings published "
                    "in the last six weeks already has a post. Search for "
                    "other news."), [], hidden
        return "", [], hidden
    lines = [f"- {i['date']} | {i['title']}\n  {i['url']}"
             + (f"\n  {i['summary']}" if i['summary'] else "") for i in rows]
    text = ("OFFICIAL NEWS FROM THE LAST SIX WEEKS - read from the sources' own "
            "listings, so every URL below is real and exact. Choose your topics "
            "from this list first. Open the page before you use it, and cite "
            "that URL exactly. Items this account already posted are left out."
            "\n\n" + "\n".join(lines))
    return text, [i["url"] for i in rows], hidden


if __name__ == "__main__":
    import accounts
    acct = accounts.get(sys.argv[1] if len(sys.argv) > 1 else None)
    import os
    os.environ["ACCOUNT"] = acct["slug"]     # generate_batch reads it on import
    import generate_batch
    search.COVERED.update(generate_batch.used_sources(generate_batch.load_queue()))
    text, urls, hidden = digest(acct)
    print(text or "(no newsroom configured, or every source failed)")
    for url, post in hidden:
        print(f"  hidden, already posted as {post}: {url}")
    print(f"\n{len(urls)} item(s) offered, {len(hidden)} hidden")
