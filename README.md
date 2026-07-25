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
| `publish.py` | Picks the oldest due post, uploads it, marks it published |
| `build_schedule.py` | Regenerates `schedule.json` from the queue folders |
| `.github/workflows/publish.yml` | Daily cron + manual "Run workflow" button |

## Secrets (repo → Settings → Secrets and variables → Actions)

- `IG_ACCESS_TOKEN` — long-lived Instagram user access token
- `IG_USER_ID` — Instagram professional account ID

## Token expiry

Long-lived tokens last **60 days**. Refresh with ~10 days of headroom.

- Token generated: `TBD — fill in on the day the token is created`
- **Expires: TBD (generation date + 60 days)**
- **Refresh by: TBD (generation date + 50 days)**

To refresh: SETUP.md Part 2 step 4 (Access Token Debugger → Extend Access
Token), then update the `IG_ACCESS_TOKEN` secret. Takes about two minutes.

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
