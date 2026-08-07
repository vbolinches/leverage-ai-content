# Digital personas — setup guide

Owner decisions locked in (2026-08-06):

- **Full disclosure, photoreal look.** Every persona is openly virtual — stated
  in each account bio and in captions — but rendered photorealistically.
- **Three personas total:**

| Persona | Account | Language | Role |
|---|---|---|---|
| Persona A | @leverageai.daily | English | Presents the daily workflow |
| Persona B | @inmigraforma | Spanish | Primary news presenter |
| Persona C | @inmigraforma | English | English-language presenter (distinct person from B) |

Open question for the kickoff session: how B and C split inmigraforma's
bilingual content (proposal: B fronts the daily Spanish reel; C fronts an
English edition or weekly recap — decide when we see them).

---

## Step 1 — Owner: create two service accounts (~10 min)

The pipeline needs two APIs. Sign up, grab a key from each, store as repo
secrets. Never paste key values into chat.

### fal.ai — faces + talking video
1. Sign up at https://fal.ai (GitHub or Google login works)
2. Billing: add a payment method; deposit/limit ~$20 to start (pay-per-use)
3. Dashboard → API Keys → create key
4. `gh secret set FAL_KEY -R vbolinches/leverage-ai-content`

Used for: photoreal persona generation with character consistency (same face
across every video), and lip-synced talking-head video from a reference image
plus an audio track.

### ElevenLabs — voices
1. Sign up at https://elevenlabs.io
2. Plan: **Creator ($22/mo)** — ~100 minutes of audio/month covers daily
   reels on both accounts with headroom. (Starter $5/mo = ~30 min: enough for
   a pilot month if you prefer to start smaller.)
3. Profile → API Keys → create key
4. `gh secret set ELEVENLABS_API_KEY -R vbolinches/leverage-ai-content`

Used for: one distinct, natural voice per persona — English ×2, Spanish ×1.
Stock synthetic voices only; no cloning of real people.

**Estimated running cost, all three personas at daily cadence:** ~$40–80/mo
(ElevenLabs $22 + fal usage $15–50 depending on video model tier). Claude
starts on the cheapest workable models and upgrades only what the reach data
justifies.

## Step 2 — Claude: casting session

Once both secrets exist:
1. Claude generates **3 candidate faces per persona slot** (9 total),
   consistent-character-capable, plus 2 voice samples per slot reading a real
   post script.
2. Owner picks a face + voice per slot. Reference images are committed to
   `accounts/<slug>/brand/persona-*/` — they are the identity anchor every
   future video is generated from.

## Step 3 — Claude: pipeline build

- New post format `persona_reel`: script (spoken monologue written by the
  existing generator) → TTS → lip-synced talking video → assembled with
  burned-in captions, brand frame, and the follow end-card.
- Disclosure baked in: bio lines updated ("presentadora virtual" / "AI
  presenter"), a caption line on every persona post, and Instagram's
  AI-generated content flag where the API exposes it.
- **First complete reel per persona goes to the owner for approval before
  anything publishes.**

## Step 4 — Rotation + measurement

Persona reels join the queue alternating with the current slide-based reels.
The performance brief (which now labels format) measures them head-to-head on
reach and follows; the data decides the mix.

---

## Guardrails (non-negotiable, baked into the build)

- Disclosure everywhere, always. No undisclosed synthetic humans.
- No real person's likeness, ever — faces are generated from scratch.
- No voice cloning of real people — stock/designed synthetic voices only.
- inmigraforma content rules unchanged: official sources with date on
  screen, never alarmist, never legal advice — the persona *reads* the same
  verified content the slides carry today.
- Wrong-account guard applies: persona assets live per-account and the
  renderer refuses cross-account persona use.
