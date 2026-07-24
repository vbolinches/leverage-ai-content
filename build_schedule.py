#!/usr/bin/env python3
"""Build queue/schedule.json from the produced week1+week2 post specs.
Run from the ig-business root. Re-run any time new batches are added."""
import json, glob, datetime, os

posts = []
start = datetime.date(2026, 7, 27)
files = sorted(glob.glob("03-content/week1/post*.json")) + \
        sorted(glob.glob("03-content/week2/post*.json"))
for i, f in enumerate(files):
    d = json.load(open(f))
    slug = d["slug"]
    slides = sorted(os.listdir(f"07-automation/queue/{slug}"))
    posts.append({
        "id": slug,
        "date": (start + datetime.timedelta(days=i)).isoformat(),
        "slides": [f"queue/{slug}/{s}" for s in slides],
        "caption": d["caption"],
        "status": "queued",
    })
json.dump({"posts": posts}, open("07-automation/queue/schedule.json", "w"),
          indent=2, ensure_ascii=False)
print("schedule.json:", len(posts), "posts,",
      posts[0]["date"], "→", posts[-1]["date"])
