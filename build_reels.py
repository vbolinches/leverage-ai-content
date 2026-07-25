#!/usr/bin/env python3
"""Convert alternate queued posts into Reels.

Reach on this account is ~1 per carousel: Instagram shows carousels mostly to
people who already follow you, and there aren't any. Reels are the discovery
surface. This flips every other queued post to a Reel so half the schedule
targets strangers, without raising posting frequency (which would add
spam-pattern risk on a young account).

Converts rather than duplicates — publishing the same content as both a carousel
and a Reel would read as duplicate content.

    python build_reels.py            # convert alternates, render the videos
    python build_reels.py --dry-run  # show what would change
"""
import argparse, io, json, os, sys

import render_reel

QUEUE = "queue/schedule.json"


def load():
    with io.open(QUEUE, encoding="utf-8") as f:
        return json.load(f)


def save(sched):
    with io.open(QUEUE, "w", encoding="utf-8") as f:
        json.dump(sched, f, indent=2, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--every", type=int, default=2,
                    help="convert every Nth queued post (default 2 = alternate)")
    ap.add_argument("--rebuild", action="store_true",
                    help="re-render reels that already exist (use after a "
                         "renderer or audio change)")
    a = ap.parse_args()

    sched = load()
    queued = [p for p in sched["posts"] if p["status"] == "queued"]
    if not queued:
        sys.exit("nothing queued")

    targets = ([p for p in queued if p.get("format") == "reel"] if a.rebuild
               else [p for i, p in enumerate(queued) if i % a.every == 0])
    verb = "re-rendering" if a.rebuild else "converting"
    print(f"{len(queued)} queued -> {verb} {len(targets)} reels\n")

    converted = 0
    for p in targets:
        if p.get("format") == "reel" and not a.rebuild:
            print(f"  {p['id']}: already a reel")
            continue
        slides = [s for s in p.get("slides", []) if os.path.exists(s)]
        if len(slides) < 2:
            print(f"  {p['id']}: SKIP — {len(slides)} slide images found")
            continue

        if a.dry_run:
            print(f"  {p['id']} ({p['date']}): would render {len(slides)} slides")
            continue

        path, secs = render_reel.render_from_images(p["id"], slides)
        p["format"] = "reel"
        p["video"] = path
        converted += 1
        size = os.path.getsize(path) / 1e6
        print(f"  {p['id']} ({p['date']}): {secs:.1f}s reel, {size:.1f} MB")

    if a.dry_run:
        print("\ndry run — queue unchanged")
        return 0

    save(sched)

    remaining = [p for p in sched["posts"] if p["status"] == "queued"]
    reels = sum(1 for p in remaining if p.get("format") == "reel")
    print(f"\nconverted {converted}. Queue now {len(remaining)} posts: "
          f"{reels} reels / {len(remaining) - reels} carousels, alternating.")
    print(f"next up: {remaining[0]['id']} on {remaining[0]['date']} "
          f"({remaining[0].get('format', 'carousel')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
