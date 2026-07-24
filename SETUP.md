# Full Automation Setup — one time, ~45 min, then zero-touch daily posting

After this setup, posting is fully autonomous: GitHub's servers publish one
post per day at 12:00 CEST via Instagram's OFFICIAL API. No laptops open, no
sessions running, no weekly paste-work. This is the compliant version of
automation — Meta explicitly supports API publishing for professional
accounts (25/day cap; we use 1).

## Prerequisites (already true)
- @leverageai.daily is a professional (Creator) account ✅
- It must be linked to a Facebook Page — if you haven't done the Business
  Suite link yet, do it first (business.facebook.com → add the IG account;
  create Page "Leverage AI" when prompted).

## Part 1 — GitHub repo (10 min)
1. Create a GitHub account if needed → New repository → name: `leverage-ai-content`,
   visibility: **Public** (required: Meta's servers fetch images from the
   repo's raw URLs). Note: the queue is visible to anyone who looks — it's
   content that's about to be public anyway.
2. Upload the CONTENTS of `07-automation/` (publish.py, queue/, .github/) to
   the repo root. Easiest: GitHub web UI → "uploading an existing file" →
   drag the whole folder contents. Make sure `.github/workflows/publish.yml`
   lands at exactly that path.

## Part 2 — Meta developer app + token (25 min)
1. developers.facebook.com → My Apps → Create App → type "Business".
2. Add product: none needed beyond default Graph API access for your own account.
3. Tools → Graph API Explorer:
   - App: yours. User token → add permissions:
     `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `business_management`.
   - Generate Access Token (log in, approve).
4. Make it long-lived (60 days): Tools → Access Token Debugger → paste token
   → "Extend Access Token". Copy the extended token.
5. Get your IG user ID: in Graph API Explorer run
   `GET me/accounts` → copy your Page's `id`, then
   `GET {page-id}?fields=instagram_business_account` → copy the number. That's IG_USER_ID.

## Part 3 — Wire the secrets (5 min)
Repo → Settings → Secrets and variables → Actions → New repository secret:
- `IG_ACCESS_TOKEN` = the long-lived token
- `IG_USER_ID` = the number from Part 2 step 5

## Part 4 — Test fire (2 min)
Repo → Actions tab → "Publish daily Instagram post" → Run workflow.
Watch the log: it should publish post01 immediately (it's dated in the past
by design). Check @leverageai.daily — the first carousel should be live.
From then on the cron handles one post per day, in calendar order.

## Ongoing operation
- **Daily:** nothing. GitHub publishes at 12:00 CEST.
- **Weekly:** Claude produces the next batch → you drag the new `queue/postXX-*/`
  folders + updated `schedule.json` into the repo (2 min via web UI). Claude
  regenerates schedule.json for you each time (build_schedule.py).
- **Every ~50 days:** the long-lived token expires at 60 days. Re-run Part 2
  step 4 (2 min) and update the secret. Claude's weekly check-in tracks the
  expiry date and reminds you in advance.
- **Failure alerts:** if a publish fails, GitHub emails you automatically
  (Actions failure notification). Forward it to Claude for diagnosis.

## What stays manual forever (and why)
- Token refresh (~2 min / 2 months): Meta requires the account owner.
- Comments/DMs: the page still needs engagement — Claude drafts, you send.
- Stories: not supported for API publishing on this plan; optional, manual.

## Rollback
Disable the workflow (Actions → workflow → "..." → Disable) and you're back
to manual/Business Suite posting instantly. The queue format works for both.
