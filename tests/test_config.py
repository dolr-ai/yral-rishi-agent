"""Tests for app/config.py — verify env reading and defaults."""

import os


def test_env_helper_returns_default():
    from config import _env

    assert _env("NONEXISTENT_VAR_12345", "fallback") == "fallback"


def test_env_int_returns_default():
    from config import _env_int

    assert _env_int("NONEXISTENT_VAR_12345", 42) == 42


def test_env_int_handles_invalid():
    from config import _env_int

    os.environ["TEST_BAD_INT"] = "not_a_number"
    assert _env_int("TEST_BAD_INT", 99) == 99
    del os.environ["TEST_BAD_INT"]


def test_env_float_returns_default():
    from config import _env_float

    assert _env_float("NONEXISTENT_VAR_12345", 0.7) == 0.7


def test_env_bool_false_by_default():
    from config import _env_bool

    assert _env_bool("NONEXISTENT_VAR_12345", False) is False


def test_env_bool_true():
    from config import _env_bool

    os.environ["TEST_BOOL"] = "true"
    assert _env_bool("TEST_BOOL") is True
    del os.environ["TEST_BOOL"]


def test_expected_issuers():
    from config import EXPECTED_ISSUERS

    assert "https://auth.yral.com" in EXPECTED_ISSUERS
    assert "https://auth.dolr.ai" in EXPECTED_ISSUERS


def test_app_defaults():
    from config import APP_NAME, APP_VERSION, GEMINI_MODEL

    assert APP_NAME == "Yral Agent API"
    assert APP_VERSION == "2.0.0"
    assert "gemini" in GEMINI_MODEL.lower()
