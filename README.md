# leverage-ai-content

Autonomous publishing engine for **@leverageai.daily**.

GitHub Actions publishes one queued post per day at **10:00 UTC / 12:00 CEST**
to Instagram via the official Graph API. No machine needs to be on.

This repo is **public by design**: Meta's servers fetch the slide images from
this repo's `raw.githubusercontent.com` URLs, which only works on a public
repo on branch `main`. The queue is visible to anyone — it is content that is
about to be public anyway.

## Layout

| Path | Purpose |
|---|---|
| `queue/schedule.json` | The queue: post id, date, slide paths, caption, status |
| `queue/postNN-*/` | Slide images for each post |
| `specs/*.json` | Post specs (content) — rendered into slides |
| `publish.py` | Picks the oldest due post, uploads it, marks it published |
| `render_slides.py` | Spec → branded 1080×1350 slide PNGs |
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

- `IG_ACCESS_TOKEN` — long-lived Instagram user access token
- `IG_USER_ID` — Instagram professional account ID

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
