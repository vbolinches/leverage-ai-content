#!/usr/bin/env python3
"""Account monitor for @leverageai.daily — reports, never acts.

Pulls follower/media counts, per-post performance, and unanswered comments,
then prints a digest. Run it on a schedule and the digest lands in the Actions
log; failures surface as email.

    python monitor.py
    python monitor.py --json          # machine-readable

Reads IG_ACCESS_TOKEN and IG_USER_ID from the environment.

Deliberately read-only. It does not reply, like, or follow — see MONITORING.md
for why those are handled differently.
"""
import argparse, json, os, sys, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timezone

GRAPH = "https://graph.instagram.com/v21.0"
TOKEN = os.environ.get("IG_ACCESS_TOKEN")
IG_ID = os.environ.get("IG_USER_ID")

EXPECTED_USERNAME = "leverageai.daily"


def get(path, **params):
    """GET a Graph edge. Returns (data, error_message)."""
    params["access_token"] = TOKEN
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
            return None, body.get("error", {}).get("message", str(e))
        except Exception:
            return None, str(e)
    except Exception as e:
        return None, str(e)


def token_runway():
    """Days until IG_ACCESS_TOKEN expires, from the committed date file.

    graph.instagram.com exposes no token-debug endpoint, and calling
    refresh_access_token to check would mint a new token as a side effect — so
    the expiry date is tracked in token_status.json instead.
    """
    try:
        with open("token_status.json", encoding="utf-8") as f:
            data = json.load(f)
        expires = date.fromisoformat(data["expires"])
    except Exception as e:
        return None, f"token_status.json unreadable: {e}"
    return (expires - date.today()).days, None


def collect():
    report = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    days, err = token_runway()
    report["token"] = {"days_left": days, "error": err}

    me, err = get("me", fields="user_id,username,account_type,followers_count,media_count")
    if err:
        report["fatal"] = f"account lookup failed: {err}"
        return report

    # Same guard the publisher uses: never report on the wrong account.
    if me.get("username") != EXPECTED_USERNAME:
        report["fatal"] = (f"token resolves to @{me.get('username')}, "
                           f"expected @{EXPECTED_USERNAME}")
        return report

    report["account"] = {
        "username": me.get("username"),
        "type": me.get("account_type"),
        "followers": me.get("followers_count"),
        "posts": me.get("media_count"),
    }

    media, err = get(f"{IG_ID}/media",
                     fields="id,caption,permalink,timestamp,media_type,"
                            "like_count,comments_count",
                     limit=12)
    if err:
        report["media_error"] = err
        return report

    posts = []
    for m in media.get("data", []):
        cap = (m.get("caption") or "").split("\n")[0][:60]
        posts.append({
            "id": m["id"],
            "posted": m.get("timestamp", "")[:10],
            "hook": cap,
            "likes": m.get("like_count", 0),
            "comments": m.get("comments_count", 0),
            "url": m.get("permalink"),
        })
    report["posts_recent"] = posts

    if posts:
        report["totals"] = {
            "likes": sum(p["likes"] for p in posts),
            "comments": sum(p["comments"] for p in posts),
        }
        report["best"] = max(posts, key=lambda p: p["likes"] + 3 * p["comments"])

    # Comments needing a human reply. Requires instagram_business_manage_comments.
    needs_reply = []
    for p in posts:
        if not p["comments"]:
            continue
        c, err = get(f"{p['id']}/comments",
                     fields="id,text,username,timestamp,replies{id}")
        if err:
            report.setdefault("comment_errors", []).append(f"{p['id']}: {err}")
            continue
        for item in c.get("data", []):
            if item.get("username") == EXPECTED_USERNAME:
                continue
            if item.get("replies", {}).get("data"):
                continue
            needs_reply.append({
                "post": p["url"],
                "from": item.get("username"),
                "text": (item.get("text") or "")[:160],
                "at": item.get("timestamp", "")[:16],
            })
    report["comments_awaiting_reply"] = needs_reply

    return report


def render(report):
    if "fatal" in report:
        print(f"::error::{report['fatal']}")
        return 1

    a = report["account"]
    print(f"@{a['username']} ({a['type']})")
    print(f"  followers: {a['followers']}    posts: {a['posts']}")

    if report.get("media_error"):
        print(f"::warning::media unavailable: {report['media_error']}")
        return 0

    t = report.get("totals")
    if t:
        print(f"  last {len(report['posts_recent'])} posts: "
              f"{t['likes']} likes, {t['comments']} comments")
        b = report["best"]
        print(f"  best: {b['likes']}L/{b['comments']}C  {b['hook']}")
        print(f"        {b['url']}")

    print("\nrecent posts")
    for p in report["posts_recent"]:
        print(f"  {p['posted']}  {p['likes']:>5}L {p['comments']:>4}C  {p['hook']}")

    # Accumulate history — the API only reports current counts, so the time
    # series has to be built up snapshot by snapshot for the feedback loop.
    try:
        import performance
        performance.record(a["followers"], report["posts_recent"])
        _, stats = performance.brief()
        if stats.get("reason"):
            print(f"\nlearning: not yet — {stats['reason']}")
        else:
            print(f"\nlearning: active on {stats['posts']} posts "
                  f"({stats['total_engagement']} engagement)")
    except Exception as e:
        print(f"::warning::could not record performance history: {e}")

    pending = report.get("comments_awaiting_reply", [])
    print(f"\ncomments awaiting reply: {len(pending)}")
    for c in pending:
        print(f"  @{c['from']} ({c['at']}): {c['text']}")
        print(f"    -> {c['post']}")

    for e in report.get("comment_errors", []):
        print(f"::warning::comments: {e}")

    if pending:
        print(f"\n::notice::{len(pending)} comment(s) need a reply.")

    tok = report.get("token", {})
    days = tok.get("days_left")
    if tok.get("error"):
        print(f"::warning::token expiry unknown — {tok['error']}")
    elif days is not None:
        print(f"\ntoken expires in {days} days")
        if days <= 10:
            print(f"::error::Instagram token expires in {days} days. Refresh it now "
                  f"or publishing stops. See token_status.json.")
            return 1
        if days <= 21:
            print(f"::warning::Instagram token expires in {days} days — refresh soon.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not TOKEN or not IG_ID:
        sys.exit("IG_ACCESS_TOKEN and IG_USER_ID must be set")

    report = collect()
    if a.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1 if "fatal" in report else 0
    return render(report)


if __name__ == "__main__":
    sys.exit(main())
