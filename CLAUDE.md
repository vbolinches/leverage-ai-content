# leverage-ai-content — operating notes

Autonomous Instagram publishing, multi-account. Deployed and running since
2026-07-25 with **@leverageai.daily** as the first account. This file replaces
the original deployment handoff, which described a setup that turned out not to
match reality.

Every account lives in `accounts/<slug>/` — config, queue, specs, strategy,
token dates, performance history. Scripts select one account via the `ACCOUNT`
env var (automatic while only one is enabled); workflows matrix over
`accounts.py --list-json`. To add an account: `python new_account.py <slug>
--username ... --theme ...` and follow the printed checklist. New accounts are
created disabled and stay invisible to every workflow until `"enabled": true`.

Repo: https://github.com/vbolinches/leverage-ai-content (public — Meta fetches
slide images from raw URLs, which only works on a public repo on `main`).

## Read this before changing anything

**1. The wrong-account hazard is real — and both accounts are live now.**
`@inmigraforma` (IG `17841464133054122`) was originally the account these
guards protected *against*: an unrelated live business the setup token could
reach by accident. As of 2026-07 it is **tenant two** — a bilingual US
immigration news account with a real audience. That makes the hazard
symmetric: crossed secrets would publish AI-productivity content to an
immigration audience or vice versa, both live, both real.

- `17841443853596707` = `@leverageai.daily` (AI workflows, English)
- `17841464133054122` = `@inmigraforma` (immigration news, ES/EN)

Each `account.json` records the expected username AND ig_user_id, and
`publish.py` re-proves the live token against both before every publish
(`assert_target()`); `monitor.py` and the verify workflow assert the same.
Multiple accounts make crossed secrets *more* likely, not less — do not weaken
these guards, and never reuse one account's secret names for another.

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
enforces this in both the brand prompt and the validator. The DM auto-responder
(point 8) does not change this rule: it sends one generic welcome and must
itself never promise a further reply.

**4. Running out of content fails silently.** `publish.py` exits 0 with
"nothing due" by design, so the cron never fails spuriously. The queue-health
workflow exists to make that failure loud. Keep it.

**5. Reach is the constraint, and carousels do not reach anyone.** Settled on
data 2026-09-07, independently on both accounts:

| Account | Carousel reach (median) | Reel reach (median) |
|---|---|---|
| leverageai | 2 (n=6, range 1-2) | 40 (n=8, range 6-108) |
| inmigraforma | 3 (n=6, range 2-7) | 22 (n=6, range 2-59) |

Both accounts are therefore **Reels-only** (`"reel_ratio": "all"`). Do not
reintroduce carousels while an account is still in discovery: a carousel slot
is a publishing day that reaches nobody. Revisit only once an account has a
real audience to serve. Judge posts on reach, not likes.

**5b. Reel length is the retention lever, and it is capped by reading time.**
At the deliberately slow CHAR_RATE (11 chars/sec, owner's call — see point 6),
a 30-second Reel holds only ~280 characters of slide text. Reels ran 67s
(leverageai) and 77s (inmigraforma) because the slide limits were sized for
canvas overflow, not for reading time. Both accounts now carry
`reel_target_seconds` and a per-account `slide_limits` budget that
`validate()` enforces against the whole post, so a long slide can no longer
silently ship a minute-long Reel. Detail that does not fit goes in the
**caption** — that is also the only place a viewer can copy a prompt or open a
source URL.

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

**9. Instagram can action-block an account (error 4 / subcode 2207051).**
It is an anti-spam block on the account, not an API failure, and daily retries
extend it. `publish.py` records it in `accounts/<slug>/block_status.json`,
skips attempts during a growing cool-down (2..5 days), silences the DM
responder meanwhile, and clears the file on the first successful publish. The
monitor digest surfaces it daily. Only an in-app appeal (Account Status)
lifts a block faster — that is the owner's job, not the pipeline's.

**8. DM automation is reply-only, and DM-on-follow is not an API feature.**
Meta's API can only reply within 24h of an inbound message
(`instagram_business_manage_messages`, Advanced Access via App Review — both
still missing here, so `dm_responder.py` is armed but inert and warns instead
of failing). The "messaged you because you followed" DM big accounts send is
ManyChat's Meta-partner "Follow to DM" (beta, ~1000-follower eligibility) —
it cannot be built from this repo at any price. Do not add DM code that
initiates conversations; the API will reject it and Meta may flag the app.

**10. inmigraforma owes the reader the source and the meaning.**
Three owner rules (2026-08-25), all enforced by `validate()`, not just the
prompt: every caption carries a real `https://` URL of the exact official page
(allow-listed domains, home pages rejected, invented URLs forbidden); the
phrases "es cuando" / "es como cuando" are banned outright; and any post
quoting official English text needs a `QUÉ SIGNIFICA` slide of 150+ chars
covering what it says, what it means day to day, and what happens if ignored.
`repeated_openers()` also warns when one comparison formula spreads across a
batch — that is how the banned tic formed in the first place.

## Layout

| Path | Purpose |
|---|---|
| `accounts/<slug>/account.json` | Account identity, secret *names*, brand voice |
| `accounts/<slug>/queue/schedule.json` | That account's queue |
| `accounts/<slug>/specs/*.json` | Post content specs, rendered into slides |
| `accounts/<slug>/strategy.md` | The feedback loop's memory |
| `accounts/<slug>/hooks.json` | Every graded cover hook + score, joined to reach later |
| `accounts/<slug>/token_status.json` | Token expiry dates (no secret) |
| `accounts.py` | Registry; `--list-json` feeds the workflow matrices |
| `new_account.py` | Scaffolds a new (disabled) account + prints setup steps |
| `publish.py` | Publishes the oldest due post; marks it published |
| `render_slides.py` | Spec → branded 1080×1350 slides |
| `render_reel.py` | Spec or slides → 1080×1920 MP4 Reel, with audio |
| `audio.py` | Synthesises the Reel music bed (original, per-slug) |
| `build_reels.py` | Converts alternate queued posts to Reels |
| `generate_batch.py` | Authors a batch with Claude (Sonnet 5 on both accounts; per-account `model` in account.json), renders, queues |
| `hooks.py` | Hook-shape taxonomy; grades candidate covers blind, logs scores |
| `monitor.py` | Read-only digest + token expiry warning |
| `ideas.py` | Content-ideas monitor (X / RSS / HN) → `accounts/<slug>/ideas.json` |
| `dm_responder.py` | One automated welcome per inbound DM conversation |

## Workflows

| Workflow | When | Notes |
|---|---|---|
| Publish daily Instagram post | 16:00 UTC daily | The core job; 16:00 UTC = noon ET, both audiences are US |
| Account monitor | 08:00 UTC daily | Digest; fails at <10 days token runway |
| Queue health check | Mon 09:00 UTC | Fails below 5 queued posts |
| DM auto-responder | every 2h | Inert (warns) until messaging permission + App Review |
| Content ideas monitor | Tue 06:00 UTC | Refreshes idea sources; X only with `X_BEARER_TOKEN` |
| Generate content batch | daily 06:00 UTC | Only acts when queue < 8 (extra runs skip in seconds); reads fresh ideas |
| Refresh Instagram token | 1st monthly | Working since 2026-09-01 |
| Verify Instagram credentials | manual | Run after any token change |

## Secrets

Secret *names* are per-account, recorded in each `account.json`
(`token_secret`, `user_id_secret`); workflows resolve them with
`secrets[matrix.account.token_secret]`. `ANTHROPIC_API_KEY` and `GH_PAT` are
shared across accounts.

| Secret | Account | State |
|---|---|---|
| `IG_ACCESS_TOKEN` | leverageai | set; auto-refreshed, expires 2026-10-30 |
| `IG_USER_ID` | leverageai | set |
| `THREADS_TOKEN_LEVERAGEAI` | leverageai | set; auto-refreshed |
| `IG_TOKEN_INMIGRAFORMA` | inmigraforma | set; carries `instagram_manage_insights` since 2026-09-07, expires ~2026-11-06 |
| `IG_USER_ID_INMIGRAFORMA` | inmigraforma | set |
| `THREADS_TOKEN_INMIGRAFORMA` | inmigraforma | **not set** — Threads cross-posting skipped |
| `ANTHROPIC_API_KEY` | shared | set |
| `FB_APP_SECRET` | shared | set — inmigraforma's Facebook-route refresh needs it |
| `GH_PAT` | shared | set; fine-grained, Secrets:RW, expires 2027-08-31 (monitor warns) |
| `X_BEARER_TOKEN` | shared | **not set** — X idea sources skipped until it is |

**7. X (Twitter) reads cost money and need a billing-enabled developer
account.** X moved to pay-per-use in Feb 2026 (~$0.005/post read; no free read
tier). `ideas.py` therefore treats X as optional: without `X_BEARER_TOKEN` it
runs on the free sources (RSS, Hacker News) and says so. Ideas are **leads,
not facts** — the generator's prompt tells it to verify anything it uses by
web search. Do not let idea items bypass that rule.

## Known gaps

- **Token auto-refresh works** (fixed 2026-09-01). The monthly workflow
  refreshes every account and writes the result back with `GH_PAT`. Still
  update `accounts/<slug>/token_status.json` after any manual re-mint, or the
  warning fires against a stale date. The daily monitor now also warns before
  `GH_PAT` itself expires — its lapse is silent and kills refresh.
- **Insights work on both accounts** (inmigraforma fixed 2026-09-07 by
  re-granting its token with `instagram_manage_insights` — the app already had
  the permission at Standard access; only the token lacked the scope). Both
  accounts' learning loops are now above `performance.py`'s signal threshold,
  so generation is guided by real reach for the first time.
- **The app is in development mode.** Fine for Tester-role accounts, but every
  new account must be added as an Instagram Tester on the Meta app (and accept
  the invite) before its token can be minted. If publishing ever fails on
  permissions, App Review is the cause.
- **Generated posts publish unreviewed** on the Wednesday schedule. `--dry-run`
  plus the artifact is the review path.

## Conventions

- Write `schedule.json` with `json.dump(..., indent=2, ensure_ascii=False)` —
  matches what the publish workflow commits, keeps diffs clean.
- Media paths in `schedule.json` are repo-relative
  (`accounts/<slug>/queue/...`) because Meta fetches them from raw URLs —
  moving files means rewriting the paths in the same commit.
- Workflow jobs that commit run with `max-parallel: 1` and `git pull --rebase`
  before push — matrix jobs racing each other lose commits otherwise.
- Slides render with DejaVu on every platform so local previews match CI.
- Never commit a token. `token_status.json` holds dates only.
- Every Anthropic API call uses prompt caching — never pass `system` as a bare
  string or an uncached `tools` list. Use `hooks.cached(text)` for the system
  prompt and `hooks.cached_tools(*tools)` for the tools list (puts
  `cache_control` on the last tool only — that caches every tool before it
  too, so never mark more than the last), and call
  `hooks.log_cache_usage(resp, label)` after the response so a hit/write shows
  up in the run log. Default 5-minute TTL everywhere; nothing in this repo
  calls the same prompt again after a long enough gap for 1h TTL's 2x write
  cost to earn out. Caches are per-model — `hooks.grade()`/`retry()` run on
  `SCORER_MODEL`, `generate_batch.author()` on `MODEL`; identical text on
  different models is still two separate cache entries. This applies to any
  new call site too, not just the three that exist today.
