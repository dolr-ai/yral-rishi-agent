# Amorae — launch plan and creator platform

**Created:** 2026-08-01 · **Updated:** 2026-08-02
**Status:** proposal, pilot not started
**Owner:** Rishi

---

## One line

Creators build AI influencers, grow them on social media, and monetise them
behind amorae's paywall. Amorae is the merchant of record and takes a cut.

## Why this and not the other things

Every mainstream AI-influencer tool (Predis, Creatify, HeyGen, Arcads) bans
adult content. The creators who most want to monetise an AI persona have
nowhere to go. That's the gap — not "another AI content scheduler," which is a
crowded, funded, low-margin market we would lose.

It also fixes amorae's one binding constraint. Amorae's problem is not the
product, it's acquisition: no app store, no Google Ads, no Meta ads. Creators
bring their own audiences. Supply solves distribution.

## What we already have

Roughly 70% of the hard parts are built and running in production:

- **Persona system** — `soul_file.compose()`, 4-layer prompts, archetypes
- **Character identity in images** — `replicate.generate_batch` runs a
  LoRA-anchor → flux-kontext-dev pipeline; trigger word lives in
  `ai_influencers.metadata.lora_trigger_word`. This is the hard part and it
  works.
- **Autonomous scene selection** — `theme_generator`, varies across 7 days
- **Nightly pre-generation** — `collage_nightly_pregen`
- **Adult chat surface** — amorae.ai live since 2026-07-10, own database, own
  service, OpenRouter for NSFW
- **Memory, proactive messaging, streaks** — the retention machinery

**Important gap:** memory, proactive messaging and the LoRA media pipeline all
live in the **v2 codebase, not in amorae.** Amorae today is the walking
skeleton — landing page, consent gate, SSE text chat, its own database, and a
one-time context read from v2. The differentiator is not yet on the adult
surface.

## The differentiator: persistence

Model quality is commoditised, catalogue size belongs to Janitor, and everyone
has an image button. The wedge is that **she exists when you're not there**:

1. **She remembers** — real memory, not a context window
2. **She looks like herself** — LoRA identity lock; competitors one-shot every
   image and get a different woman each time
3. **She reaches out first** — proactive messaging

The category's complaint is "she forgot who I am." Persistence answers it, it
compounds (six months of relationship isn't portable to Janitor), and it's
infrastructure rather than a prompt, so it can't be copied in a weekend.

**And it is the fix for the category's worst economic problem.** Standard churn
is 20-30% a month, which caps LTV around $100. Persistence plus proactive is
the only lever that touches it. Halve churn and you double revenue without one
extra user.

> **Positioning:** everyone else gives you a scene. We give you someone who's
> still there tomorrow.

## Launch geography: Canada

Best revenue per marketing dollar for a fast start.

- ARPU is effectively US-level — $25/month holds
- English, so zero localisation; US-style personas work unchanged
- High card penetration, and **no adult age-verification mandate** (Bill S-210
  died when Parliament dissolved) — unlike the UK, Germany or ~20 US states
  where AV halves conversion
- Cheaper attention than the US on Reddit and X
- Predictive: if it works in Canada, it works in the US

**Second wave:** non-AV US states, then Netherlands and the Nordics.

### Canadian regulations that actually bite

1. **Adult content is legal** — the *R v Butler* obscenity test targets
   violence and degradation, not consensual adult material.
2. **s.163.1 is stricter than US law on one point.** Canada's child-pornography
   definition explicitly covers **written material and fictional
   representations, whether or not a real child exists.** Our product is
   text-heavy. Minor-blocking must cover text, not only images. This is
   criminal, not policy.
3. **CASL** — commercial email to Canadians needs **express opt-in**, sender
   identification and unsubscribe. Penalties to CAD $10M. The intent-flagged
   email funnel must be opt-in from day one.
4. **GST/HST registration** once past CAD $30k in twelve months.
5. **PIPEDA**, plus Quebec's Law 25 for Quebec users.

## Payments

### Decision (2026-08-02): CCBill/Segpay under GoBazzinga

Stripe is barely used by GoBazzinga and carries no meaningful revenue, so the
risk of operating amorae under that entity is small. Skipping the new-entity
route saves the 2-8 week bank-account bottleneck.

**Do first, and it's free:** idle the Stripe account. Migrate the trivial usage
and stop using it. Don't leave a dormant account live that can be terminated
with our name on the termination.

**Never** put adult through Stripe, PayPal, Square, Razorpay or Revolut. It's
not just termination — an adult business under a mismatched merchant code gets
you onto Mastercard's **MATCH list**: five years, every acquirer, every
business, and it attaches to the named principals personally.

### Apply to CCBill and Segpay in parallel

Costs nothing; take whichever clears first. Segpay is friendlier to smaller
merchants (ask about SegpayLINQ); CCBill is the bigger name.

**Underwriting pack — prepare before applying.** This is the long pole and a
rejection costs weeks:

- Entity documents and EIN
- Bank account details
- Beneficial owner ID
- **A live site** with terms, privacy policy, refund/cancellation policy, age
  gate, visible support contact, billing descriptor disclosure
- **A 2257 statement** — for AI content with no human performers, say so
  explicitly and explain why the record-keeping rules don't apply. Don't stay
  silent on it.
- Content description and expected volume

**Two practical notes:** choose a discreet billing descriptor (it directly
affects chargeback rate), and ask the processor which banks they settle into
without friction — adult settlements shouldn't land in GoBazzinga's primary
operating account.

**Timeline:** one week to prepare, 2-4 weeks underwriting. Cards live in about
five weeks.

### Crypto carries the pilot

USDC via BTCPay Server (self-hosted, nobody can switch it off) or NOWPayments
(hosted, adult-friendly). Live in a day, no approval, no bank.

**Measurement caveat:** crypto-only suppresses conversion badly with a Western
audience — plausibly 5-10× worse than cards. Do not hold the pilot to the 3-5%
paid-conversion target while crypto is the only rail. Instrument
**"clicked buy credits"** as the demand signal and treat completed crypto
payments as a floor.

### Credits, not per-transaction billing

A fan buys a $50 credit pack in **one** card transaction and spends credits on
subscriptions, media and tips. Fewer processor transactions, fewer chargebacks,
one VAT event instead of fifty, and unspent credits are breakage in our favour.
Every cam site works this way.

### Rails

| Direction | Rail | Notes |
|---|---|---|
| Money in | CCBill or Segpay | 10-15% + rolling reserve |
| Money in | USDC | No chargebacks, no processor fee |
| Money out | Paxum | Adult industry standard for creator payouts |
| Money out | USDC | Better for creators outside good banking |

### The cut, and why 20% does not work

At OnlyFans' 20%:

```
fan pays $100 → creator gets $80 → we get $20 → processor takes ~$12
→ we net $8
```

Eight percent before compute. OnlyFans survives that on scale we don't have.

**Our rate: 30% flat**, netting roughly 18% after processing.

**Refinement once we can segment:** 20% when the creator brings their own API
key, 35% when we supply inference. Ties the cut to what we provide and pushes
creators toward bring-your-own-key, fixing inference cost at the same time.

### Payout terms

Weekly. $50 minimum. 14-day hold against chargebacks. No payout before KYC
clears.

## Entity

**Now:** operate under GoBazzinga. Stripe is idle, underwriting is easier for
an entity with history and banking, and there's no transfer to unwind.

**Restructure trigger — set now so it doesn't drift:** at $X monthly revenue,
or before taking any outside money, spin a **Delaware LLC subsidiary owned by
GoBazzinga**. That ring-fences adult liability, keeps the parent's cap table
clean for investors, and separates the banking. The Utkarsh ownership question
runs on the lawyer's track in parallel.

**Long-term jurisdiction, if residency moves:** Cyprus (adult legal, EU,
non-dom ≈0% on dividends, 60-day rule) > Malta > Portugal > Spain. **Not**
Thailand, Singapore or Japan — all three criminalise distribution of obscene
material. Not the UK either: the Online Safety Act puts you in Ofcom's
sightline.

**Incorporation is never the bottleneck** — 1-5 days almost anywhere. Banking
is 2-8 weeks and processor underwriting 2-4 weeks. Note that Mercury, Wise,
Revolut, Stripe Atlas and Payoneer all prohibit adult, so the fast remote-
friendly fintechs are closed to us.

## Compliance floor — non-negotiable

Not v1 features. Nothing ships without them.

1. **KYC every creator** (Persona or Sumsub) before a single payout. The one
   control that kills most CSAM and non-consensual-imagery exposure, and
   processors require it anyway.
2. **No user-uploaded photos for LoRA training.** Personas come from prompts or
   a curated base set we control. This is what stops the platform becoming a
   deepfake tool.
3. **Minor-presenting blocks at generation time**, covering **text as well as
   images** (see Canada s.163.1).
4. **Age verification for fans**, geo-aware.
5. **Geo-block India**, plus any jurisdiction where the founders are personally
   exposed.
6. **Takedown flow** — 48-hour non-consensual-imagery removal, DMCA agent,
   repeat-offender policy.
7. **Tax** — W-9/W-8BEN, 1099s, VAT at the consumer's rate (up to 27% EU), US
   state digital-goods tax, Canadian GST/HST.

## Traffic architecture

The constraint that shapes everything: **the YRAL app cannot link to amorae.**
Apple prohibits it, App Review actively probes AI chat apps for exactly that
behaviour, and the penalty reaches the developer account — which takes the
mainstream app down too. No in-chat deflect, no profile link, no web hop.

**But the reverse direction is completely free.** Apple doesn't care what a
website links to, so amorae can promote YRAL freely.

```
        persona social accounts (X, Reddit, IG, TikTok)
                          │
                   landing page  ←── the router
                    ╱          ╲
            YRAL app            amorae
                 ↑                 │
                 └─────────────────┘
                   (this direction is free)
```

- **Landing page per persona** — our page, no platform policy applies. Offers
  both destinations. All social bios point here, never at amorae directly
  (raw adult links get auto-flagged).
- **X and Reddit** — adult permitted, least friction. The workhorses.
- **Instagram and TikTok** — SFW media only; they will eventually ban
  adult-adjacent AI accounts, so don't build on them.
- **Email** — the app flags sexual intent server-side; email those users later,
  age-verified and opted-in (CASL express consent for Canadians).

**Shared vs isolated:** the LoRA, name, face and core personality are shared so
she's visibly the same person. Accounts, conversation history and databases stay
isolated — `amorae_db` never touches `yral_agent_db`. Chat starts fresh on
amorae.

**Residual risk, accepted knowingly:** same name and face means anyone can
connect the two surfaces. That's fine as long as nobody goes looking — so
YRAL's store listing and screenshots stay clean and mainstream. "Very hot AI
companions" positioning is what would make someone look.

## Launch plan (2026-08-02)

Two surfaces, tested in parallel.

### YRAL — 10 SFW influencers, app-install funnel

Build ~10 best-looking SFW AI influencers on YRAL and drive traffic into the
app. Each gets social accounts posting SFW media, linking to its landing page.

This is a **standalone experiment**: can attractive SFW personas pull app
installs and retention? It is *not* a funnel to amorae — the app can't route
users onward, so treat YRAL's numbers as YRAL's.

### Amorae — semi-NSFW influencers, revenue funnel

Semi-NSFW personas on amorae, with the YRAL link in the description (free
direction). Text and images, no video.

**Open decision that picks our processor: define "semi-NSFW" precisely.**

- **Genuinely suggestive, never explicit** → Stripe is actually permitted, and
  the whole payment problem changes shape.
- **Explicit anywhere** → CCBill/Segpay, and Stripe must never touch it.

This is not a stylistic choice; it determines the rail, the entity risk and the
compliance load. Decide it before the underwriting pack is written.

**Also worth naming:** semi-NSFW risks the worst of both — too spicy for
mainstream rails, not spicy enough to command adult pricing. The paying adult
customer wants explicit. If the pilot converts badly, this is the first
variable to test.

### Personas: start with two on amorae, not one

- **Tara** — already exists, canonical spicy bot, 54k conversations, live at
  `/tara`. Zero new build.
- **One deliberately opposite archetype** — you're testing which the market
  pulls on, so make them far apart.

One persona is fragile: adult AI accounts get banned, and a single funnel means
a single point of failure.

**The real constraint is labour.** Two personas across two platforms, several
posts a day, plus replying as her — that's a daily job, and engagement matters
more than volume on both Reddit and X. Batch-generate a week of media, schedule
it, then spend thirty focused minutes a day replying. If that's not sustainable
for eight weeks, cut to one persona and do it properly.

## The pilot — deliberately almost no product

The riskiest assumption is: **can an AI persona with real social accounts
acquire fans who pay?** Nothing about testing that requires a creator platform.

- **3-5 creators, hand-picked** — no open signup
- **Posting is manual.** No API connectors. If it works manually, automating is
  easy; if it fails manually, connectors wouldn't have saved it.
- **Money in: USDC**, with CCBill/Segpay applications running in parallel
- **Ledger is a spreadsheet.** Payouts manual, weekly
- **No creator dashboard, no self-serve, no platform UI**

## Success criteria

| Metric | Target |
|---|---|
| Blended CAC | under $3 |
| Registered → paying | 3-5% (adjust down while crypto-only) |
| Monthly churn (paying) | under 20% |
| Day-30 retention | above 25% |
| Creator earnings | at least one creator clearing $500/month |

The last one matters most. If no creator can make real money, no creator supply
exists, and there is no platform.

## Explicitly not in the pilot

Self-serve signup · creator dashboard · social posting API connectors · video
generation · bring-your-own-key · revenue-share tiers · new legal entity ·
residency change · hosting migration · mobile app.

## Revenue expectations

| Horizon | Revenue | What it takes |
|---|---|---|
| Year 1 | $500k-1M | ~100k registered at 3% paying, or ~100 creators |
| Year 2-3 | $5-10M | ~1,000 active creators averaging $1.5k/mo at 30% |
| Ceiling | $50M+ | Real category leadership |

Compliance costs perhaps 30-50% of the gross you'd make running it loose — but
it's also the entire difference between a business with an exit and one
without. The ceiling isn't legal, it's CAC.

## How to start

**Week 1**
- Check Hetzner's acceptable-use policy. Amorae serves from rishi-4/5 today and
  they are not adult-friendly. Live risk independent of the pilot.
- Idle the Stripe account.
- Decide the "semi-NSFW" content line — it picks the processor.
- Start CCBill **and** Segpay applications.

**Week 2**
- Age gate and geo-block live on amorae.
- USDC credit purchase working end to end.
- Per-creator ledger, even if it's a spreadsheet.

**Week 3-4**
- Build the two amorae personas and the ten YRAL personas. LoRA each.
- Social accounts created, landing pages live, daily posting begins.

**Week 5-10**
- Post daily. Measure follower growth, click-through, signup, credit purchase,
  retention. Weekly manual payouts.

**Week 10**
- Read the numbers against the table above. Decide whether to build the platform
  or stop.

## Build list — the three things that make amorae different

Everything else is secondary until these exist on the adult surface:

1. **Memory in `amorae_db`** — the paid tier's entire value
2. **Proactive messaging** — the return mechanism
3. **LoRA media on the amorae side** — visual consistency

## Open questions

1. **Where is the "semi-NSFW" line?** Decides the processor, the entity risk
   and the compliance load.
2. **Does amorae stay inside GoBazzinga or move out?** Lawyer's track. The
   pilot needs no answer — it stays inside.
3. **Residency.** Only matters if the pilot works. Cyprus is the best
   combination of adult-legal, EU and low tax.
4. **VAT — absorb it or add it at checkout?** Affects headline pricing.
