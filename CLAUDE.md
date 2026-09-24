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

Publishing runs in GitHub Actions. **Writing runs on the owner's PC**, on a
local model through Ollama, since 2026-09-11 — see point 11.

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

Both accounts therefore run **6 Reels to 1 carousel** (`"reel_ratio": 6`).

That last carousel is deliberate and is **not** there for reach — it is a
measurement tax (owner's call 2026-09-07). Reels win 10-20x *today*, but
carousels are a follower-facing format and that gap should narrow as an
account grows; freezing September's numbers into the config would mean never
noticing. One carousel in seven costs about one low-reach day a week and keeps
the comparison alive. `monitor.format_read()` prints median reach by format in
every daily digest, and raises a notice if carousels ever come within 2x of
Reels — that is the signal to rebalance. Do not remove the carousel share
without replacing the measurement.

Note the number that makes this less obvious than it looks: inmigraforma has
164 followers and its carousels reach 3 — under 2% of its own audience, with
0 profile views. Having followers is not sufficient for carousels to work.
Judge posts on reach, not likes.

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

**A budget has to be checked against the arc it is paired with.** When those
limits were set on 2026-09-07 no batch ran again until 2026-09-11, so nothing
tested them, and inmigraforma's were unsatisfiable: its five mandated
eyebrows, five headlines, cover sub and three recap arrows cost 337 of the
470-character budget before a word of explanation, leaving 44 characters for
each of the three explaining slides against an arc asking slide 2 for 2-3
everyday sentences. The per-slide ceilings said 110 and the total said 44;
both were enforced, so every post was rejected whatever it did. Raised to 60
seconds (owner's call 2026-09-11), which leaves ~96 per explaining slide and
still beats the 77-second Reels this account used to ship.

**`slide_limits` are derived from `reel_target_seconds` — change them
together.** Raising the target to 60s without revisiting the per-field
ceilings left `body` at 110, the figure derived from the old 45-second
budget, and that became the binding limit instead of the budget. Measured on
a post that passed: structure costs 274 of the 627 characters, leaving 353
across three explaining slides, or 117 each — which is exactly where the
model plateaus. `body` is now 120, so the TOTAL budget is the authority and
the field ceiling only stops one slide eating the post.

Before changing `reel_target_seconds` or `slide_limits` again, do the
subtraction: fixed structure first, then what is left for the slides that
carry meaning. `generate_batch._slide_plan()` now states that arithmetic in
the authoring prompt, because a model cannot be expected to derive it — told
only "cut 170 characters", it plateaued three repair rounds in a row.

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

**The hook grader now has one override on this account, and only this one.**
Grading on scroll-stopping power alone is deliberate everywhere else, but on
a news account it selects for alarm: on a real batch it ranked "¿Usas el
I-864 viejo? ¡Detente! / pierde tu Green Card" first *because* it was the
most frightening, and an outdated form costs nobody a Green Card they already
hold. `hooks._accuracy_floor()` scores an overstated hook below 3 for
accounts with `require_source_url`, which is this account's own
ACCURACY-OVER-BREVITY rule applied to the grader instead of only to the
writer. A hook is still judged on stopping power; it just cannot buy that
with a claim that is not true.

"Invented URLs forbidden" stopped being a request when generation moved local
(point 11). `search.RETRIEVED` records every URL a real search or page read
returned, and `validate()` rejects a caption citing anything else. The model
is also shown that list and told to copy from it, because it cannot recall a
URL across a dozen tool calls — it reconstructs one that looks exactly right
and 404s, `uscis.gov/news` for a page that was really
`uscis.gov/newsroom/all-news`. Selecting from a list it can do; remembering
it cannot. Do not relax either half.

**11. Generation runs on this machine, not in Actions (2026-09-11).**
The pipeline dropped the Anthropic API for a local Ollama model
(`qwen3:30b-a3b` by default; `OLLAMA_MODEL` overrides). A GitHub-hosted
runner cannot reach `localhost:11434`, and a self-hosted runner on a repo
that **must stay public** for Meta would let a fork's pull request execute
code on the owner's PC — so the schedule moved to Windows Task Scheduler,
03:00 local, via `run_local_batch.py`, and `generate-batch.yml` was deleted.
Publishing, monitoring, ideas and token refresh are untouched and still run
in Actions on time regardless of whether the PC is awake.

What this costs: if the PC is off for a week, nothing generates and nobody is
told. The queue-health workflow (Mon 09:00 UTC, fails below 5 queued posts)
is the alarm, which is exactly the job point 4 gave it. Do not remove it.

**The host is a variable, and is expected to change.** The owner travels and
the PC is not always on, so a VPS move is planned rather than hypothetical.
Nothing in the pipeline may assume this machine: no Windows paths, no
hardcoded `localhost` (use `llm.HOST` / `OLLAMA_HOST`), no dependency that
only exists here. `deploy/vps-setup.sh` installs the same job on Debian or
Ubuntu behind a systemd timer, and `run_local_batch.py --check` proves a host
can actually run a batch — Ollama, the model, ddgs, curl, DejaVu, ffmpeg and
push credentials — before a schedule trusts it. Run it on any new host, and
run it here when something breaks for no clear reason.

The sizing question is the only hard part of that move: `qwen3:30b-a3b` wants
about 24GB of RAM and a normal VPS has no GPU. It activates 3B parameters per
token so CPU is viable, but budget hours rather than the ~40 minutes a 7-post
batch takes on the 5090 here. Time a `--dry-run --force --count 2` on the box
before believing it. `OLLAMA_MODEL` is the knob, and a smaller model costs
inmigraforma first — its rules are the ones already at the edge of what a 30B
can satisfy.

What it changes about authoring. A hosted frontier model wrote seven posts
and rewrote strategy.md in one 128K response; a 30B local model cannot hold
that shape, and the failure was all-or-nothing. `author()` is three passes
now — research and verify, write post by post, rewrite the strategy — so a
post that comes out malformed costs that post rather than the batch. Two
guards exist because the local model actually tripped them on its first run:
`write_post()` shows the model its own `validate()` errors and takes one
repair pass, and `rewrite_strategy()` refuses to replace strategy.md with
anything much shorter than what is on disk. That file is the loop's memory;
the first local run offered a 327-character summary to replace 15,027
characters of earned findings.

**12. Every post is fact-checked against its own source before it queues.**
Added 2026-09-22 after the local model published harmful immigration advice.
Everything upstream proved something narrower: `search.RETRIEVED` proves a
source is real; `search.supports()` proves the brief's quote is on it. Nothing
proved the finished POST says what the page says. So the local model, with a
real source and a verified quote, wrote that Medicaid and food stamps "no
longer count" toward public charge - citing the USCIS page whose operative
sentence is that benefits received on or after 2026-09-18 now DO count. It was
pulled the day before publishing. Three others had already gone out: two told
readers to leave the US over a rule still open for public comment, one said a
priority date is lost when it is kept. leverageai recommended "AutoBill Pro",
which does not exist. The owner deleted what could be deleted.

`factcheck.verify()` has the model list every claim and instruction in the
post, each with the page sentence that decides it and a verdict. A small model
checks far better than it writes accurately - but only if it cannot bluff, so
the evidence sentence is looked up on the page in code (`factcheck.on_page`):
three quarters of its four-word runs must be there. That tolerates a copying
slip (three dropped words, a colon added to "Release Date") and rejects a
fabricated quote, a real sentence bent to say the opposite, or a wrong date -
all seven calibration cases pass.

The strict bar lives in ONE place. `search.supports()`, at the research stage,
only asks whether the page is ABOUT the topic, and stays lenient (the quote's
opening, or half its word-runs, on the page). It was briefly made as strict as
the fact-check and research starved - nine good topics dropped in one run,
TPS, EAD and a scam alert among them - because the model drifts when copying
at that stage. Truth is decided on the finished post, claim by claim; do not
move that bar back upstream.

`generate_batch._truth_pass()` runs last in `write_post()`, shows the model
the contradictions WITH the page's own sentence, gives it two fix rounds, and
`author()` drops whatever is still untrue - a spare brief takes the slot. A
gap in the queue is recoverable; a published wrong fact on an account people
act on is not.

Calibrated on real posts before it shipped: post68 (inverted) failed 1/10
supported with the operative sentence quoted back; post48 (correct, same page)
passed 10/10. Safe advice is allowed without a source (`SAFE_ADVICE`: save
this, consult a lawyer, check your case, beware scams) because the danger is
asymmetric - "consulta a un abogado" cannot hurt anyone, "sal de EE.UU." can.
Anything telling the reader to change what they do about status, benefits,
residence or a deadline must be on the page.

The mechanical half is `_reader_safety()` in `validate()`, fast and model-free
so it runs inside every repair round: `forbidden_advice` patterns per account
(leave the country, "sin miedo", "no te preocupes"); English on a Spanish
slide; "Embajadores'" possessives; the "El sistema" template placeholder; and
an action deadline that will have passed by publish day ("manda ... antes del
14 sep" went out on the 26th). Tested against the 6 posts that did the damage
(all caught) and the 21 Sonnet-era posts (all clean) - keep both sets passing
when you touch these rules. Research also drops `audience: "niche"` briefs and
reads each account's `topic_priorities`: the month the local model took over,
inmigraforma covered a Federal Register index and civil-surgeon designations.

**What made the local model produce ANY passing post** (2026-09-23, seven
test batches; the first four yielded zero). Each change answers an observed
failure - keep them together:

- The writer thinks (`WRITER_THINK`, per-account `writer_think`). It ran with
  reasoning off while the checker ran with it on; the checker was precise all
  day, the writer copied English onto Spanish slides and ignored the date.
- The writer is told TODAY and the publish window. The researcher always was;
  the writer never was, and wrote "renueva antes del 9 de septiembre" on the
  23rd.
- `llm.chat` sends a fresh seed whenever temperature > 0. Without one, repair
  rounds 2-5 came back byte-identical - four "retries" of one answer.
- Repair by REMOVAL. `factcheck.strip_claims()` deletes the sentence carrying
  each still-untrue claim, after two rewrite rounds; `_fit_sentences()` does
  the same for small length overflows. A rewrite trades one invented detail
  for another; a deletion only removes. A claim in a headline, or a deletion
  that would empty a slide, rejects the post instead.
- One field, one job. English on a Spanish slide is fixed by `_to_spanish()`
  per field, asked IN Spanish for a field NAMED in Spanish
  (`texto_en_espanol`) - asked in English for "text", the model echoed the
  English back unchanged.
- The checker judges meaning across languages, splits compound sentences into
  single facts, and accepts a who-is-affected line that follows from the page's
  own stated coverage. Each fixed a real false rejection. Tips accounts
  (no `require_source_url`) are checked on world facts only (`WORLD_ONLY`) -
  what exists, what it does, what it costs, when it shipped, numbers - never
  on their own prompts and advice, which are the product.
- Research drops what the writer then turns into invented news: evergreen form
  and hub pages (`_evergreen`), OMB paperwork notices (`_paperwork` - a Form
  I-821 collection notice became "DHS proposes extending TPS for several
  countries"), topics built on a deadline already past (`_past_deadline`), and
  a second topic citing a page already used. One empty sweep no longer ends
  research for the night.
- The total reading budget rejects only above its target; the 5% margin is
  what the writer aims for, not the line.
- `next_date()` counts only queued and published posts. Counting retired ones
  meant retiring a week of bad posts pushed every replacement a week out.

Yield is still the open problem: the checks now stop what they should (in
testing: an invented TPS deadline, a paperwork notice read as a TPS
extension, false hope for refugee families, and the public-charge inversion
generated again from scratch), but a batch produces few posts. Fewer true
posts is the safe failure - the queue-health alarm catches an empty queue;
nothing catches a wrong post once it is seen.

On 2026-09-23 the queue audit (every queued post through these checks)
retired all 8 of inmigraforma's and 12 of leverageai's 13 - invented
consequences and deadlines, an asylum deadline from January 2025, a leaked
Siri demo presented as a shipped feature, invented products and prices.
Retired posts keep their reasons in `note` and restore by setting `status`
back to `queued`.

Do not remove the fact-check to speed up the nightly run. It adds roughly a
minute per post on the 5090, and it is the only thing that checks the post
rather than the source.

**13. Moving covers: generated motion behind the hook (2026-09-23/24, off
until the text is fixed - see point 14).** A Reel's first second decides
whether a stranger stays, and ours opened on a still. `motion.py` runs in
the nightly job AFTER all writing (the video model does not fit beside the
text model in 24GB, so it unloads Ollama first), makes a ~4s clip per queued
Reel with Wan 2.2 TI2V 5B (Apache-2.0, local, free, ~32GB in the Hugging
Face cache) and re-renders the Reel with the clip behind the cover
(`render_reel.MotionCover`). Per account, opt-in: `"motion_cover":
{"enabled": ..., "mode": "topic", ...}`.

- The clip is RELATED TO THE POST (owner's call after seeing abstract
  clips: "boring"). `motion.concepts()` has the local text model say what
  the post is about, then write one LITERAL and one METAPHOR prompt for it.
  Generated people, officers, flags, borders, places and datacenters MAY
  appear (owner, 2026-09-23) - never a real or recognisable person, never a
  brand's product, and on inmigraforma nothing frightening (`negative`,
  `visual_rules` per account). The cover's WORDS are still drawn by
  `render_slides`; a video model renders writing as gibberish, so
  `_unreadable()` sends back any prompt that asks for a word, digit, label,
  stamp or date and `_scrub()` is the last resort.
- Wan 2.1 1.3B was tried first and deleted: five minutes a clip, but flat,
  near-static, and it drew fake text. The owner compared both; Wan 2.2 won
  clearly (real hands, real motion, no text) at 20-23 minutes a clip on the
  5090 laptop. That means 1-3 Reels a night, not twelve. Untested lever:
  fewer inference steps.
- The GPU is shared. Another project on this PC (`fed-bid-workflow`,
  `qwen2.5:14b`) loaded a model mid-run twice and cost 8 of 12 clips
  (out-of-memory) and a 30-minute text stall. The nightly window must have
  the GPU to itself, or the stage waits.
- It can never cost a post: no environment, no model, a failed clip - the
  Reel keeps its still cover and the night's commit goes ahead.
- Every Reel with a moving cover publishes with `is_ai_generated=true`
  (Meta's "AI info" label; required on photorealistic generated video, and
  these are). `publish.py` retries without the field if a route rejects it -
  container creation publishes nothing, so that retry is safe.
- The model environment lives OUTSIDE the repo (`~/.cache/leverage-motion/
  venv`, override `MOTION_VENV`/`MOTION_PYTHON`): the repo sits in a synced
  Google Drive folder. Clips are cached in `motion_cache/` (gitignored);
  only the finished `reel.mp4` is committed. `python motion.py --check` says
  whether a host is ready; a VPS without a GPU simply keeps still covers.
- Trials never touch the queue: `python motion.py --account <slug> --post
  <id>,<id> --variants 2 --out review_out/motion` renders each concept as
  its own Reel; `--synthetic` uses an ffmpeg-made clip that needs no model.

**14. Posts must inform a first-time reader, and the writer is the limit
(2026-09-23/24).** The owner read a week of queued posts that had passed
every truth and format check and could not tell what they were about:
"Spot-check skills. No AI allowed", "Connect tools / Ask Gemini / to draft
proposals", "Antes: H-2Bpara 2027. Ahora: no.", a recap of "1. Familiares:
Dates". His rule: posts are for informing people; someone with limited
knowledge must finish clear and without doubts, not feeling they wasted
their time. Clarity outranks yield; an empty queue is the acceptable
failure.

What now enforces it:

- `clarity.py`: a first-time reader (per-account persona in
  `clarity_reader` - a solo owner who knows ChatGPT but not "API";
  an immigrant who knows "Green Card" but not "I-485") reads ONLY the
  slides, says what it understood, and quotes every place it stopped. Quotes
  not on the slides are discarded in code; only "blocks" severity counts;
  new version numbers and current-year dates are not doubts.
  `author()` drops a post with more than `clarity_max_doubts` (1).
- `_clarity_rules()` in `validate()`, model-free: filler headlines ("The
  system", "Qué significa esto." - both were copied from the schema's own
  examples, now replaced), step bodies under 6 words, recap items under 4,
  words glued by a shortener ("H-2Bpara").
- Explain first (`_explain`): the writer produces 5-8 self-contained plain
  sentences for that reader, each clarity-read AND fact-checked, up to four
  rounds; the slides must carry those sentences word for word (`_snap`
  restores trimmed ones). Rewriting SLIDES for clarity was measured never to
  converge (4->4, 5->5, 4->6) and is gone; the model shortener (64-74 calls
  a run, the source of the fragments) is gone - length is fixed only by
  removing whole sentences. Budgets allow full sentences: inmigraforma 70s,
  leverageai 55s.
- The writer sees the source page's own how-to and availability lines
  (`_page_steps`) and may cite steps, menus, plans and prices from those
  only; with none it writes news, not an invented how-to ("Type
  @QuickBooks" was invented and rejected). Research drops topics that
  promise a number their page never states ("50% cheaper", "3x faster").
- The hook grader scores an unreadable hook below 3.

**The finding these produced:** with `qwen3:30b-a3b` as the writer, 0 of 14
posts met the bar in the final test, and the explanation stage trades
clarity for truth round by round (0 doubts / 3 untrue, then 4 doubts / 0
untrue). The checks work; the writer cannot satisfy them. It activates 3B of
its 30B parameters per token, which is why it is fast and why it checks
better than it writes. The next step (in progress) is a dense writer that
fits 24GB - `qwen3.6:27b` first, `gemma4:31b` if needed - kept SEPARATE from
the checker (`writer_model` per account) so checking stays cheap and the
writer stays under a reader it did not train with. Slower is accepted:
fewer, clear posts.

**What the writer test found (2026-09-24, eight runs, `qwen3.6:27b`
writing):** the explanation stage converged on 7 of 7 topics (0 doubts, 0
untrue in 2-4 rounds) once three things were true, and on none before:
the repair touches only the flagged sentences; a sentence cleared once
stays cleared while unchanged (the checkers flip verdicts on identical
text - three verbatim sentences went "supported" to "contradicted" between
rounds); and a correct DEFINITION of a term is judged on correctness, not
page presence (the clarity gate demands definitions, the truth gate was
rejecting them). Every remaining failure was in what the slide writer
adds on top of the verified sentences, and each is now mechanical in
`validate()`: `_off_script()` - headlines, subs and recap arrows may use
only the explanation's own words, and the cover no acronym its first
sentence lacks; subs under five words and step bodies under six are
fragments. The truth pass and the final read exempt cleared sentences
(`_verify`, `_uncleared`), so they judge only the writer's additions.

Not yet proven: a post through every gate end to end, because the test
could not finish on a shared GPU. `wings_agent/sandbox/trader.py` uses the
same Ollama with `qwen2.5:14b` (14.5GB); with the 16.5GB writer and the
18GB checker, three models thrash through 24GB and an 80-second call takes
30 minutes. Batches need the card to themselves. Ollama also auto-updates
and restarts mid-run (14:41 that day); a lost round is survived, not a
lost post - `_explain` continues on error and `factcheck` retries a cap
overrun with reasoning off.

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
| `generate_batch.py` | Authors a batch with the local model, renders, queues |
| `llm.py` | The model seam — every generative call goes through here |
| `search.py` | Web search and page reading, and the record of what was really retrieved |
| `factcheck.py` | Checks a finished post's claims against its own source page; `python factcheck.py <spec.json>` checks one by hand |
| `motion.py` | Moving covers: generates an abstract clip per queued Reel and re-renders it; `--check`, `--synthetic` |
| `motion_worker.py` | The only file that imports the video model; runs in the motion environment |
| `run_local_batch.py` | The nightly run: gate, generate, commit, push; `--check` proves a host |
| `setup_schedule.ps1` | Registers that run with Windows Task Scheduler |
| `deploy/vps-setup.sh` | The same run on a Debian/Ubuntu VPS, behind a systemd timer |
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
| Refresh Instagram token | 1st monthly | Working since 2026-09-01 |
| Verify Instagram credentials | manual | Run after any token change |

Generation is deliberately **not** a workflow any more — see point 11.

## Secrets

Secret *names* are per-account, recorded in each `account.json`
(`token_secret`, `user_id_secret`); workflows resolve them with
`secrets[matrix.account.token_secret]`. `GH_PAT` is shared across accounts.
Generation needs no secret at all now — it runs on a local model.

| Secret | Account | State |
|---|---|---|
| `IG_ACCESS_TOKEN` | leverageai | set; auto-refreshed, expires 2026-10-30 |
| `IG_USER_ID` | leverageai | set |
| `THREADS_TOKEN_LEVERAGEAI` | leverageai | set; auto-refreshed |
| `IG_TOKEN_INMIGRAFORMA` | inmigraforma | set; carries `instagram_manage_insights` since 2026-09-07, expires ~2026-11-06 |
| `IG_USER_ID_INMIGRAFORMA` | inmigraforma | set |
| `THREADS_TOKEN_INMIGRAFORMA` | inmigraforma | **not set** — Threads cross-posting skipped |
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
- Every generative call goes through `llm.py`. Never talk to Ollama's HTTP API
  directly and never add a second provider SDK: `llm.structured()` for a JSON
  answer, `llm.tool_loop()` for anything that must search first. The reasons
  are in that module's docstring and each one is a silent failure, not a loud
  one — above all `num_ctx`, which defaults to 4096 and drops the overflow
  off the FRONT of the prompt, so a call that forgets it runs without its
  brand rules and returns confident nonsense.
- Prompt caching is gone with the Anthropic API, but the discipline behind it
  still pays: Ollama reuses the KV cache of the longest matching prompt
  PREFIX, so keep the system prompt byte-identical across the calls in a run
  (`hooks.grade()` does, once per post) rather than rebuilding it per call.
  `llm.log_usage` prints the token counts that show whether it is working.
- Structure belongs in the schema, content belongs in the prompt. Ollama's
  `format` constrains the sampler, so `minItems`/`maxItems`/`enum` make a
  wrong shape unreachable, while a description saying "exactly 5" is only a
  suggestion the sampler may ignore — and it did: told in plain words to
  write five slides, shown the account's five-slide arc, and given its own
  validation errors to fix, the model closed the slides array after two every
  single time until `minItems` was added. Reach for the schema before
  reaching for a firmer instruction.
- `search.RETRIEVED` is the provenance record, not a cache. Anything that
  cites a URL must check against it — see `validate()`. A local model writes
  URLs that look perfect and 404, and for inmigraforma that is the whole
  product.
