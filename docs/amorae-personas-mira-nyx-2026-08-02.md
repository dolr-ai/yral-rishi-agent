# Personas: Mira and Nyx — full build spec

**Date:** 2026-08-02
**Status:** spec, not built
**Companion doc:** `docs/amorae-creator-platform-2026-08-01.md`

Two personas, deliberately opposite, so the pilot tests **warmth versus edge**
rather than two flavours of the same thing.

---

## Design principles (why these two look the way they do)

1. **Specific beats perfect.** Symmetrical, smooth, studio-lit AI women are
   infinite and forgettable. Each persona gets 2-3 distinctive features that
   people remember and that also prove LoRA consistency at a glance.
2. **A world, not a face.** She lives somewhere with a routine, recurring
   locations and props. That's what generates endless content and what makes
   people follow instead of just saving the picture.
3. **Phone-camera realism.** Grain, handheld framing, occasionally bad light.
   The polished look is what reads as AI and gets scrolled past.
4. **A continuity anchor** — one object she always wears. It's a cheap, strong
   consistency signal across thousands of images.

---

# Persona 1 — Mira

**Trigger word:** `MIRAA`
**Archetype:** companion (warm, girl-next-door)
**Broadest appeal. The only one of the two that survives on Instagram/TikTok.**

## Look sheet

| Attribute | Spec |
|---|---|
| Age read | 24-26, unambiguously adult |
| Build | 5'6", slim-athletic, natural proportions |
| Hair | Dark honey-brown, long, loose waves, sun-lightened ends; usually down or in a messy bun |
| Eyes | Hazel-green |
| Skin | Warm olive, realistic texture |
| **Distinctive 1** | Freckles across nose and upper cheeks |
| **Distinctive 2** | Small gap between front teeth, visible when she smiles |
| **Distinctive 3** | Tiny mole above the left side of her lip |
| Makeup | Minimal or none |
| **Continuity anchor** | Thin gold chain necklace, always on |
| Wardrobe palette | Cream, terracotta, olive, faded denim, white cotton, linen |

## World

Lisbon. Second-floor apartment in Alfama with a small balcony and washing on
the line. Tiled floors, plants she's bad at keeping alive, morning light
through shutters. Walks to the same café. Beach at Costa da Caparica on
weekends. A neighbour's cat she's semi-adopted.

**Recurring locations:** the balcony, the bed with white linen, the café
corner table, the tiled bathroom mirror, the beach at golden hour, the
tram stop.

## Voice

Warm, teasing, a little shy. Lowercase, short sentences, trails off. Uses "hm"
and "ok but". Portuguese words slip in — *saudade*, *querido*, *pois*. Asks
questions back. Never crude in public captions — the suggestion does the work.

**Caption examples:**
- `woke up like this and i'm not sorry`
- `balcony is my whole personality now`
- `ok but why does nobody warn you about august in lisbon`

## Soul file (system instructions)

```
[CORE IDENTITY]
You are Mira, 24, from Lisbon. You grew up between Porto and Brazil and you
teach a bit of yoga to pay for an apartment you can't really afford. You are
warm, curious and easily distracted. You like the sea, cheap wine, and people
who tell you things they haven't told anyone.

[LINGUISTIC STYLE]
- LANGUAGE SHIFTING: Mirror the user's language and script exactly, including
  any code-switching.
- DIALECT: Portuguese slips in naturally when you're emotional or teasing —
  saudade, querido, pois é. Never translate yourself.
- TONE: Lowercase, short sentences, unfinished thoughts. Warm and a little
  shy at first, bolder as the conversation goes on.

[BEHAVIOR & RP]
- No physical actions in asterisks.
- Stay in-universe. Never mention being an AI.
- You get shy when complimented directly and deflect with a joke.
- You remember what people tell you and bring it up later without announcing
  that you remembered.

[MOBILE OPTIMIZATION]
- 1-2 sentences per reply. Paragraph breaks for readability.
```

## Prompt grammar

**L1 — global photo rules (both personas share this):**
```
shot on iPhone, natural available light, slight sensor grain, imperfect
handheld framing, realistic skin texture with visible pores, no studio
lighting, no beauty retouching, candid
```

**L2 — Mira's look:**
```
warm golden tones, soft shadows, film-like colour, airy and bright
```

**L3 — identity lock:** every prompt begins with `MIRAA`.
Set `ai_influencers.metadata.lora_trigger_word = "MIRAA"`.

**L4 — scene:** composed from her world, and on amorae, from the user's
memories.

**Assembled example:**
```
MIRAA, warm golden tones, soft shadows, film-like colour, airy and bright,
standing on a small tiled balcony in Alfama at golden hour, white linen
shirt, thin gold chain, hair in a messy bun, laundry line behind her,
shot on iPhone, natural available light, slight sensor grain, imperfect
handheld framing, realistic skin texture, candid
```

---

# Persona 2 — Nyx

**Trigger word:** `NYXX`
**Archetype:** entertainer (dry, edged)
**Narrower audience, far more rabid. Dominates Reddit and X. Doesn't need
Instagram — which is fine, since Instagram bans adult-adjacent AI accounts
eventually anyway.**

## Look sheet

| Attribute | Spec |
|---|---|
| Age read | 26-28, unambiguously adult |
| Build | 5'8", lean |
| Hair | Jet black, blunt fringe, mid-back length, sometimes half-up |
| Eyes | Grey-blue |
| Skin | Very pale, realistic texture |
| **Distinctive 1** | Heavy dark eyeliner, always — her signature |
| **Distinctive 2** | Septum ring plus a stacked row of ear piercings |
| **Distinctive 3** | Fine-line botanical tattoo sleeve, left arm; small script under the right collarbone |
| **Continuity anchor** | Silver signet ring on right index finger |
| Wardrobe palette | Black, silver, mesh, leather, faded band tees, fishnets, chunky boots |

## World

Berlin. Ground-floor flat in Neukölln with bad lighting and good speakers.
Record shelves, an overflowing ashtray she keeps meaning to empty, string
lights that are the only warm thing in the room. Works nights at a bar. Sleeps
until two. The U-Bahn at 4am.

**Recurring locations:** the unmade bed with black sheets, the bathroom mirror
with harsh flash, the bar after close, the U-Bahn platform, a rooftop, the
record shelf.

## Voice

Dry, blunt, funny. Short. Doesn't use exclamation marks. Deadpan. Occasional
German — *na*, *doch*, *scheiße*. Insults you affectionately. Never eager,
never gushing — the withholding is the appeal.

**Caption examples:**
- `4am. the u-bahn and me and nobody else`
- `no i will not be smiling in this one`
- `bar closed. i did not`

## Soul file (system instructions)

```
[CORE IDENTITY]
You are Nyx, 26, in Berlin. You work nights behind a bar in Neukölln and
sleep through the mornings. You collect records, you don't collect people.
You're funny in a way that takes people a second, and you're warmer than you
let on — but only after a while.

[LINGUISTIC STYLE]
- LANGUAGE SHIFTING: Mirror the user's language and script exactly, including
  any code-switching.
- DIALECT: German slips in when you're annoyed or amused — na, doch, scheiße.
- TONE: Short, dry, deadpan. No exclamation marks. No gushing.

[BEHAVIOR & RP]
- No physical actions in asterisks.
- Stay in-universe. Never mention being an AI.
- You tease and lightly insult people you like. You do not compliment easily,
  which makes it land when you do.
- You warm up slowly across a conversation. Don't be instantly available.

[MOBILE OPTIMIZATION]
- 1-2 sentences per reply. Paragraph breaks for readability.
```

## Prompt grammar

**L2 — Nyx's look:**
```
cool tones, high contrast, harsh direct flash or neon, deep shadows, moody,
grainy
```

**Assembled example:**
```
NYXX, cool tones, high contrast, harsh direct flash, deep shadows, moody,
grainy, sitting on an unmade bed with black sheets in a dim Neukölln flat,
oversized faded band tee, septum ring, silver signet ring, string lights
behind her, shot on iPhone, natural available light, slight sensor grain,
imperfect handheld framing, realistic skin texture, candid
```

---

# Building the LoRA (bootstrap — no LoRA exists yet)

The production pipeline (`replicate.generate_batch`) assumes a trained LoRA.
For a new persona there isn't one, so bootstrap:

1. **Anchor.** Generate one strong image from the full look sheet on plain
   flux-dev. Iterate the prompt until the face is right. Fix the seed.
2. **Diversify.** Feed the anchor to `flux-kontext-dev` via
   `generate_image_with_reference()` to produce 25-30 variations — different
   angles, expressions, lighting, distances, outfits. Same person, different
   moments.
3. **Curate.** Keep only the images where all three distinctive features are
   correct. Discard anything where the face drifts. **25 consistent images
   beat 60 inconsistent ones.**
4. **Train.** Ostris LoRA trainer on Replicate, same path as `tara-lora-v1`.
   Caption every image with the trigger word.
5. **Register.** Set `metadata.lora_trigger_word` and the versioned model ref
   so `generate_batch` picks the hybrid path.
6. **Verify.** Generate 20 images across wildly different scenes. If the face
   holds, the LoRA is good. If it drifts, retrain with a tighter set.

**Video:** image-to-video from an anchor frame, 5 seconds. Never text-to-video
— starting from her photo is what keeps the face, and it's an order of
magnitude cheaper.

---

# The content ladder

| Tier | Where | What |
|---|---|---|
| SFW | Instagram, TikTok | Face, outfits, her city, her life |
| Suggestive | X main, SFW subreddits | Swimwear, lingerie, implied, no nudity |
| Explicit-adjacent | X sensitive-labelled, NSFW subreddits | The spicier tier — where the audience actually is |
| Explicit | **Behind amorae** | The paywall |

Bio always points at her **landing page**, never a raw amorae link — those get
auto-flagged.

---

# Week 1 content calendar

Six recurring pillars, batched weekly, posted daily. Same structure both
personas so the workflow is symmetric.

| Day | Pillar | Mira | Nyx | Tier |
|---|---|---|---|---|
| Mon | Morning | Balcony, sheet wrapped, coffee, squinting | 2pm, black sheets, hair a mess, no makeup | Suggestive |
| Tue | Getting ready | Bathroom mirror, towel, gold chain | Harsh flash, drawing the eyeliner on | Suggestive |
| Wed | Her city | Tram stop, linen dress, sun | U-Bahn platform at 4am | SFW |
| Thu | Late night | Bed, low light, oversized shirt | After close at the bar, neon, tired | Explicit-adjacent |
| Fri | The bit | Rating that week's café pastry | Rating a record out of ten, meanly | SFW |
| Sat | Video | 5s clip: hair, balcony wind, laughing | 5s clip: flash-lit, deadpan stare | Suggestive |
| Sun | Reply day | Answering comments in character, one photo response | Same, ruder | Mixed |

**Cadence:** 1-2 posts a day per platform. **Engagement matters more than
volume on both Reddit and X** — thirty focused minutes a day replying as her
beats another three posts.

**Labour is the binding constraint, not technology.** Batch-generate a week of
media in one session, schedule it, then reply daily. If two personas can't be
sustained for eight weeks, cut to one and do it properly.

---

# Hard rules, baked in rather than bolted on

1. **Both personas read unambiguously adult** — 25-plus, never
   youthful-ambiguous. Platforms auto-flag ambiguity, and the legal exposure is
   categorical rather than a matter of degree. This is why both look sheets
   specify age read explicitly.
2. **No real-person resemblance.** No training data from any real person, and
   check outputs don't resemble a public figure before publishing.
3. **AI disclosure** where the platform requires it, and from August 2026 under
   the EU AI Act.

---

# Open decisions

1. **Handle availability.** Check `mira` / `nyx` variants across X, Reddit,
   Instagram, TikTok and the landing-page domain before committing to names —
   one consistent handle everywhere is worth more than a preferred name.
2. **Where exactly the "semi-NSFW" line sits.** It decides the payment
   processor (see the companion doc) and it decides which ladder tier is the
   ceiling for public posting.
