# leverage-ai-content — operating notes

Autonomous Instagram publishing for **@leverageai.daily**. Deployed and running
since 2026-07-25. This file replaces the original deployment handoff, which
described a setup that turned out not to match reality.

Repo: https://github.com/vbolinches/leverage-ai-content (public — Meta fetches
slide images from raw URLs, which only works on a public repo on `main`).

## Read this before changing anything

**1. The wrong-account hazard is real.** The Facebook account behind this app
also administers **@inmigraforma**, an unrelated live business with a real
audience. During setup the token could reach *only* that account. Publishing 14
AI-productivity carousels there would have been unrecoverable.

- Correct target: `IG_USER_ID = 17841443853596707` = `@leverageai.daily`
- Wrong target: `17841464133054122` = `@inmigraforma`

`publish.py`, `monitor.py` and the verify workflow all assert the username. Do
not weaken those guards.

**2. This uses the Instagram Login API, not the Facebook Page API.**
`publish.py` targets `graph.instagram.com`. The Page-based route
(`graph.facebook.com` + `instagram_business_account`) **cannot work here**:
@leverageai.daily lives in a different Meta Accounts Center from the Facebook
account, so the Page link only ever forms at profile level and
`instagram_business_account` never populates. A Facebook Page named "Leverage AI"
(`1132549139952111`) exists from that attempt and is unused.

`SETUP.md` documents the abandoned route and is retained only as history.

**3. Nobody is watching the inbox.** Captions must never promise a reply,
template or DM — anyone who took it up would get silence. `generate_batch.py`
enforces this in both the brand prompt and the validator.

**4. Running out of content fails silently.** `publish.py` exits 0 with
"nothing due" by design, so the cron never fails spuriously. The queue-health
workflow exists to make that failure loud. Keep it.

**5. Reach is the constraint, not content quality.** Measured 2026-07-25:
post01 got **reach 1** — one person saw it. Carousels are shown mostly to
existing followers and there are none, so the content-optimising feedback loop
cannot help; it has nothing to learn from. The queue therefore alternates
carousels with **Reels**, which are the discovery surface. Judge the account on
reach per post, not likes. If reach is still single digits by 2026-08-07, the
answer is more Reels or an audience transfer — not better carousels.

**6. Reels cannot use trending audio, and never will through this pipeline.**
Meta's Content Publishing API exposes no `audio_id` or music-library parameter —
audio must be embedded in the MP4 before upload. This is a platform limit that
affects every third-party scheduler, confirmed against Meta's docs 2026-07-25.

`audio.py` therefore synthesises an original bed per post (numpy; seeded from the
slug, so reruns are reproducible). Silent Reels get suppressed, so this is not
cosmetic. Do not replace it with a licensed track — Instagram detects and mutes
or strikes those, and the repo is public. **If a Reel genuinely needs a trending
sound, it must be posted by hand in the app.** After any renderer or audio
change, run `python build_reels.py --rebuild` and re-run the verify workflow.

## Layout

| Path | Purpose |
|---|---|
| `queue/schedule.json` | The queue: id, date, slides, caption, status |
| `queue/postNN-*/` | Slide PNGs |
| `specs/*.json` | Post content specs, rendered into slides |
| `publish.py` | Publishes the oldest due post; marks it published |
| `render_slides.py` | Spec → branded 1080×1350 slides |
| `render_reel.py` | Spec or slides → 1080×1920 MP4 Reel, with audio |
| `audio.py` | Synthesises the Reel music bed (original, per-slug) |
| `build_reels.py` | Converts alternate queued posts to Reels |
| `generate_batch.py` | Authors a batch with Claude, renders, queues |
| `monitor.py` | Read-only digest + token expiry warning |
| `token_status.json` | Token expiry dates (no secret) |

## Workflows

| Workflow | When | Notes |
|---|---|---|
| Publish daily Instagram post | 10:00 UTC daily | The core job |
| Account monitor | 08:00 UTC daily | Digest; fails at <10 days token runway |
| Queue health check | Mon 09:00 UTC | Fails below 5 queued posts |
| Generate content batch | Wed 06:00 UTC | Only runs when queue < 7 |
| Refresh Instagram token | 1st monthly | **Blocked** — needs a valid `GH_PAT` |
| Verify Instagram credentials | manual | Run after any token change |

## Secrets

| Secret | State |
|---|---|
| `IG_ACCESS_TOKEN` | set; expires 2026-09-22 |
| `IG_USER_ID` | set |
| `ANTHROPIC_API_KEY` | set |
| `GH_PAT` | **invalid** — rejected 401; value is not a GitHub token |

## Known gaps

- **Token auto-refresh is blocked** on `GH_PAT`. Until fixed, refresh by hand
  (README has the steps) and **update `token_status.json`**, or the warning
  fires against a stale date.
- **Insights** (reach, impressions, saves) need
  `instagram_business_manage_insights` added to the app; `monitor.py` degrades
  gracefully without it.
- **The app is in development mode.** Fine for a Tester-role account, but if
  publishing ever fails on permissions, App Review is the cause.
- **Generated posts publish unreviewed** on the Wednesday schedule. `--dry-run`
  plus the artifact is the review path.

## Conventions

- Write `schedule.json` with `json.dump(..., indent=2, ensure_ascii=False)` —
  matches what the publish workflow commits, keeps diffs clean.
- Slides render with DejaVu on every platform so local previews match CI.
- Never commit a token. `token_status.json` holds dates only.
