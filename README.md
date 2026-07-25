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

## Deployment status — NOT YET LIVE (as of 2026-07-25)

The daily cron is **disabled**. No secrets are set. Nothing publishes until the
Instagram link below is completed.

Done:
- Repo public, queue validated (14 posts, 83 slides)
- Meta app `leverage-ai-publisher-2` with permissions `instagram_basic`,
  `instagram_content_publish`, `pages_show_list`, `business_management`,
  `pages_read_engagement`
- Facebook Page **Leverage AI** created — Page ID `1132549139952111`

Blocked on one thing:
- `@leverageai.daily` (registered to `vbolinches+leverageai@gmail.com`) sits in a
  **different Meta Accounts Center** than the Facebook account that owns the
  Leverage AI Page. The Page shows the Instagram account as connected, but only
  at profile level — `instagram_business_account` does not populate, so the
  Content Publishing API cannot see it.
- Fix: from the Instagram app, switch `@leverageai.daily` to a **Business**
  account and connect it to the **Leverage AI** Page, authorising with the
  Instagram account's own credentials. Then `IG_USER_ID` =
  `GET /1132549139952111?fields=instagram_business_account`.

To re-enable once credentials verify:
`gh workflow enable "Publish daily Instagram post"`

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
