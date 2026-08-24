#!/usr/bin/env python3
"""Auto-reply once to new inbound DMs, per account.

What this can and cannot do is set by Meta, not by us:

- The API can only REPLY: a user must message the account first, and the reply
  must go out within 24 hours of their message. It cannot start a conversation.
- The DM-on-follow seen from big accounts ("X messaged you because you followed
  their account") is a Meta-partner feature (ManyChat "Follow to DM", beta,
  ~1000-follower eligibility). It does not exist in the public API at any price.
- Messaging the general public needs the instagram_business_manage_messages
  permission with Advanced Access (Meta App Review). In development mode the
  API only serves app-role users, so this script stays harmlessly inert until
  those gates are passed — it warns and exits 0 rather than failing the cron.

Behaviour: for each conversation where the account has NEVER sent a message,
and the other side's latest message is inside the 24h window, send the
account's configured welcome once. Stateless by design — "have we ever sent
anything in this thread" is derived from the thread itself, so there is no
state file to commit and a re-run can never double-send.

The welcome text must follow the same rule as captions (CLAUDE.md #3): never
promise a human reply. It IS the reply.

    python dm_responder.py            # env: IG_ACCESS_TOKEN, IG_USER_ID
    python dm_responder.py --dry-run  # report what would be sent
"""
import argparse, json, os, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

import accounts

GRAPH = "https://graph.instagram.com/v21.0"
TOKEN = os.environ.get("IG_ACCESS_TOKEN")
IG_ID = os.environ.get("IG_USER_ID")
ACCT = accounts.get()

MAX_SENDS_PER_RUN = 20   # safety valve, far under Meta's 200/hour cap


def get(path, **params):
    params["access_token"] = TOKEN
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url) as r:
        return json.load(r)


def send_text(igsid, text):
    body = json.dumps({"recipient": {"id": igsid},
                       "message": {"text": text}}).encode()
    req = urllib.request.Request(
        f"{GRAPH}/me/messages?access_token={urllib.parse.quote(TOKEN)}",
        data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def assert_target():
    me = get("me", fields="user_id,username")
    if me.get("username") != ACCT["username"] \
            or str(me.get("user_id")) != str(ACCT["ig_user_id"]):
        raise RuntimeError(
            f"WRONG ACCOUNT: token is @{me.get('username')} "
            f"({me.get('user_id')}), account {ACCT['slug']!r} expects "
            f"@{ACCT['username']} ({ACCT['ig_user_id']}). Refusing to send.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    # Facebook-Login-route accounts read DMs via the Page inbox — different
    # endpoints, not implemented. Skip cleanly rather than half-work.
    if ACCT.get("api", "instagram_login") != "instagram_login":
        print(f"[{ACCT['slug']}] api={ACCT.get('api')} — DM responder supports "
              f"the instagram_login route only; skipping")
        return 0

    cfg = ACCT.get("dm_welcome") or {}
    if not cfg.get("enabled"):
        print(f"[{ACCT['slug']}] dm_welcome disabled in account.json — nothing to do")
        return 0
    text = (cfg.get("text") or "").strip()
    if not text:
        print(f"::error::[{ACCT['slug']}] dm_welcome.enabled but no text set")
        return 1

    if not TOKEN or not IG_ID:
        sys.exit("IG_ACCESS_TOKEN and IG_USER_ID must be set")

    # While the account is action-blocked (see publish.py), every automated
    # call is activity that can extend the block. Stay completely quiet.
    if os.path.exists(ACCT.path("block_status.json")):
        print(f"::warning::[{ACCT['slug']}] account is in action-block "
              f"cool-down — skipping DM polling until publishing recovers")
        return 0

    assert_target()

    try:
        convs = get("me/conversations", platform="instagram",
                    fields="id,updated_time,messages.limit(25){from,created_time}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        # The expected state until the messaging permission + App Review exist:
        # stay quiet-but-visible instead of failing the cron every 2 hours.
        if e.code in (400, 403) or "permission" in detail.lower() or "OAuth" in detail:
            print(f"::warning::[{ACCT['slug']}] cannot read conversations — the "
                  f"token lacks instagram_business_manage_messages (or the app "
                  f"lacks Advanced Access / App Review for it). The responder "
                  f"is armed and will start working once that is granted. "
                  f"API said: {detail}")
            return 0
        raise

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    sent = 0
    for conv in convs.get("data", []):
        msgs = (conv.get("messages") or {}).get("data", [])
        if not msgs:
            continue
        mine = ACCT["username"]
        if any((m.get("from") or {}).get("username") == mine for m in msgs):
            continue        # we already spoke in this thread — welcome only once

        latest = msgs[0]    # newest first
        who = latest.get("from") or {}
        when = latest.get("created_time", "")
        try:
            ts = datetime.strptime(when, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            continue
        if ts < cutoff:
            continue        # outside Meta's reply window; a send would be rejected

        if sent >= MAX_SENDS_PER_RUN:
            print(f"::warning::hit {MAX_SENDS_PER_RUN}-send cap; rest next run")
            break

        # Never print sender usernames: this log is public (public repo), and
        # who DMs an account like this can itself be sensitive information.
        if a.dry_run:
            print(f"  would welcome 1 conversation ({when})")
        else:
            try:
                send_text(who["id"], text)
                print("  welcomed 1 conversation")
            except urllib.error.HTTPError as e:
                print(f"::warning::a send failed: "
                      f"{e.read().decode(errors='replace')[:200]}")
                continue
        sent += 1

    print(f"[{ACCT['slug']}] {sent} welcome(s) {'planned' if a.dry_run else 'sent'}, "
          f"{len(convs.get('data', []))} conversation(s) checked")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
