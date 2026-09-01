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

import accounts

ACCT = accounts.get()
EXPECTED_USERNAME = ACCT["username"]

# Route-aware (see publish.py): facebook_page accounts are read through
# graph.facebook.com, where there is no Instagram /me — the IG node is
# addressed by id and answers the same fields.
ROUTE = ACCT.get("api", "instagram_login")
GRAPH = ("https://graph.facebook.com/v21.0" if ROUTE == "facebook_page"
         else "https://graph.instagram.com/v21.0")
TOKEN = os.environ.get("IG_ACCESS_TOKEN")
IG_ID = os.environ.get("IG_USER_ID")


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
        with open(ACCT.token_status, encoding="utf-8") as f:
            data = json.load(f)
        expires = date.fromisoformat(data["expires"])
    except Exception as e:
        return None, f"{ACCT.token_status} unreadable: {e}"
    return (expires - date.today()).days, None


def collect():
    report = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    days, err = token_runway()
    report["token"] = {"days_left": days, "error": err}

    if ROUTE == "facebook_page":
        me, err = get(str(IG_ID), fields="username,followers_count,media_count")
    else:
        me, err = get("me", fields="user_id,username,account_type,"
                                   "followers_count,media_count")
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
        "type": me.get("account_type", "business"),
        "followers": me.get("followers_count"),
        "posts": me.get("media_count"),
    }

    # Reach, saves and shares are the metrics that actually explain growth;
    # likes explain very little. They need instagram_business_manage_insights,
    # and a token minted AFTER that permission was added — an older token
    # silently lacks the scope, so failures here are reported, not fatal.
    acct_insights, acct_err = get(
        f"{IG_ID}/insights",
        metric="reach,profile_views,accounts_engaged,total_interactions",
        period="day",
        metric_type="total_value",
    )
    if acct_err:
        report["insights_error"] = acct_err
    else:
        report["account_insights"] = {
            d["name"]: d.get("total_value", {}).get("value")
            for d in acct_insights.get("data", [])
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
        entry = {
            "id": m["id"],
            "posted": m.get("timestamp", "")[:10],
            "hook": cap,
            "likes": m.get("like_count", 0),
            "comments": m.get("comments_count", 0),
            "url": m.get("permalink"),
        }
        # Per-post reach and saves — the diagnostic pair. Low reach means a
        # distribution problem; high reach with low saves means a content one.
        ins, ins_err = get(f"{m['id']}/insights",
                           metric="reach,saved,shares,total_interactions")
        if not ins_err:
            for d in ins.get("data", []):
                vals = d.get("values") or [{}]
                entry[d["name"]] = vals[0].get("value")
        posts.append(entry)
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
        print(f"::error::[{ACCT['slug']}] {report['fatal']}")
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

    ai = report.get("account_insights")
    if ai:
        print("\naccount (last 24h)")
        for k, v in ai.items():
            print(f"  {k}: {v}")
    elif report.get("insights_error"):
        print(f"\n::warning::insights unavailable — {report['insights_error']}")
        print("  If this mentions permissions, regenerate IG_ACCESS_TOKEN: the "
              "token predates instagram_business_manage_insights being added.")

    print("\nrecent posts")
    has_reach = any("reach" in p for p in report["posts_recent"])
    for p in report["posts_recent"]:
        line = f"  {p['posted']}  {p['likes']:>5}L {p['comments']:>4}C"
        if has_reach:
            line += (f" {p.get('reach', '?'):>6} reach"
                     f" {p.get('saved', '?'):>4} saves")
        print(f"{line}  {p['hook']}")

    # The diagnosis the whole exercise is for.
    reached = [p for p in report["posts_recent"] if p.get("reach")]
    if reached:
        avg_reach = sum(p["reach"] for p in reached) / len(reached)
        saves = sum(p.get("saved") or 0 for p in reached)
        rate = saves / sum(p["reach"] for p in reached) * 100
        print(f"\ndiagnosis: avg reach {avg_reach:.0f}/post, "
              f"save rate {rate:.1f}%")
        if avg_reach < 100:
            print("  -> DISTRIBUTION problem: too few people see these. "
                  "Better content will not fix it.")
        elif rate < 1:
            print("  -> CONTENT problem: people see it and do not save it.")
        else:
            print("  -> Content is landing; growth is a conversion/volume question.")

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

    # This output lands in PUBLIC Actions logs (public repo): never print who
    # commented or what they said — for this audience that can be sensitive.
    # The post permalink is enough to find and answer them in the app.
    pending = report.get("comments_awaiting_reply", [])
    print(f"\ncomments awaiting reply: {len(pending)}")
    for c in pending:
        print(f"  ({c['at']}) -> {c['post']}")

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
            print(f"::error::[{ACCT['slug']}] Instagram token expires in {days} "
                  f"days. Refresh it now or publishing stops. "
                  f"See {ACCT.token_status}.")
            return 1
        if days <= 21:
            print(f"::warning::Instagram token expires in {days} days — refresh soon.")
    return 0



def check_pat():
    """Warn before GH_PAT expires, and shout if it is already dead.

    GH_PAT is what lets the monthly refresh workflow write a refreshed
    Instagram token back into the repo's secrets. Nothing else watches it, and
    its failure mode is silent: refresh stops working, no post fails, and
    publishing dies ~60 days later when the Instagram tokens themselves lapse.
    That is exactly what happened between 2026-08-01 and 2026-09-01. The PAT is
    shared across accounts, so this runs once per digest, not once per account.
    """
    pat = os.environ.get("GH_PAT", "").strip()
    if not pat:
        print("::warning::GH_PAT is not available to the monitor, so its "
              "expiry cannot be checked. Instagram token auto-refresh depends "
              "on it.")
        return 0

    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {pat}",
                 "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req) as r:
            expires = r.headers.get("github-authentication-token-expiration")
    except urllib.error.HTTPError as e:
        print(f"::error::GH_PAT is rejected by GitHub (HTTP {e.code}). Token "
              f"auto-refresh is broken until it is replaced, and publishing "
              f"stops roughly 60 days after the last successful refresh. "
              f"Replace it at "
              f"https://github.com/settings/personal-access-tokens")
        return 1
    except Exception as e:
        print(f"::warning::could not check GH_PAT expiry: {e}")
        return 0

    if not expires:
        print("GH_PAT authenticates and carries no expiry date.")
        return 0

    # GitHub sends "2027-08-31 00:00:00 +0000" (and has used an ISO form);
    # the leading date is the part that matters either way.
    try:
        day = date.fromisoformat(expires.strip()[:10])
    except ValueError:
        print(f"::warning::GH_PAT expiry not parseable: {expires!r}")
        return 0

    left = (day - date.today()).days
    print(f"GH_PAT expires in {left} days ({day})")
    if left <= 14:
        print(f"::error::GH_PAT expires in {left} days. Once it lapses the "
              f"monthly token refresh fails silently and publishing stops "
              f"about 60 days later. Replace it at "
              f"https://github.com/settings/personal-access-tokens")
        return 1
    if left <= 45:
        print(f"::warning::GH_PAT expires in {left} days ({day}) — replace it "
              f"before it lapses; Instagram token auto-refresh depends on it.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check-pat", action="store_true",
                    help="check GH_PAT's expiry instead of an account digest; "
                         "needs no Instagram credentials")
    a = ap.parse_args()

    # Runs on its own, before the Instagram credential check: the PAT has
    # nothing to do with any one account, and this must still report when an
    # account's own token is missing.
    if a.check_pat:
        return check_pat()

    if not TOKEN or not IG_ID:
        sys.exit("IG_ACCESS_TOKEN and IG_USER_ID must be set")

    block_file = ACCT.path("block_status.json")
    if os.path.exists(block_file):
        with open(block_file, encoding="utf-8") as f:
            bs = json.load(f)
        print(f"::warning::[{ACCT['slug']}] Instagram action block active "
              f"since {bs.get('first_detected')} (strike {bs.get('strikes')}, "
              f"next publish attempt after {bs.get('cooldown_until')}). "
              f"Appealing in the app is the only accelerator.")

    report = collect()
    if a.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1 if "fatal" in report else 0
    return render(report)


if __name__ == "__main__":
    sys.exit(main())
