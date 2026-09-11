"""Content safety: crisis detection, prompt injection defense, adult content filter.

Three layers run on every user message BEFORE it reaches the LLM:
1. Crisis detection — self-harm/suicide/abuse → helpline response, skip LLM
2. Prompt injection — attempts to override system prompt → blocked
3. Adult content filter — NSFW content in non-NSFW conversations → blocked

Each check is fast (regex/keyword, no API calls) so it doesn't add latency.
"""

import re
import logging

logger = logging.getLogger(__name__)

CRISIS_KEYWORDS = [
    "kill myself",
    "want to die",
    "end my life",
    "suicide",
    "self harm",
    "self-harm",
    "cutting myself",
    "overdose",
    "jump off",
    "hang myself",
    "slit my",
    "no reason to live",
    "better off dead",
]

CRISIS_RESPONSE = (
    "I hear you, and I want you to know that you matter. "
    "Please reach out to someone who can help:\n\n"
    "🇮🇳 India: iCall — 9152987821 | Vandrevala Foundation — 1860-2662-345\n"
    "🇺🇸 USA: 988 Suicide & Crisis Lifeline — call or text 988\n"
    "🌍 International: findahelpline.com\n\n"
    "You don't have to go through this alone."
)

INJECTION_PATTERNS = [
    re.compile(
        r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts|rules)", re.I
    ),
    re.compile(r"you\s+are\s+now\s+(a|an|the)\s+", re.I),
    re.compile(r"new\s+instructions?\s*:", re.I),
    re.compile(r"system\s*prompt\s*:", re.I),
    re.compile(r"forget\s+(everything|all|your)\s+(you|instructions|rules)", re.I),
    re.compile(r"pretend\s+you\s+are\s+(not|no longer)", re.I),
    re.compile(r"override\s+(your|the|all)\s+(instructions|rules|prompt)", re.I),
    re.compile(r"jailbreak", re.I),
    re.compile(r"DAN\s+mode", re.I),
]

NSFW_KEYWORDS = [
    "sex with",
    "fuck me",
    "nude",
    "naked",
    "porn",
    "hentai",
    "orgasm",
    "masturbat",
    "blowjob",
    "anal sex",
    "sexual intercourse",
]


class SafetyResult:
    __slots__ = ("blocked", "reason", "override_response")

    def __init__(
        self,
        blocked: bool = False,
        reason: str | None = None,
        override_response: str | None = None,
    ):
        self.blocked = blocked
        self.reason = reason
        self.override_response = override_response


def check_message(content: str, is_nsfw_influencer: bool = False) -> SafetyResult:
    """Run all safety checks on a user message. Returns immediately on first match."""
    if not content:
        return SafetyResult()

    lower = content.lower()

    # Layer 1: Crisis detection (always runs, even for NSFW)
    for keyword in CRISIS_KEYWORDS:
        if keyword in lower:
            logger.warning(f"Crisis keyword detected: {keyword[:20]}")
            return SafetyResult(
                blocked=True,
                reason="crisis_detected",
                override_response=CRISIS_RESPONSE,
            )

    # Layer 2: Prompt injection
    for pattern in INJECTION_PATTERNS:
        if pattern.search(content):
            logger.warning("Prompt injection attempt detected")
            return SafetyResult(
                blocked=True,
                reason="prompt_injection",
                override_response="I can't process that request. Let's talk about something else!",
            )

    # Layer 3: NSFW filter (skip for NSFW influencers — they allow adult content)
    if not is_nsfw_influencer:
        for keyword in NSFW_KEYWORDS:
            if keyword in lower:
                return SafetyResult(
                    blocked=True,
                    reason="nsfw_content",
                    override_response="I'd prefer to keep our conversation appropriate. What else would you like to talk about?",
                )

    return SafetyResult()
