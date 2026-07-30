#!/usr/bin/env python3
"""
Leverage AI — autonomous Instagram publisher (official Graph API).

Runs daily via GitHub Actions cron. Publishes the next due post from
queue/schedule.json as a carousel (or single image), then marks it done.

Requirements (GitHub repo secrets):
  IG_ACCESS_TOKEN  — long-lived user access token (see SETUP.md)
  IG_USER_ID       — Instagram professional account ID

Design notes:
- Images must be publicly fetchable by Meta's servers: we use this repo's
  raw.githubusercontent.com URLs (repo must be public).
- Instagram API caps at 25 API-published posts/day; we publish 1/day.
- Exits 0 with "nothing due" when queue is empty/ahead of schedule, so the
  cron never fails spuriously. Any API error exits 1 → Actions email alert.
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import date

import accounts

ACCT = accounts.get()
QUEUE = ACCT.queue

# Two API routes exist because Meta's account topologies differ per account:
#
# instagram_login (default) — graph.instagram.com with an Instagram-Login
#   token. Needs no Facebook Page. Used by accounts whose Instagram sits in
#   its own Accounts Center (leverageai).
#
# facebook_page — graph.facebook.com with a Facebook-Login user token. Works
#   only when the Instagram account is business-linked to a Facebook Page the
#   token's user administers (inmigraforma). Same container/publish endpoints,
#   different host — and it can ALSO cross-post to the linked Facebook Page.
ROUTE = ACCT.get("api", "instagram_login")
GRAPH = ("https://graph.facebook.com/v21.0" if ROUTE == "facebook_page"
         else "https://graph.instagram.com/v21.0")
TOKEN = os.environ["IG_ACCESS_TOKEN"]
IG_ID = os.environ["IG_USER_ID"]
RAW_BASE = os.environ.get("RAW_BASE")  # e.g. https://raw.githubusercontent.com/<user>/<repo>/main


def _get(path, **params):
    params["access_token"] = TOKEN
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url) as r:
        return json.load(r)


def assert_target():
    """Refuse to publish unless token, secret, and account config all agree.

    The wrong-account hazard is real (see CLAUDE.md): both accounts are live
    businesses now, so a crossed secret pair would publish one brand's content
    to the other's audience — every publish re-proves the token's identity
    against the account directory it is about to publish from.
    """
    if ROUTE == "facebook_page":
        # A Facebook-Login token has no Instagram /me — ask the IG node
        # directly whether this token sees the expected username there.
        me = _get(str(ACCT["ig_user_id"]), fields="username")
        actual_user, actual_id = me.get("username"), str(me.get("id"))
    else:
        me = _get("me", fields="user_id,username")
        actual_user, actual_id = me.get("username"), str(me.get("user_id"))
    if actual_user != ACCT["username"]:
        raise RuntimeError(
            f"WRONG ACCOUNT: token resolves to @{actual_user}, but account "
            f"{ACCT['slug']!r} expects @{ACCT['username']}. Refusing to publish.")
    if actual_id != str(ACCT["ig_user_id"]) or str(IG_ID) != str(ACCT["ig_user_id"]):
        raise RuntimeError(
            f"WRONG ACCOUNT: ids disagree (token={actual_id}, "
            f"secret={IG_ID}, config={ACCT['ig_user_id']}). Refusing to publish.")


THREADS_GRAPH = "https://graph.threads.net/v1.0"
THREADS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN")
THREADS_ID = os.environ.get("THREADS_USER_ID")


def threads_text(post):
    """Compress an Instagram caption into Threads' 500-char limit.

    Keeps the opening paragraph (the substance) and, when present, the
    not-legal-advice disclaimer — that line is a content rule, not filler,
    so it survives truncation ahead of everything else.
    """
    paras = [p.strip() for p in post["caption"].split("\n") if p.strip()]
    first = paras[0] if paras else ""
    disclaimer = next((p for p in paras
                       if "asesoría legal" in p.lower()
                       or "asesoria legal" in p.lower()), "")
    text = first
    if disclaimer and disclaimer != first:
        text = text[:500 - len(disclaimer) - 2].rstrip()
        text += "\n\n" + disclaimer
    return text[:500]


def crosspost_threads(post):
    """Mirror the just-published post onto Threads. Best-effort, like the
    Page cross-post: the IG publish is already recorded, so failure warns.

    Carousels become a single-image post (the cover slide) — Threads is a
    text-first surface and six branded slides there read as spam. Reels go
    as native video.
    """
    if not THREADS_TOKEN or not THREADS_ID:
        threads_cfg = ACCT.get("threads") or {}
        if threads_cfg:
            print(f"::warning::[{ACCT['slug']}] threads configured but secrets "
                  f"{threads_cfg.get('token_secret')}/"
                  f"{threads_cfg.get('user_id_secret')} not set — skipping")
        return None
    try:
        me_url = (f"{THREADS_GRAPH}/me?fields=username"
                  f"&access_token={urllib.parse.quote(THREADS_TOKEN)}")
        with urllib.request.urlopen(me_url) as r:
            tuser = json.load(r).get("username")
        expected = (ACCT.get("threads") or {}).get("username", ACCT["username"])
        if tuser != expected:
            raise RuntimeError(f"threads token is @{tuser}, expected @{expected}")

        params = {"text": threads_text(post), "access_token": THREADS_TOKEN}
        if post.get("format") == "reel":
            params["media_type"] = "VIDEO"
            params["video_url"] = f"{RAW_BASE}/{urllib.parse.quote(post['video'])}"
        else:
            params["media_type"] = "IMAGE"
            params["image_url"] = f"{RAW_BASE}/{urllib.parse.quote(post['slides'][0])}"

        req = urllib.request.Request(f"{THREADS_GRAPH}/{THREADS_ID}/threads",
                                     data=urllib.parse.urlencode(params).encode(),
                                     method="POST")
        with urllib.request.urlopen(req) as r:
            cid = json.load(r)["id"]

        # Containers process async; video takes longer than images.
        for _ in range(30 if post.get("format") == "reel" else 6):
            with urllib.request.urlopen(
                    f"{THREADS_GRAPH}/{cid}?fields=status"
                    f"&access_token={urllib.parse.quote(THREADS_TOKEN)}") as r:
                status = json.load(r).get("status")
            if status == "FINISHED":
                break
            if status == "ERROR":
                raise RuntimeError(f"threads container {cid} failed")
            time.sleep(5)

        req = urllib.request.Request(
            f"{THREADS_GRAPH}/{THREADS_ID}/threads_publish",
            data=urllib.parse.urlencode({"creation_id": cid,
                                         "access_token": THREADS_TOKEN}).encode(),
            method="POST")
        with urllib.request.urlopen(req) as r:
            tid = json.load(r).get("id")
        print(f"  cross-posted to Threads (id {tid})")
        return True
    except Exception as e:
        detail = e.read().decode(errors="replace")[:300] \
            if isinstance(e, urllib.error.HTTPError) else str(e)
        print(f"::warning::Threads cross-post failed (Instagram post succeeded "
              f"and is recorded): {detail}")
        return False


def page_token():
    """The linked Facebook Page and a Page token derived from the user token.

    The Page is discovered from the link itself — the page among /me/accounts
    whose instagram_business_account is this account's IG — so a stale or
    mistyped page id in config can never redirect posts to another Page.
    """
    pages = _get("me/accounts",
                 fields="id,name,access_token,instagram_business_account")
    for p in pages.get("data", []):
        if (p.get("instagram_business_account") or {}).get("id") == str(ACCT["ig_user_id"]):
            want = str(ACCT.get("facebook_page_id") or p["id"])
            if p["id"] != want:
                raise RuntimeError(
                    f"page mismatch: linked page {p['id']} != config {want}")
            return p["id"], p["name"], p["access_token"]
    raise RuntimeError("no administered Facebook Page is linked to "
                       f"IG {ACCT['ig_user_id']} — cannot cross-post")


def crosspost_page(post):
    """Mirror the just-published IG post onto the linked Facebook Page.

    Best-effort by design: the IG publish already succeeded and is recorded,
    so a Page hiccup must not fail the run and re-publish tomorrow.
    """
    try:
        pid, pname, ptok = page_token()
        if post.get("format") == "reel":
            # file_url upload — publishes as a Page video post. (True FB
            # "Reels" need a resumable binary upload; not worth it yet.)
            data = urllib.parse.urlencode({
                "file_url": f"{RAW_BASE}/{urllib.parse.quote(post['video'])}",
                "description": post["caption"],
                "access_token": ptok,
            }).encode()
            req = urllib.request.Request(f"{GRAPH}/{pid}/videos",
                                         data=data, method="POST")
            with urllib.request.urlopen(req) as r:
                vid = json.load(r).get("id")
            print(f"  cross-posted video to Page {pname!r} (id {vid})")
        else:
            media = []
            for s in post["slides"]:
                data = urllib.parse.urlencode({
                    "url": f"{RAW_BASE}/{urllib.parse.quote(s)}",
                    "published": "false",
                    "access_token": ptok,
                }).encode()
                req = urllib.request.Request(f"{GRAPH}/{pid}/photos",
                                             data=data, method="POST")
                with urllib.request.urlopen(req) as r:
                    media.append(json.load(r)["id"])
                time.sleep(1)
            params = {"message": post["caption"], "access_token": ptok}
            for i, m in enumerate(media):
                params[f"attached_media[{i}]"] = json.dumps({"media_fbid": m})
            req = urllib.request.Request(f"{GRAPH}/{pid}/feed",
                                         data=urllib.parse.urlencode(params).encode(),
                                         method="POST")
            with urllib.request.urlopen(req) as r:
                fid = json.load(r).get("id")
            print(f"  cross-posted {len(media)} photos to Page {pname!r} (id {fid})")
        return True
    except Exception as e:
        detail = e.read().decode(errors="replace")[:300] \
            if isinstance(e, urllib.error.HTTPError) else str(e)
        print(f"::warning::Facebook Page cross-post failed (Instagram post "
              f"succeeded and is recorded): {detail}")
        return False

def api(path, params):
    data = urllib.parse.urlencode({**params, "access_token": TOKEN}).encode()
    req = urllib.request.Request(f"{GRAPH}/{path}", data=data, method="POST")
    with urllib.request.urlopen(req) as r:
        out = json.load(r)
    if "id" not in out:
        raise RuntimeError(f"API error on {path}: {out}")
    return out["id"]

def wait_ready(container_id, tries=20):
    """Poll container status until FINISHED (Meta processes async)."""
    for _ in range(tries):
        url = f"{GRAPH}/{container_id}?fields=status_code&access_token={TOKEN}"
        with urllib.request.urlopen(url) as r:
            status = json.load(r).get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"container {container_id} failed processing")
        time.sleep(5)
    raise RuntimeError(f"container {container_id} not ready after {tries} polls")

def main():
    sched = json.load(open(QUEUE, encoding="utf-8"))
    today = date.today().isoformat()
    due = [p for p in sched["posts"] if p["status"] == "queued" and p["date"] <= today]
    if not due:
        print(f"[{ACCT['slug']}] nothing due today — queue ahead or empty"); return
    post = sorted(due, key=lambda p: p["date"])[0]

    # Only hit the network guard when actually about to publish, so idle days
    # keep their exit-0 "nothing due" behaviour even during an API wobble.
    assert_target()

    # Reels are a separate media type and a separate discovery surface —
    # carousels mostly reach existing followers, Reels reach strangers.
    if post.get("format") == "reel":
        video_url = f"{RAW_BASE}/{urllib.parse.quote(post['video'])}"
        print(f"publishing {post['id']} (reel)")
        container = api(f"{IG_ID}/media", {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": post["caption"],
        })
        # Video transcoding takes far longer than image processing.
        wait_ready(container, tries=90)
        media_id = api(f"{IG_ID}/media_publish", {"creation_id": container})
        print(f"published: media id {media_id}")
        post["status"] = "published"
        post["published_media_id"] = media_id
        post["published_on"] = today
        if ROUTE == "facebook_page":
            post["facebook_page_posted"] = crosspost_page(post)
        threads_result = crosspost_threads(post)
        if threads_result is not None:
            post["threads_posted"] = threads_result
        json.dump(sched, open(QUEUE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        return

    slides = post["slides"]  # list of repo-relative paths
    urls = [f"{RAW_BASE}/{urllib.parse.quote(s)}" for s in slides]
    print(f"publishing {post['id']} ({len(urls)} slides)")

    if len(urls) == 1:
        container = api(f"{IG_ID}/media", {"image_url": urls[0], "caption": post["caption"]})
    else:
        children = []
        for u in urls:
            cid = api(f"{IG_ID}/media", {"image_url": u, "is_carousel_item": "true"})
            children.append(cid)
            time.sleep(2)
        for cid in children:
            wait_ready(cid)
        container = api(f"{IG_ID}/media", {
            "media_type": "CAROUSEL",
            "children": ",".join(children),
            "caption": post["caption"],
        })
    wait_ready(container)
    media_id = api(f"{IG_ID}/media_publish", {"creation_id": container})
    print(f"published: media id {media_id}")

    post["status"] = "published"
    post["published_media_id"] = media_id
    post["published_on"] = today
    if ROUTE == "facebook_page":
        post["facebook_page_posted"] = crosspost_page(post)
    threads_result = crosspost_threads(post)
    if threads_result is not None:
        post["threads_posted"] = threads_result
    json.dump(sched, open(QUEUE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
