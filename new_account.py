#!/usr/bin/env python3
"""Scaffold a new Instagram account into the multi-account system.

Creates accounts/<slug>/ with its config, an empty queue, and a starter
strategy file, then prints the exact manual steps that no script can do
(Meta app roles, token minting, GitHub secrets). Once those are done and the
verify workflow passes, every existing workflow — publish, monitor, generate,
queue health, token refresh — picks the account up automatically: they all
matrix over accounts/*/account.json.

    python new_account.py fitcoach \
        --username fitcoach.daily \
        --theme "one evidence-based fitness habit a day for busy professionals" \
        --wordmark "FITCOACH"

The account is created DISABLED so a half-configured account can never
publish. Enable it (set "enabled": true) after the verify workflow is green.
"""
import argparse, io, json, os, re, sys

TEMPLATE_VOICE = ("Direct, concrete, no hype. Short sentences. Second person. "
                  "Always give something actionable. Be honest about "
                  "limitations and trade-offs.")

STRATEGY_SEED = """# Strategy — {handle}

## Working hypotheses (unproven)

- The theme ({theme}) has an audience on Instagram discovery surfaces.
- Reels reach strangers; carousels serve whoever follows. Alternate them.
- Saves and shares matter; likes are near-noise.

## Confirmed

(nothing yet — needs published posts and real engagement data)

## Disproven

(nothing yet)
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", help="short identifier, e.g. fitcoach")
    ap.add_argument("--username", required=True,
                    help="Instagram username, without @")
    ap.add_argument("--theme", required=True,
                    help='what the account publishes, e.g. "one X a day for Y"')
    ap.add_argument("--wordmark", default=None,
                    help="header wordmark on slides (default: slug upper-cased)")
    ap.add_argument("--voice", default=TEMPLATE_VOICE)
    a = ap.parse_args()

    if not re.fullmatch(r"[a-z][a-z0-9-]{1,30}", a.slug):
        sys.exit("slug must be lowercase letters/digits/hyphens, start with a letter")
    root = f"accounts/{a.slug}"
    if os.path.exists(root):
        sys.exit(f"{root} already exists")

    suffix = a.slug.upper().replace("-", "_")
    cfg = {
        "slug": a.slug,
        "username": a.username,
        "ig_user_id": "SET-AFTER-TOKEN-MINT",
        "token_secret": f"IG_TOKEN_{suffix}",
        "user_id_secret": f"IG_USER_ID_{suffix}",
        "enabled": False,
        "wordmark": a.wordmark or a.slug.upper(),
        "theme": a.theme,
        "voice": a.voice,
        "cta_line": f"Follow @{a.username} — {a.theme}.",
        "search_brief": (f"what is current and changing in this space: {a.theme} — "
                         "recent developments, tools, findings, and anything that "
                         "would date a post or make it wrong"),
        "colors": {},
        # Content-ideas monitor (ideas.py). rss + hn_queries are free;
        # x_accounts needs the shared X_BEARER_TOKEN secret (pay-per-use).
        "idea_sources": {"x_accounts": [], "rss": [], "hn_queries": []},
        # One automated reply to inbound DMs (dm_responder.py). Needs the
        # messaging permission + App Review; text must never promise a human.
        "dm_welcome": {"enabled": False, "text": ""},
    }

    os.makedirs(f"{root}/queue", exist_ok=True)
    os.makedirs(f"{root}/specs", exist_ok=True)
    with io.open(f"{root}/account.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    with io.open(f"{root}/queue/schedule.json", "w", encoding="utf-8") as f:
        json.dump({"posts": []}, f, indent=2)
        f.write("\n")
    with io.open(f"{root}/strategy.md", "w", encoding="utf-8") as f:
        f.write(STRATEGY_SEED.format(handle=f"@{a.username}", theme=a.theme))

    print(f"scaffolded {root}/ (disabled)\n")
    print("Manual steps — none of these can be scripted:\n")
    print(f" 1. Create @{a.username} as an Instagram PROFESSIONAL account "
          "(Business/Creator) in the app.")
    print(" 2. Meta app dashboard -> leverage-ai-publisher -> App roles: add "
          f"@{a.username} as an Instagram Tester, then accept the invite from "
          "the account (Settings -> Website permissions -> Apps and websites).")
    print(" 3. Dashboard -> Use cases -> API setup with Instagram login -> "
          f"Generate token next to @{a.username}. NEVER paste the token here — "
          "set it directly:")
    print(f"      gh secret set {cfg['token_secret']}")
    print(f"      gh secret set {cfg['user_id_secret']}   # the numeric user id "
          "shown beside the account")
    print(f" 4. Put that same numeric id in {root}/account.json (ig_user_id), "
          "and record the token dates in a new "
          f"{root}/token_status.json ({{\"minted\": ..., \"expires\": ...}}).")
    print(f" 5. Fill the queue: ANTHROPIC_API_KEY=... ACCOUNT={a.slug} "
          "python generate_batch.py --count 7 --dry-run  (review, then re-run "
          "without --dry-run), or drop in hand-made posts.")
    print(f" 6. Set \"enabled\": true in {root}/account.json, commit, push.")
    print(" 7. Run the 'Verify Instagram credentials' workflow and confirm the "
          f"'{a.slug}' job is green before the next 10:00 UTC publish.")
    print("\nThe daily publish, monitor, queue-health, generate and token-refresh "
          "workflows all pick the account up automatically once it is enabled.")


if __name__ == "__main__":
    main()
