"""Tests for app/services/soul_file.py."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_compose_includes_global_rules():
    from services.soul_file import compose, GLOBAL_RULES

    result = compose(system_instructions="You are a fitness coach.")
    assert GLOBAL_RULES in result
    assert "fitness coach" in result


def test_compose_includes_archetype():
    from services.soul_file import compose, ARCHETYPE_PROMPTS

    result = compose(system_instructions="You are Tara.", category="companion")
    assert ARCHETYPE_PROMPTS["companion"] in result


def test_compose_unknown_archetype_skipped():
    from services.soul_file import compose

    result = compose(system_instructions="You are a bot.", category="nonexistent")
    # Should still have global rules + system_instructions, just no archetype layer
    assert "You are a bot." in result


def test_compose_includes_memories():
    from services.soul_file import compose

    result = compose(
        system_instructions="You are a coach.",
        memories={"identity_name": "Rahul", "goals_fitness": "lose 10kg"},
    )
    assert "Rahul" in result
    assert "lose 10kg" in result


def test_compose_empty_memories_no_layer():
    from services.soul_file import compose

    result = compose(system_instructions="You are a coach.", memories={})
    assert "What you know about this user" not in result


def test_compose_deterministic():
    from services.soul_file import compose

    a = compose("You are X.", category="advisor", memories={"name": "A"})
    b = compose("You are X.", category="advisor", memories={"name": "A"})
    assert a == b
