"""Coach Fix 4 — action-verb intent classifier.

When Saikat typed "Save these changes." in the Coach chat AFTER Coach
had already shown the ✅ Saved receipt, Coach treated it as a NEW edit
request and produced fresh proposed_changes. That's the "infinite loop"
feel from his 2026-06-09 alpha session.

This module is the fast pre-check that runs BEFORE the Coach LLM call.
If the creator's message is a recognized action verb (save / apply /
discard / undo / start-over) AND there's a pending unapplied proposal
in the session, the route returns `{type: "action", action: "save"}`
and SKIPS the LLM call entirely. Mobile reads the action and triggers
the existing /apply flow.

Why keyword-match not a tiny LLM call:
  - Speed: 0 network calls, sub-ms.
  - Determinism: same input → same output. No model drift.
  - Predictability: easy to extend the pattern list when new phrasings
    surface in alpha testing.
  - Conservative on misses: if the classifier returns None, the
    request falls through to the normal Coach flow — no behavioral
    change vs pre-PR for unmatched cases.

When the classifier matches but no pending proposal exists, the route
ALSO falls through to the Coach LLM — Coach has the history context to
ask "what would you like me to save?" instead of producing yet another
proposal. So the "ambiguous" case from Rishi's spec is handled by
Coach itself, not by this module.
"""

import re


# Patterns are matched against the creator's message AFTER lowering +
# stripping. Word-boundaries prevent false positives ("savings" should
# not match "save", "discarded the offer" should not match "discard").
_SAVE_PATTERNS = (
    r"\bsave\b",
    r"\bsave it\b",
    r"\bsave these\b",
    r"\bsave the changes\b",
    r"\bsave changes\b",
    r"\bsave it now\b",
    r"\bapply\b",
    r"\bapply it\b",
    r"\bapply these\b",
    r"\bapply changes\b",
    r"\bapply the changes\b",
    r"\bgo ahead\b",
    r"\bconfirm\b",
    r"\bconfirmed\b",
    r"\byes save\b",
    r"\byes save it\b",
    r"\bdo it\b",
    r"\bproceed\b",
    r"\bship it\b",
    r"\bok save\b",
    r"\bcommit\b",
)

_DISCARD_PATTERNS = (
    r"\bdiscard\b",
    r"\bdiscard it\b",
    r"\bdiscard these\b",
    r"\bdiscard changes\b",
    r"\bdiscard the changes\b",
    r"\bcancel\b",
    r"\bcancel it\b",
    r"\bcancel changes\b",
    r"\bnevermind\b",
    r"\bnever mind\b",
    r"\bforget it\b",
    r"\bthrow it away\b",
    r"\bdrop it\b",
)

_UNDO_PATTERNS = (
    r"\bundo\b",
    r"\bundo that\b",
    r"\bundo the\b",
    r"\bundo it\b",
    r"\brevert\b",
    r"\brevert it\b",
    r"\brevert the\b",
    r"\bgo back\b",
    r"\bstart over\b",
    r"\breset\b",
    r"\brollback\b",
    r"\broll back\b",
)


# Pre-compile the union regex for each intent. One regex per intent so
# we know which intent matched (vs. one mega-regex that just says "matched").
_INTENT_RE = {
    "save": re.compile("|".join(_SAVE_PATTERNS), re.IGNORECASE),
    "discard": re.compile("|".join(_DISCARD_PATTERNS), re.IGNORECASE),
    "undo": re.compile("|".join(_UNDO_PATTERNS), re.IGNORECASE),
}


# Length cap on intent matching: long messages are almost certainly
# edit-requests phrased verbosely, not action verbs. "Save my game" is
# 12 chars, "Apply these changes please" is 27. If the message is more
# than ~50 chars and contains other content beyond the action verb,
# we treat it as a regular edit request — Coach will interpret it.
#
# This is the false-positive guard for messages like "I want to save
# time on these long replies — can you make them shorter?" which would
# otherwise match `\bsave\b`.
_MAX_ACTION_MESSAGE_LEN = 50


def classify_intent(content: str) -> str | None:
    """Return one of {"save", "discard", "undo"} if the message is a
    short action verb, else None.

    Conservative on length to avoid matching action verbs inside longer
    edit-request sentences ("I want to save my draft and also..."). The
    fall-through to None means the normal Coach flow runs — no behavioral
    regression for unmatched cases."""
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    if not stripped:
        return None
    if len(stripped) > _MAX_ACTION_MESSAGE_LEN:
        return None
    for intent, pattern in _INTENT_RE.items():
        if pattern.search(stripped):
            return intent
    return None
