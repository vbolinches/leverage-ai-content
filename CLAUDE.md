# HANDOFF — instructions for Claude Code (local agent)

You are finishing the deployment of a fully-built Instagram automation for
the account @leverageai.daily, owned by Vicente. Everything in this folder is
final and tested — your job is DEPLOYMENT ONLY, not redesign. Do not modify
publish.py, the workflow, or the queue except where these steps say so.

## Context
- This folder is the complete contents of a GitHub repo to be named
  `leverage-ai-content` (must be PUBLIC — Meta fetches images from raw URLs).
- `.github/workflows/publish.yml` posts 1 queued post/day at 10:00 UTC via
  Instagram Graph API. `queue/schedule.json` holds 14 posts (2026-07-27 → 08-09).
- Dated posts in the past publish on next run (by design, oldest first, one per run).

## Your steps

1. **Preflight:** verify `git` and `gh` are installed (`gh --version`). If gh
   is not authenticated, run `gh auth login` and let Vicente complete the
   browser login. Scope must include repo + workflow.

2. **Create and push the repo** from this folder as repo root:
   - `git init && git add . && git commit -m "Leverage AI content engine v1"`
   - `gh repo create leverage-ai-content --public --source=. --push`
   - Verify `.github/workflows/publish.yml` exists at that path in the repo.

3. **Meta token (Vicente does this part in his browser — guide him):**
   Walk him through SETUP.md Part 2 step by step (developers.facebook.com →
   Business app → Graph API Explorer → permissions instagram_basic,
   instagram_content_publish, pages_show_list, business_management →
   generate token → extend to long-lived → get IG user ID via
   me/accounts → page id → instagram_business_account).
   PRECONDITION he may still need: the IG account must be linked to a
   Facebook Page (business.facebook.com). If me/accounts returns empty,
   do that first.
   Ask him to paste the long-lived token and IG user ID to you.
   NEVER write the token to any file, commit, or log — secrets go only into
   the gh secret commands below.

4. **Set secrets:**
   - `gh secret set IG_ACCESS_TOKEN` (paste value when prompted)
   - `gh secret set IG_USER_ID` (paste value)

5. **Test fire:** `gh workflow run "Publish daily Instagram post"` then
   `gh run watch`. Success = log line "published: media id ...". Ask Vicente
   to confirm post01 (6-slide carousel, "Stop writing emails from scratch")
   is live on @leverageai.daily.

6. **Post-deploy checks:**
   - `queue/schedule.json` on main should now show post01 status "published"
     (the workflow commits this; `git pull` to confirm).
   - Record today's date + 60 days as TOKEN EXPIRY in the repo README
     (create a short README.md noting: what this repo is, token expiry date,
     and "weekly batches come from the Cowork session").
   - Confirm the cron is enabled (Actions tab, workflow not disabled).

7. **Report back** to Vicente: repo URL, first-post link, token expiry date,
   and remind him the cron now runs daily at 12:00 CEST with no further action.

## Failure notes
- API error "media type unsupported / image fetch failed": raw URL not
  public — repo must be public, files on branch `main`.
- (#10) permission error: token missing instagram_content_publish or the
  IG account isn't linked to the Page the token can see.
- Rate/limit errors: we publish 1/day, far under the 25/day cap — if you see
  a limit error, something else is wrong; stop and report, don't retry-loop.
