#!/usr/bin/env python3
"""Monitor outside sources for content ideas, per account.

Pulls recent signals from three kinds of source, all configured in the
account's account.json under "idea_sources":

  x_accounts   X (Twitter) handles, via the official X API v2. Costs money:
               X moved to pay-per-use in Feb 2026 (~$0.005/post read), so this
               source only runs when X_BEARER_TOKEN is set. Skipped silently
               otherwise — the free sources below carry the file until then.
  rss          RSS/Atom feeds (blogs, newsletters, news sections). Free.
  hn_queries   Hacker News searches via the free Algolia API. Free.

Results accumulate in accounts/<slug>/ideas.json — deduped, dated, trimmed to
the recent window — and generate_batch.py folds the freshest items into the
authoring prompt as *leads to investigate*, not facts: the generator's web
search remains the source of truth (never state a fact you did not verify).

    python ideas.py                 # refresh the selected account
    python ideas.py --all           # refresh every enabled account
    python ideas.py --show          # print the current digest, fetch nothing
"""
import argparse, io, json, os, re, sys, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone, timedelta

import accounts

KEEP_DAYS = 21          # drop items older than this
KEEP_MAX = 80           # hard cap on stored items
DIGEST_MAX = 18         # items surfaced to the generator
UA = {"User-Agent": "Mozilla/5.0 (content-ideas-monitor)"}


def _get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _iso(dt_str):
    """Best-effort normalisation of feed dates to YYYY-MM-DD."""
    if not dt_str:
        return None
    s = dt_str.strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None


# ---------------------------------------------------------------- sources ---

def fetch_x(handles, token):
    """Latest original posts from each handle, ranked by engagement.

    Two endpoints per handle (id lookup + timeline); ids are cached by the
    caller so steady-state cost is one timeline read per handle per run.
    """
    out, id_cache_updates = [], {}
    for h in handles:
        h = h.lstrip("@")
        try:
            u = json.loads(_get(
                f"https://api.x.com/2/users/by/username/{urllib.parse.quote(h)}",
                headers={"Authorization": f"Bearer {token}"}))
            uid = u["data"]["id"]
            id_cache_updates[h] = uid
            tl = json.loads(_get(
                f"https://api.x.com/2/users/{uid}/tweets?max_results=10"
                "&exclude=retweets,replies"
                "&tweet.fields=created_at,public_metrics",
                headers={"Authorization": f"Bearer {token}"}))
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            print(f"::warning::X @{h}: HTTP {e.code} — {body}")
            if e.code in (402, 403):
                print("::warning::X API refused access — the developer account "
                      "likely has no active pay-per-use billing. Skipping X.")
                break
            continue
        except Exception as e:
            print(f"::warning::X @{h}: {e}")
            continue

        for t in tl.get("data", []):
            m = t.get("public_metrics", {})
            out.append({
                "source": f"x/@{h}",
                "title": t["text"][:220].replace("\n", " "),
                "url": f"https://x.com/{h}/status/{t['id']}",
                "at": _iso(t.get("created_at")),
                "signal": m.get("like_count", 0) + 2 * m.get("retweet_count", 0),
            })
    return out


def fetch_rss(feeds):
    out = []
    for url in feeds:
        try:
            root = ET.fromstring(_get(url))
        except Exception as e:
            print(f"::warning::rss {url}: {e}")
            continue
        ns = {"a": "http://www.w3.org/2005/Atom"}
        # RSS 2.0
        for item in root.iter("item"):
            out.append({
                "source": f"rss/{urllib.parse.urlparse(url).netloc}",
                "title": (item.findtext("title") or "")[:220],
                "url": item.findtext("link") or "",
                "at": _iso(item.findtext("pubDate")),
                "signal": 0,
            })
        # Atom
        for entry in root.findall("a:entry", ns):
            link = entry.find("a:link", ns)
            out.append({
                "source": f"rss/{urllib.parse.urlparse(url).netloc}",
                "title": (entry.findtext("a:title", namespaces=ns) or "")[:220],
                "url": link.get("href") if link is not None else "",
                "at": _iso(entry.findtext("a:updated", namespaces=ns)
                           or entry.findtext("a:published", namespaces=ns)),
                "signal": 0,
            })
    return out


def fetch_hn(queries):
    out = []
    since = int((datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).timestamp())
    for q in queries:
        url = ("https://hn.algolia.com/api/v1/search?"
               + urllib.parse.urlencode({
                   "query": q, "tags": "story",
                   "numericFilters": f"points>40,created_at_i>{since}",
                   "hitsPerPage": 8}))
        try:
            hits = json.loads(_get(url)).get("hits", [])
        except Exception as e:
            print(f"::warning::hn {q!r}: {e}")
            continue
        for h in hits:
            out.append({
                "source": f"hn/{q}",
                "title": (h.get("title") or "")[:220],
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                "at": (h.get("created_at") or "")[:10] or None,
                "signal": h.get("points", 0),
            })
    return out


# ---------------------------------------------------------------- storage ---

def refresh(acct):
    cfg = acct.get("idea_sources") or {}
    path = acct.path("ideas.json")
    store = {"items": []}
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as f:
            store = json.load(f)

    fresh = []
    fresh += fetch_rss(cfg.get("rss") or [])
    fresh += fetch_hn(cfg.get("hn_queries") or [])

    x_handles = cfg.get("x_accounts") or []
    token = os.environ.get("X_BEARER_TOKEN")
    if x_handles and token:
        fresh += fetch_x(x_handles, token)
    elif x_handles:
        print(f"[{acct['slug']}] X_BEARER_TOKEN not set — skipping "
              f"{len(x_handles)} X account(s), using free sources only")

    # Dedup by URL, keep first-seen; refresh signal if it grew.
    seen = {i["url"]: i for i in store["items"] if i.get("url")}
    added = 0
    for i in fresh:
        if not i["url"] or not i["title"]:
            continue
        if i["url"] in seen:
            seen[i["url"]]["signal"] = max(seen[i["url"]].get("signal", 0),
                                           i.get("signal", 0))
        else:
            i["seen"] = date.today().isoformat()
            seen[i["url"]] = i
            added += 1

    floor = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    items = [i for i in seen.values() if (i.get("at") or i["seen"]) >= floor]
    items.sort(key=lambda i: ((i.get("at") or i["seen"]), i.get("signal", 0)),
               reverse=True)
    store = {"updated": date.today().isoformat(), "items": items[:KEEP_MAX]}

    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, ensure_ascii=False)
    print(f"[{acct['slug']}] +{added} new, {len(store['items'])} kept -> {path}")
    return store


def digest(acct, max_items=DIGEST_MAX, max_age_days=14):
    """Prompt-ready digest of recent signals, or '' when stale/absent."""
    path = acct.path("ideas.json")
    if not os.path.exists(path):
        return ""
    with io.open(path, encoding="utf-8") as f:
        store = json.load(f)
    updated = store.get("updated")
    if not updated or (date.today() - date.fromisoformat(updated)).days > max_age_days:
        return ""
    lines = [f"- [{i['source']}] {i['title']} ({i['url']})"
             for i in store.get("items", [])[:max_items]]
    if not lines:
        return ""
    return ("RECENT SIGNALS from monitored sources (X, feeds, HN), newest "
            "first. These are LEADS for topics people are discussing now — "
            "not verified facts. If one inspires a post, verify the details "
            "by web search first:\n" + "\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every enabled account")
    ap.add_argument("--show", action="store_true", help="print digest only")
    a = ap.parse_args()

    targets = accounts.list_accounts() if a.all else [accounts.get()]
    for acct in targets:
        if a.show:
            print(digest(acct) or f"[{acct['slug']}] no fresh ideas file")
        else:
            refresh(acct)


if __name__ == "__main__":
    sys.exit(main())
