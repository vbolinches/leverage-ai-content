# leverage-ai-content

Autonomous multi-account Instagram publishing engine. First account:
**@leverageai.daily**.

GitHub Actions publishes one queued post per account per day at
**10:00 UTC / 12:00 CEST** via the official Graph API. No machine needs to be
on. Each account lives in `accounts/<slug>/` with its own config, queue,
specs, strategy and token dates; every workflow matrixes over the enabled
accounts in `accounts/*/account.json`.

## Adding an account

```bash
python new_account.py <slug> --username <ig.username> --theme "one X a day for Y"
```

The scaffolder creates `accounts/<slug>/` **disabled** and prints the manual
steps (Instagram professional account, Tester role on the Meta app, token →
GitHub secrets, first content batch). Enable it in `account.json` only after
the *Verify Instagram credentials* workflow is green for that account —
disabled accounts are invisible to every workflow, so a half-configured
account can never publish.

This repo is **public by design**: Meta's servers fetch the slide images from
this repo's `raw.githubusercontent.com` URLs, which only works on a public
repo on branch `main`. The queue is visible to anyone — it is content that is
about to be public anyway.

## Layout

| Path | Purpose |
|---|---|
| `accounts/<slug>/account.json` | Account identity, secret names, brand voice |
| `accounts/<slug>/queue/schedule.json` | The queue: post id, date, slide paths, caption, status |
| `accounts/<slug>/queue/postNN-*/` | Slide images and reel for each post |
| `accounts/<slug>/specs/*.json` | Post specs (content) — rendered into slides |
| `accounts/<slug>/strategy.md` | Feedback-loop memory for that account |
| `accounts/<slug>/token_status.json` | Token expiry dates (no secret) |
| `accounts.py` | Account registry; feeds the workflow matrices |
| `new_account.py` | Scaffold a new account (created disabled) |
| `publish.py` | Picks the oldest due post, uploads it, marks it published |
| `render_slides.py` | Spec → branded 1080×1350 slide PNGs |
| `render_reel.py` | Spec or slide PNGs → 1080×1920 MP4 Reel |
| `build_reels.py` | Converts alternate queued posts into Reels |
| `performance.py` | Performance history + the brief that feeds generation |
| `generate_batch.py` | Authors the next batch with Claude, renders, queues it |
| `monitor.py` | Read-only account digest (see `MONITORING.md`) |
| `brand/avatar.png` | Profile picture |
| `build_schedule.py` | **Dead code** — points at `03-content/`, `07-automation/`, which don't exist here. Superseded by `generate_batch.py` |

## Workflows

| Workflow | Schedule | Does |
|---|---|---|
| Publish daily Instagram post | 10:00 UTC daily | Posts the next due carousel |
| Account monitor | 08:00 UTC daily | Read-only performance digest |
| Queue health check | Mondays 09:00 UTC | **Fails loudly** when < 5 posts remain |
| Refresh Instagram token | 1st monthly | Renews the 60-day token in place |
| Verify Instagram credentials | manual | Asserts the token maps to the right account |

## Carousels and Reels

The queue alternates the two. Carousels reach people who already follow the
account; Reels are Instagram's discovery surface and reach strangers. Alternating
puts half the schedule in front of new people **without raising posting
frequency**, which would add spam-pattern risk on a young account.

A queue entry with `"format": "reel"` carries a `"video"` path and publishes as
`media_type=REELS`; everything else publishes as a carousel. Reels are built from
the same spec as the carousel — one authored post, rendered for whichever surface
it is scheduled on, so nothing is published twice.

```bash
python render_reel.py specs/post15-example.json   # spec  -> reel.mp4
python build_reels.py --dry-run                   # preview queue conversion
python build_reels.py                             # convert alternates
python build_reels.py --rebuild                   # re-render existing reels
```

Video encoding uses `imageio-ffmpeg`, which ships its own ffmpeg binary — no
system install, and identical output locally and in CI.

### Audio

**Instagram's publishing API cannot attach trending or licensed audio.** There is
no `audio_id` parameter and Meta does not expose the music library to third
parties — every API-published Reel must carry its audio inside the MP4. This is a
platform limit, not a gap in this repo, and it affects all third-party
schedulers.

So `audio.py` synthesises an original bed per post: a slow minor loop, seeded
from the post slug, with a cue on each slide change. Every sample is computed
locally, so there is no licence, no attribution and no takedown surface. It
exists because silent Reels get suppressed — not because it competes with a
trending sound. It cannot.

```bash
python audio.py bed.wav --seconds 14 --slug post02-meeting-notes-prompt
```

**If a Reel ever needs a trending sound, it has to be posted by hand in the app.**
That is the only route Meta provides.

## Producing the next batch

```bash
export ANTHROPIC_API_KEY=...
python generate_batch.py --count 7 --dry-run   # author + render, review first
python generate_batch.py --count 7             # author, render, queue
```

`--dry-run` writes specs and slides without scheduling them — the intended way to
keep a human between the model and the audience. Without it, generated posts go
into the queue and publish unreviewed.

## Secrets (repo → Settings → Secrets and variables → Actions)

Per account, under the names recorded in its `account.json`:

- token secret (leverageai: `IG_ACCESS_TOKEN`) — long-lived Instagram user access token
- user-id secret (leverageai: `IG_USER_ID`) — Instagram professional account ID

Shared: `ANTHROPIC_API_KEY` (generation), `GH_PAT` (token auto-refresh).

## Deployment status (as of 2026-07-25)

The daily cron is **disabled** pending one remaining step. Nothing publishes yet.

**Which API this uses.** `publish.py` targets the **Instagram API with Instagram
Login** (`graph.instagram.com`), not the Facebook-Page-based API the original
handoff assumed. `@leverageai.daily` sits in a different Meta Accounts Center
than the Facebook account, so the Page-linked path could never populate
`instagram_business_account`. The Instagram Login API removes the Facebook Page
from the picture entirely — authorisation comes from the Instagram account.

Done:
- Repo public, queue validated (14 posts, 83 slides)
- Meta app `leverage-ai-publisher` (Instagram app `leverage-ai-publisher-IG`)
  with `instagram_business_basic` + `instagram_business_content_publish`
- `@leverageai.daily` accepted as an **Instagram Tester** on the app
- `IG_USER_ID` secret set to `17841443853596707` (verified `@leverageai.daily`)

Remaining:
1. App dashboard → Use cases → API setup with Instagram login → **Generate token**
   for `leverageai.daily`, then `gh secret set IG_ACCESS_TOKEN`
2. Run the **Verify Instagram credentials** workflow — it asserts the token
   resolves to `@leverageai.daily` and fails loudly otherwise
3. `gh workflow enable "Publish daily Instagram post"`

**A Facebook Page named "Leverage AI"** (ID `1132549139952111`) was created during
setup and is now unused. Harmless; delete it if you prefer.

**Wrong-account hazard.** This Facebook account also administers
`@inmigraforma` (IG ID `17841464133054122`), an unrelated live business. Always
confirm the target is `17841443853596707` / `@leverageai.daily` before publishing.

## Token expiry

- Token generated: **2026-07-25**
- **Expires: 2026-09-23** (60 days)
- **Refresh by: 2026-09-13** (10 days of headroom)

These dates are computed from the standard 60-day window, not read back from
Meta — `graph.instagram.com` exposes no token-debug endpoint, so the exact
expiry could not be confirmed programmatically. Treat 2026-09-23 as the latest
plausible expiry, not a guarantee.

**Auto-refresh is built but not working.** `refresh-token.yml` renews the token
monthly and writes it back to the secret, but it needs a valid `GH_PAT` (the
default `GITHUB_TOKEN` cannot write secrets). The stored PAT is currently
rejected with 401 — the value isn't a GitHub token. Fix that secret and the
workflow takes over; until then, refresh by hand.

**You will be warned in advance either way.** `token_status.json` records the
expiry date and the daily monitor warns at 21 days and fails (emailing you) at
10 days. **After refreshing by hand, update the dates in `token_status.json`
and commit** — otherwise the warning fires against a stale date.

To refresh (note: **not** the Access Token Debugger route in SETUP.md — that
belongs to the Facebook-Login API this repo no longer uses):

- App dashboard → `leverage-ai-publisher` → Use cases → Customize →
  **API setup with Instagram login** → **Generate token** next to
  `leverageai.daily`, then `gh secret set IG_ACCESS_TOKEN`
- Or refresh in place while the token is still valid:
  `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=<current>`

After refreshing, run the **Verify Instagram credentials** workflow.

## Ongoing operation

- **Daily:** nothing. The cron publishes on its own.
- **Weekly:** new content batches come from the Cowork session — drop the new
  `queue/postNN-*/` folders in and update `schedule.json`, then push.
- **Every ~50 days:** refresh the token (above).
- **On failure:** GitHub emails an Actions failure notification. Forward it for
  diagnosis; see the failure notes in `CLAUDE.md`.

## Rollback

Actions tab → "Publish daily Instagram post" → `...` → **Disable workflow**.
Posting reverts to manual immediately; the queue format works either way.
