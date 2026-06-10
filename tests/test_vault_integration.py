"""Tests for the Vault integration helper + its wire-up in llm_registry.

These pin the contract — file-first wins, Vault next, env last. The 4
background processes that broke 2026-06-09 (quality_scorer +
memory_extraction + nudge_generation + video_idea_generation) all route
through `runpod_vllm`, so a regression in resolution-order would put
them back to 100% failure.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def test_vault_module_exposes_get_secret_and_client():
    from services import vault

    assert hasattr(vault, "get_secret")
    assert hasattr(vault, "get_vault_client")
    assert hasattr(vault, "reset_client_for_tests")


def test_get_vault_client_raises_without_token(monkeypatch, tmp_path):
    """No /run/secrets/vault_token AND no VAULT_TOKEN env → RuntimeError.

    This is what would happen in CI or any environment that hasn't been
    bootstrapped with the Vault service token yet.
    """
    from services import vault

    vault.reset_client_for_tests()
    monkeypatch.setattr(vault, "VAULT_TOKEN_SECRET_PATH", str(tmp_path / "nope"))
    monkeypatch.delenv("VAULT_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="VAULT_TOKEN not set"):
        vault.get_vault_client()


def test_read_token_prefers_file_over_env(monkeypatch, tmp_path):
    """Token file at /run/secrets/vault_token wins over env var."""
    from services import vault

    vault.reset_client_for_tests()

    token_file = tmp_path / "vault_token"
    token_file.write_text("token-from-file\n")
    monkeypatch.setattr(vault, "VAULT_TOKEN_SECRET_PATH", str(token_file))
    monkeypatch.setenv("VAULT_TOKEN", "token-from-env")

    assert vault._read_token() == "token-from-file"


def test_read_token_falls_back_to_env(monkeypatch, tmp_path):
    from services import vault

    vault.reset_client_for_tests()
    monkeypatch.setattr(vault, "VAULT_TOKEN_SECRET_PATH", str(tmp_path / "nope"))
    monkeypatch.setenv("VAULT_TOKEN", "token-from-env")

    assert vault._read_token() == "token-from-env"


def test_resolve_api_key_uses_vault_when_file_missing(monkeypatch, tmp_path):
    """For runpod_vllm: file path doesn't exist → Vault lookup is tried."""
    from services import llm_registry

    monkeypatch.setitem(
        llm_registry.PROVIDERS["runpod_vllm"],
        "secret_path",
        str(tmp_path / "no-such-file"),
    )

    fake_vault = mock.MagicMock(return_value="vault-token-value")
    monkeypatch.setattr("services.vault.get_secret", fake_vault)

    val = llm_registry._resolve_api_key("runpod_vllm")
    assert val == "vault-token-value"
    fake_vault.assert_called_once_with(
        "saikat-llm-medium-fast-bearer-token",
        "token",
    )


def test_resolve_api_key_prefers_file_over_vault(monkeypatch, tmp_path):
    """If /run/secrets/RUNPOD_VLLM_API_KEY exists, file wins. This is the
    pre-Vault state and we want graceful behavior during the transition.
    """
    from services import llm_registry

    secret_file = tmp_path / "RUNPOD_VLLM_API_KEY"
    secret_file.write_text("file-token-value")
    monkeypatch.setitem(
        llm_registry.PROVIDERS["runpod_vllm"], "secret_path", str(secret_file)
    )

    fake_vault = mock.MagicMock(return_value="vault-token-value")
    monkeypatch.setattr("services.vault.get_secret", fake_vault)

    val = llm_registry._resolve_api_key("runpod_vllm")
    assert val == "file-token-value"
    fake_vault.assert_not_called()


def test_resolve_api_key_falls_through_to_env_when_vault_fails(monkeypatch, tmp_path):
    """Vault unreachable → log warning → fall through to env. The 4
    background processes should keep working off the env-var even if
    Vault has an outage."""
    from services import llm_registry

    monkeypatch.setitem(
        llm_registry.PROVIDERS["runpod_vllm"],
        "secret_path",
        str(tmp_path / "no-such-file"),
    )

    def raise_(*a, **kw):
        raise RuntimeError("Vault network error simulated")

    monkeypatch.setattr("services.vault.get_secret", raise_)
    monkeypatch.setenv("RUNPOD_VLLM_API_KEY", "env-token-fallback")

    val = llm_registry._resolve_api_key("runpod_vllm")
    assert val == "env-token-fallback"


def test_runpod_vllm_provider_has_correct_post_migration_shape():
    """Pin the new (2026-06-10) URL + Vault path so a future refactor
    can't silently revert to the dead runpod proxy URL."""
    from services.llm_registry import PROVIDERS

    p = PROVIDERS["runpod_vllm"]
    assert p["base_url"] == "https://saikat-llm-medium-fast.yral.com/v1"
    assert p["vault"]["path"] == "saikat-llm-medium-fast-bearer-token"
    assert p["vault"]["key"] == "token"
    assert "secret_path" in p
    assert "env_fallback" in p
