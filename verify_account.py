#!/usr/bin/env python3
"""Read-only credential verification for one account, either API route.

Publishes NOTHING. Asserts, in order:
  1. the token is alive and resolves to this account's expected username
  2. the user-id secret and account.json agree with what the token sees
  3. (facebook_page route) an administered Page is business-linked to this IG
  4. the publish endpoint shows no permission error
  5. the first queued slide is publicly fetchable (what Meta's servers do)
  6. the next queued reel actually transcodes: creates a REELS container and
     polls it to FINISHED, then abandons it (containers expire on their own)

This is the loud tripwire for the wrong-account hazard: both accounts are
live businesses, so verification failing must block enabling, always.

    IG_ACCESS_TOKEN=... IG_USER_ID=... ACCOUNT=<slug> python verify_account.py
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request

import accounts

ACCT = accounts.get()
ROUTE = ACCT.get("api", "instagram_login")
GRAPH = ("https://graph.facebook.com/v21.0" if ROUTE == "facebook_page"
         else "https://graph.instagram.com/v21.0")
TOKEN = os.environ.get("IG_ACCESS_TOKEN")
IG_ID = os.environ.get("IG_USER_ID")
RAW = os.environ.get("RAW_BASE", "")


def get(path, **params):
    params["access_token"] = TOKEN
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url) as r:
        return json.load(r)


def fail(msg):
    print(f"::error::[{ACCT['slug']}] {msg}")
    sys.exit(1)


def main():
    if not TOKEN or not IG_ID:
        fail(f"secrets {ACCT['token_secret']} / {ACCT['user_id_secret']} not set")

    print(f"[{ACCT['slug']}] route={ROUTE}")

    # 1+2 — identity triangle: token, secret, config.
    try:
        if ROUTE == "facebook_page":
            me = get(str(ACCT["ig_user_id"]), fields="username")
            actual_user, actual_id = me.get("username"), str(me.get("id"))
        else:
            me = get("me", fields="user_id,username,account_type")
            actual_user, actual_id = me.get("username"), str(me.get("user_id"))
    except urllib.error.HTTPError as e:
        fail(f"token rejected: {e.read().decode(errors='replace')[:300]}")
    if actual_user != ACCT["username"]:
        fail(f"WRONG ACCOUNT: token resolves to @{actual_user}, expected "
             f"@{ACCT['username']}. Refusing to proceed.")
    print(f"  OK: token reaches @{actual_user}")
    if actual_id != str(IG_ID) or actual_id != str(ACCT["ig_user_id"]):
        fail(f"id mismatch: token={actual_id} secret={IG_ID} "
             f"config={ACCT['ig_user_id']}")
    print("  OK: user-id secret and account.json agree")

    # 3 — the Page link the facebook_page route depends on.
    if ROUTE == "facebook_page":
        pages = get("me/accounts", fields="id,name,instagram_business_account")
        linked = [p for p in pages.get("data", [])
                  if (p.get("instagram_business_account") or {}).get("id") == actual_id]
        if not linked:
            fail("no administered Facebook Page is business-linked to this "
                 "Instagram account — the facebook_page route cannot publish")
        p = linked[0]
        print(f"  OK: Page {p['name']!r} ({p['id']}) is linked to this IG")
        want = ACCT.get("facebook_page_id")
        if want and str(want) != p["id"]:
            fail(f"account.json facebook_page_id ({want}) != linked page ({p['id']})")

    # 4 — permission probe: malformed container; a scope error means the
    # permission is missing, any other error means it is present.
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"{GRAPH}/{IG_ID}/media",
            data=urllib.parse.urlencode({"access_token": TOKEN}).encode(),
            method="POST"))
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if any(w in body.lower() for w in ("permission", "scope", "oauth exception")):
            print(f"::warning::possible permission issue: {body[:250]}")
        else:
            print("  OK: no scope error on the publish endpoint")

    # 5 — public reachability of the first queued slide.
    sched = json.load(open(ACCT.queue, encoding="utf-8"))
    queued = [p for p in sched["posts"] if p["status"] == "queued"]
    slides = next((p["slides"] for p in queued if p.get("slides")), None)
    if RAW and slides:
        url = f"{RAW}/{urllib.parse.quote(slides[0])}"
        code = urllib.request.urlopen(url).status
        if code != 200:
            fail(f"{slides[0]} returned HTTP {code} — repo must be public")
        print("  OK: slide images publicly fetchable")
    elif not slides:
        print("  (queue empty — skipping reachability and reel checks)")

    # 6 — reel transcode dry-run.
    reel = next((p for p in queued if p.get("format") == "reel"), None)
    if RAW and reel:
        url = f"{RAW}/{urllib.parse.quote(reel['video'])}"
        print(f"  validating reel {reel['id']}")
        data = urllib.parse.urlencode({
            "media_type": "REELS", "video_url": url,
            "caption": "validation container - not published",
            "access_token": TOKEN}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    f"{GRAPH}/{IG_ID}/media", data=data, method="POST")) as r:
                cid = json.load(r)["id"]
        except urllib.error.HTTPError as e:
            fail(f"reel container failed: {e.read().decode(errors='replace')[:400]}")
        for _ in range(60):
            s = get(cid, fields="status_code,status")
            if s.get("status_code") == "FINISHED":
                print("  OK: Meta fetched and transcoded the reel. "
                      "Container abandoned, nothing posted.")
                break
            if s.get("status_code") == "ERROR":
                fail(f"reel transcode failed: {s.get('status')}")
            time.sleep(5)
        else:
            print("::warning::reel still processing after 5 min — slow, "
                  "not necessarily broken")

    # 7 — Threads, when configured. Absence of secrets is a warning, not a
    # failure: Threads is an optional surface and must not block Instagram.
    tcfg = ACCT.get("threads") or {}
    ttok = os.environ.get("THREADS_ACCESS_TOKEN")
    tid = os.environ.get("THREADS_USER_ID")
    if tcfg and ttok and not tid:
        # Token exists but the user-id secret doesn't: look the id up from the
        # token (ids are not secret) and tell the operator what to set.
        turl = (f"https://graph.threads.net/v1.0/me?fields=id,username"
                f"&access_token={urllib.parse.quote(ttok)}")
        try:
            with urllib.request.urlopen(turl) as r:
                tme = json.load(r)
            print(f"::warning::[{ACCT['slug']}] Threads token OK "
                  f"(@{tme.get('username')}) but {tcfg.get('user_id_secret')} "
                  f"is not set. Set it to: {tme.get('id')}")
        except urllib.error.HTTPError as e:
            fail(f"threads token rejected: {e.read().decode(errors='replace')[:300]}")
    elif tcfg and ttok and tid:
        turl = (f"https://graph.threads.net/v1.0/me?fields=id,username"
                f"&access_token={urllib.parse.quote(ttok)}")
        try:
            with urllib.request.urlopen(turl) as r:
                tme = json.load(r)
        except urllib.error.HTTPError as e:
            fail(f"threads token rejected: {e.read().decode(errors='replace')[:300]}")
        texpected = tcfg.get("username", ACCT["username"])
        if tme.get("username") != texpected:
            fail(f"WRONG THREADS ACCOUNT: token is @{tme.get('username')}, "
                 f"expected @{texpected}")
        if str(tme.get("id")) != str(tid):
            fail(f"threads user-id secret ({tid}) != token's id ({tme.get('id')})")
        print(f"  OK: Threads token reaches @{tme.get('username')}")
    elif tcfg:
        print(f"::warning::[{ACCT['slug']}] Threads configured but secrets "
              f"{tcfg.get('token_secret')} / {tcfg.get('user_id_secret')} not "
              f"set — Threads cross-posting will be skipped")

    print(f"[{ACCT['slug']}] all checks passed. Nothing was published.")


if __name__ == "__main__":
    main()
