"""Phase 23.5 — skill_state block parser + streaming suppression filter.

The first-turn onboarding flow asks the LLM to emit a hidden block:

    <skill_state>{"setup": {...}, "runtime": {...}}</skill_state>

at the END of its visible reply. The backend then:
  1. Parses the JSON out of the block
  2. Strips the block from the text sent to mobile
  3. Writes the parsed dict to user_skill_state (via skill_state_repo)

Two surfaces need to handle the block:
  - Non-streaming POST /messages: we have the full text upfront, just parse + strip.
  - Streaming /messages/stream: tokens arrive one chunk at a time; we must
    NOT emit the `<skill_state>` literal to the client. SkillStateStreamFilter
    holds back the trailing bytes that COULD be the start of "<skill_state>"
    until we know they're not.

Both surfaces share `parse_skill_state_block()` for the final extraction.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# Permissive on the inner JSON (DOTALL so newlines work). Tolerates trailing
# whitespace + an optional markdown code fence the LLM may wrap around the
# JSON ("```json … ```").
_SKILL_BLOCK_RE = re.compile(
    r"<skill_state>\s*(.+?)\s*</skill_state>",
    re.DOTALL,
)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_skill_state_block(content: str) -> tuple[dict | None, str]:
    """Extract the skill_state JSON from `content`.

    Returns (parsed_dict_or_None, cleaned_content). On parse failure the
    block is still stripped from cleaned_content (so mobile never sees the
    literal tag), but the returned dict is None — caller logs and the row
    falls into status='onboarding_partial' on the next turn.

    Safety: only accepts top-level JSON objects (dicts). A model that emits
    an array or scalar is treated as parse failure.
    """
    m = _SKILL_BLOCK_RE.search(content)
    if not m:
        return None, content

    raw = m.group(1).strip()
    # Some models wrap the JSON in a fenced code block — strip the fence.
    raw = _FENCE_RE.sub("", raw).strip()

    cleaned = _SKILL_BLOCK_RE.sub("", content).strip()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("skill_state JSON parse failed: %s — raw=%r", e, raw[:200])
        return None, cleaned

    if not isinstance(parsed, dict):
        logger.warning(
            "skill_state block was not a JSON object (got %s)", type(parsed).__name__
        )
        return None, cleaned

    return parsed, cleaned


class SkillStateStreamFilter:
    """Suppress the `<skill_state>…</skill_state>` block from a token stream.

    Usage:
        f = SkillStateStreamFilter()
        for token in stream:
            emit = f.feed(token)
            if emit:
                yield emit
        tail = f.flush()
        if tail:
            yield tail
        parsed_state, cleaned = f.parse()

    Behavior:
      - Holds back the trailing N bytes when N could be the start of
        `<skill_state>` (so we never leak a partial tag).
      - Once a complete `<skill_state>` open tag is seen, stops emitting
        entirely until end of stream.
      - `parse()` runs the same `parse_skill_state_block` on the full
        accumulated text after the stream ends.

    Why a class not a generator: the caller's loop already iterates the
    LLM client's async generator and needs to interleave with `done` /
    `error` events. A stateful filter keeps that orchestration in one place.
    """

    _OPEN_TAG = "<skill_state>"

    def __init__(self) -> None:
        self._buffer = ""  # bytes we could emit but might still be a tag prefix
        self._full = ""  # full accumulator (for final parse)
        self._suppressed = False  # True once an open tag has appeared

    def feed(self, token: str) -> str:
        """Returns the safe-to-emit slice for this token (may be empty)."""
        self._full += token
        if self._suppressed:
            return ""

        self._buffer += token

        # Open tag fully present → emit pre-tag prefix, then suppress forever.
        idx = self._buffer.find(self._OPEN_TAG)
        if idx != -1:
            prefix = self._buffer[:idx]
            self._buffer = ""
            self._suppressed = True
            return prefix

        # Hold back any tail that COULD be the start of the open tag —
        # walk from longest possible prefix down to shortest.
        max_overlap = min(len(self._buffer), len(self._OPEN_TAG))
        for k in range(max_overlap, 0, -1):
            if self._OPEN_TAG.startswith(self._buffer[-k:]):
                safe = self._buffer[:-k]
                self._buffer = self._buffer[-k:]
                return safe

        safe = self._buffer
        self._buffer = ""
        return safe

    def flush(self) -> str:
        """Emit any held-back tail at end-of-stream. Empty if suppressed."""
        if self._suppressed:
            return ""
        tail = self._buffer
        self._buffer = ""
        return tail

    @property
    def full_text(self) -> str:
        """All tokens that passed through, including the suppressed tag."""
        return self._full

    def parse(self) -> tuple[dict | None, str]:
        """Run parse_skill_state_block over the full accumulated text."""
        return parse_skill_state_block(self._full)
