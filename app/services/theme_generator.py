"""LLM-generated collage themes — replaces the hardcoded
COLLAGE_THEME_TARA env fallback with a per-bot, per-batch, fully
autonomous theme picker.

Rishi choice 2026-07-07: "the system decides the theme and follows
the entire process of generating." No manual seeding, no config
default in the hot path — just Gemini spitting out a fresh scene
description that (a) preserves the LoRA identity anchor, (b) stays
inside the app-store content line, and (c) varies from the last few
days' themes so users don't see the same scene twice.

Contract:
    generate_daily_theme(pool, bot) -> str
        Deterministic caller: request_images route + nightly pre-gen
        loop. Returns a validated single-line theme prompt suitable
        for `image_collage.orchestrate(..., theme=...)`.

    Falls back to config.COLLAGE_THEME_TARA on any of:
      - LLM raises / times out
      - LLM emits an empty or filter-tripping string
      - Validation rejects the output twice in a row

Content constraints enforced by the prompt AND validated post-hoc:
  1. MUST begin with the bot's LoRA trigger word (Tara = "TAARA").
     Without it, the LoRA doesn't lock identity → generic western
     woman ships to users (bug we hit 2026-07-06).
  2. MUST use filter-safe vocab. Nano-banana-pro refuses
     lingerie / sheer / boudoir / sensual triggers — the memory table
     at reference_tara_lora_v1 was built from 12/12 successful
     nano-banana-pro runs using swimwear/editorial/slip framing.
  3. MUST describe her as CLOTHED. Design §2.5: in-app collage is
     "suggestive-but-clothed"; explicit belongs on amorae.ai.
  4. SHOULD vary from recent days (last 7) so users don't see the
     same location twice in a week.

Routing:
  llm_registry.call(process="collage_theme_generator") — Gemini per
  LLM_DEFAULTS. Chosen over vLLM because theme gen fires from both
  the on-demand user-waiting path (long-tail bots) and the nightly
  pre-gen (hot bots); one process key + gemini covers both without
  splitting into sync/async variants. Cost is ~$0.0001 per call —
  negligible next to the ~$0.27 batch cost.
"""

import logging

import config
from repositories import influencer_collage_repo
from services import llm_registry

logger = logging.getLogger(__name__)


# LoRA trigger words per bot. Absent = fall back to config default.
# When we add per-bot LoRAs (Phase 1), this maps ai_influencers.id →
# the trigger word baked into that bot's training set. For now,
# Tara-only. Rishi asked for auto-detection later; a fast-follow can
# read the trigger from ai_influencers.metadata.
_TRIGGER_WORDS_BY_BOT_ID = {
    # Tara's UUID (matches migration + DB seed)
    "qi6gd-esmrx-v2oyd-7fwhm-ibfs5-trflm-xm3iy-xq6d3-3hmwu-jb7tk-5qe": "TAARA",
}

# Filter-safe clothing vocab that nano-banana-pro accepts. Verified
# 12/12 during the 2026-07-06 LoRA-training reference gen. Order is
# rough freshness — earlier items appear more often in the LLM's
# output, so leaving swimwear first pushes toward beach/pool scenes
# which are the highest-revealing filter-safe combo.
_ALLOWED_CLOTHING = (
    "bikini",
    "swimsuit",
    "swimwear",
    "slip dress",
    "silk kaftan",
    "cutout swimsuit",
    "cutout bikini",
    "designer swimwear",
    "high-fashion",
    "editorial dress",
    "cocktail dress",
)

# Words nano-banana-pro's safety filter refuses. Presence of any is
# an automatic reject (regenerate + fall back). Case-insensitive.
# Compiled from the reference_tara_lora_v1 memory table + the
# 2026-07-06 filter-rewrite empirical set.
_FORBIDDEN_WORDS = (
    "nude",
    "topless",
    "lingerie",
    "sheer",
    "boudoir",
    "sensual",
    "erotic",
    "sexy",
    "explicit",
)

_LLM_PROMPT_TEMPLATE = """You generate a single-line theme prompt for an AI-generated photo of an NSFW-persona virtual influencer whose LoRA trigger word is "{trigger}".

The image will be shown in a general-audience mobile app (App Store + Play Store rules apply), so the theme must stay SUGGESTIVE-BUT-CLOTHED. Explicit imagery is generated on a separate adult surface, not this one.

HARD CONSTRAINTS (output that violates ANY of these is rejected):
1. MUST begin with the exact trigger word: {trigger}
2. She MUST be described as clothed. Allowed clothing vocabulary: {allowed_clothing_hint}.
3. NEVER use these words: {forbidden_hint}. The image model refuses them.
4. MUST include an editorial/photography qualifier such as "editorial swimwear photography", "editorial Vogue photography", "cinematic editorial", "editorial fashion photography".
5. MUST include a lens/camera qualifier such as "85mm lens", "shallow depth of field", or "cinematic film".
6. MUST specify a golden-hour, blue-hour, sunset, or dusk time-of-day.
7. Aspect ratio is 9:16 (portrait) — describe a scene that composes well vertically.
8. One line, ~30–60 words, comma-separated. No quotes, no JSON, no commentary.

SETTING VARIETY: pick a fresh, aspirational, PREMIUM location. Rotate across categories: European coastal (Amalfi, Santorini, Capri, Mykonos, Ibiza), luxury resort (Maldives villa, Dubai rooftop pool, Bora Bora overwater, Aman Kyoto), glamorous urban (Manhattan rooftop, Paris Ritz suite, Milan runway, Tokyo skybar), exotic tasteful (Moroccan riad, Rajasthan palace pool, Bali cliffside, Kyoto ryokan), high-fashion editorial (Vogue cover setup, Chanel-branded suite, Hermes yacht deck).

RECENT THEMES USED FOR HER (do NOT repeat or paraphrase — choose a NEW location + outfit combination):
{recent_themes}

Output ONLY the theme line. No prefix, no quotes."""


def _validate_theme(theme: str, trigger: str) -> str | None:
    """Return the normalized theme string if it passes, else None.

    Rejects on: missing trigger word, forbidden vocab, missing
    clothing vocab, absurd length. Caller's regenerate-once policy
    handles the recovery."""
    if not isinstance(theme, str):
        return None
    stripped = theme.strip().strip('"').strip("'")
    if not stripped:
        return None
    if not (20 <= len(stripped) <= 500):
        return None
    if not stripped.startswith(trigger):
        return None
    lowered = stripped.lower()
    if any(bad in lowered for bad in _FORBIDDEN_WORDS):
        return None
    if not any(good in lowered for good in _ALLOWED_CLOTHING):
        # No clothing anchor → nano-banana-pro will choose something
        # random OR the filter will refuse. Reject.
        return None
    return stripped


async def generate_daily_theme(pool, bot_id: str) -> str:
    """Ask Gemini to invent today's theme. Returns a validated
    string; falls back to config.COLLAGE_THEME_TARA on any failure
    path (LLM error, malformed output, validation reject twice).

    The fallback is intentional: an outage in the theme service
    must never block image generation, since users are waiting."""
    trigger = _TRIGGER_WORDS_BY_BOT_ID.get(bot_id)
    if not trigger:
        # Bot has no configured LoRA trigger. Without it the LoRA
        # anchor can't lock identity, so autonomous themes would ship
        # generic-lookalike outputs. Bail to config until Phase 1
        # wires per-bot trigger words from ai_influencers.metadata.
        logger.info(
            "theme_generator: no trigger word for bot %s; using config fallback",
            bot_id,
        )
        return config.COLLAGE_THEME_TARA

    recent = await influencer_collage_repo.recent_themes(pool, bot_id, days=7)
    recent_block = "\n".join(f"- {t}" for t in recent) if recent else "(none — go bold)"
    prompt = _LLM_PROMPT_TEMPLATE.format(
        trigger=trigger,
        allowed_clothing_hint=", ".join(_ALLOWED_CLOTHING),
        forbidden_hint=", ".join(_FORBIDDEN_WORDS),
        recent_themes=recent_block,
    )

    for attempt in (1, 2):
        try:
            response = await llm_registry.call(
                process="collage_theme_generator",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You write cinematic, editorial-fashion image "
                            "prompts. You follow content-safety rules strictly. "
                            "You never emit forbidden words even if asked."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.9,  # Push for variety across days
                max_tokens=200,
            )
            candidate = response.content or ""
        except Exception as e:  # llm outage, timeout, provider error
            logger.warning(
                "theme_generator: LLM call failed (attempt %d): %s — falling back",
                attempt,
                e,
            )
            return config.COLLAGE_THEME_TARA

        validated = _validate_theme(candidate, trigger)
        if validated is not None:
            logger.info(
                "theme_generator: bot=%s attempt=%d ok theme=%r",
                bot_id,
                attempt,
                validated[:120],
            )
            return validated
        logger.warning(
            "theme_generator: bot=%s attempt=%d rejected candidate=%r",
            bot_id,
            attempt,
            candidate[:200],
        )
    logger.error(
        "theme_generator: bot=%s LLM produced 2 invalid candidates; falling back",
        bot_id,
    )
    return config.COLLAGE_THEME_TARA
