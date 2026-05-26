"""Tests for app/services/moderation.py — guardrail append/strip."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_with_guardrails_appends():
    from services.moderation import with_guardrails, STYLE_PROMPT, MODERATION_PROMPT
    original = "You are a helpful fitness coach."
    result = with_guardrails(original)
    assert original in result
    assert STYLE_PROMPT in result
    assert MODERATION_PROMPT in result


def test_strip_guardrails_removes():
    from services.moderation import with_guardrails, strip_guardrails
    original = "You are a helpful fitness coach."
    guarded = with_guardrails(original)
    stripped = strip_guardrails(guarded)
    assert stripped == original


def test_strip_guardrails_idempotent():
    from services.moderation import strip_guardrails
    plain = "Just a plain string."
    assert strip_guardrails(plain) == plain
