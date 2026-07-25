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
import json, os, sys, time, urllib.parse, urllib.request
from datetime import date

# Instagram API with Instagram Login (graph.instagram.com) — publishes direct to
# an Instagram professional account, no Facebook Page link required.
GRAPH = "https://graph.instagram.com/v21.0"
TOKEN = os.environ["IG_ACCESS_TOKEN"]
IG_ID = os.environ["IG_USER_ID"]
RAW_BASE = os.environ.get("RAW_BASE")  # e.g. https://raw.githubusercontent.com/<user>/<repo>/main
QUEUE = "queue/schedule.json"

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
    sched = json.load(open(QUEUE))
    today = date.today().isoformat()
    due = [p for p in sched["posts"] if p["status"] == "queued" and p["date"] <= today]
    if not due:
        print("nothing due today — queue ahead or empty"); return
    post = sorted(due, key=lambda p: p["date"])[0]

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
        json.dump(sched, open(QUEUE, "w"), indent=2, ensure_ascii=False)
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
    json.dump(sched, open(QUEUE, "w"), indent=2, ensure_ascii=False)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
