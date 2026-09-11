# Amorae video platform — "TikTok discovery + OnlyFans monetization" (web)

**Status:** kicked off 2026-08-09. Separate rail from the US SFW launch — do NOT fold together.
Related: `docs/us-launch-progress.md`, the amorae-web session (repo `~/Claude Projects/amorae-web`).

## The concept
A **TikTok-style vertical short-video feed** as the amorae.ai homepage (public, no login) →
tap a video → that creator/persona's **OnlyFans-style profile** → subscribe / chat / tip.
Discovery from the feed, monetization from the profile. All on the **web**.

## Content model (clarified 2026-08-09) — important
The **homepage feed is ALWAYS clean / SFW.** NSFW happens **only behind a gate**: when a user
*initiates* a spicy chat with a specific bot, or *unlocks* her pictures/videos (paid). So the
public discovery surface is clean (shareable, lower policy risk, mainstream-CDN-safe), and the
adult content is gated + monetized. Implication: the **adult-designated CDN, age-gate, and
adult payment rail apply to the GATED media/chat, not the feed.** The SFW CDN is fine for the feed.

## Why — the whole point is app-store independence
The web is outside Apple's/Google's jurisdiction: **adult content allowed, direct billing
(CCBill/Segpay, keep ~95%+ vs losing 30%), no App Review, no content bans, no anti-steering
rules.** This is the vehicle for the adult/spicy monetization the stores will never approve —
we stop asking for permission. Personas are the SAME as the YRAL catalog (`ai_influencers`),
so a persona spans SFW-YRAL-chat AND adult-Amorae-video.

## Architecture
- **Web front end** (amorae-web, FastAPI+Jinja) renders the feed + profiles + paywall.
- **Videos are served by the MOBILE backend** via a video-feed API; the web consumes the SAME
  endpoint. The web session designs the API contract + builds against a mock, then hands mobile
  a precise "here's what you must serve" spec.
- **Chat** ties into the existing amorae chat + the YRAL→Amorae handoff (`spicy_handoff`,
  `amorae_auth`).

## Tracks
| Track | Owner | Status |
|---|---|---|
| Web UI — TikTok homepage feed (no login) + OnlyFans-style profiles/paywall | amorae-web session | ▶ IN PROGRESS (tasked 2026-08-09) |
| **Video API contract** (what mobile must serve) | amorae-web session → reports to Rishi | ▶ key deliverable |
| Mobile video-feed endpoint (implements the contract) | mobile | ⏳ after the contract |
| Video generation at volume (AI-video / LoRA-per-persona pipeline) | — | ⏳ the content bottleneck |
| Age verification (18+ gate → real provider) | amorae-web + legal | ⏳ legally required (US state laws) |
| Payment (CCBill/Segpay) | — | ⏳ stubbed for now |
| Traffic / acquisition | Rishi / marketing | ⏳ the hard problem |

## The 3 make-or-break challenges — none are code
1. **Content at volume.** A feed is only as good as its supply. The AI-video pipeline must
   produce enough good short clips per persona, continuously. Empty feed = dead on arrival.
2. **Traffic.** Web frees us from the stores but NOT from the ad networks — **you still can't
   run adult content on Facebook/Google.** Traffic must come from the YRAL SFW funnel, adult
   ad networks (TrafficJunky etc.), or SEO. Building the platform doesn't solve this.
3. **Age verification** is a legal requirement now (TX/LA/VA/… mandate it, with liability) —
   design it in from day one, not bolted on.

## Also just heavier than chat
Video storage + bandwidth + a real CDN cost meaningfully more than text/images. Budget for it.

## Current status
The amorae-web session is building the homepage TikTok feed (public, age-gated, mock data) +
the OnlyFans-style profile/subscribe shell, and will report **the exact video API contract the
mobile backend must serve**. Once that lands, mobile builds just the video endpoint and the
front end is already waiting for it.

## Open questions
- Can the AI-video pipeline produce short-video content at the volume + quality a feed needs?
- Where does the traffic come from (given no mainstream adult advertising)?
- Which age-verification provider, and its cost/UX?
- Timing: this is the "next big bet" — green-light for real after the US SFW launch settles, or run partly in parallel?
