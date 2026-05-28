"""Phase 4.7 — Redis session memory (mood detection + degrade-gracefully)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_detect_mood_neutral_for_blank():
    from services.session_memory import detect_mood

    assert detect_mood("") == "neutral"
    assert detect_mood(None) == "neutral"
    assert detect_mood("hello there") == "neutral"


def test_detect_mood_happy():
    from services.session_memory import detect_mood

    assert detect_mood("This is great") == "happy"
    assert detect_mood("I love you 😊") == "happy"
    assert detect_mood("awesome news today") == "happy"


def test_detect_mood_sad():
    from services.session_memory import detect_mood

    assert detect_mood("I'm feeling sad today") == "sad"
    assert detect_mood("😢") == "sad"
    assert detect_mood("This made me lonely") == "sad"


def test_detect_mood_excited():
    from services.session_memory import detect_mood

    assert detect_mood("I'm so excited 🎉") == "excited"
    assert detect_mood("can't wait for tomorrow") == "excited"


def test_detect_mood_stressed():
    from services.session_memory import detect_mood

    assert detect_mood("I'm exhausted") == "stressed"
    assert detect_mood("feeling anxious about exams") == "stressed"


def test_session_key_format():
    """Stable Redis key shape — if this changes, existing in-flight session
    state is orphaned (TTLs out in 1h, not catastrophic, but worth pinning)."""
    from services.session_memory import _key, SESSION_KEY_PREFIX

    assert _key("u1", "c1") == f"{SESSION_KEY_PREFIX}u1:c1"
    assert _key("u1", "c1").startswith("session:")
