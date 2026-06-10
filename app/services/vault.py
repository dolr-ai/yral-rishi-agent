"""HashiCorp Vault client — read secrets at runtime from vault.yral.com.

Used for secrets we don't own (e.g. Saikat's bearer token for the
runpod_vllm endpoint). Direct swarm-secret rotation requires Saikat
SSH'ing into our cluster, which violates the ownership boundary
(we own the agent service; Saikat owns Vault + the LLM serving).
With Vault, Saikat rotates in his domain and our service picks up
the new value automatically on the next read.

Resolution chain in llm_registry._resolve_api_key:
    1. /run/secrets/<NAME> (file)
    2. Vault (this module)
    3. <NAME> (env var)
    4. RuntimeError

We sit between file + env so the file-based path stays the
authoritative source for secrets WE own (gemini, openrouter, etc.)
and Vault only kicks in for entries that explicitly declare a
`vault` block in PROVIDERS.

Bootstrap: the VAULT_TOKEN itself is a swarm secret at
/run/secrets/vault_token. Saikat (Vault admin) generates a
read-only periodic token bound to a policy covering the paths we
need; we deploy it via .github/workflows/deploy-vault-token.yml.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_VAULT_ADDR = "https://vault.yral.com"
VAULT_TOKEN_SECRET_PATH = "/run/secrets/vault_token"


@lru_cache(maxsize=1)
def get_vault_client():
    # Token first so a missing-token error surfaces clearly even when
    # the optional hvac dep isn't yet installed in the environment
    # (e.g. a fresh dev clone where pip install hasn't run).
    token = _read_token()
    if not token:
        raise RuntimeError(
            "VAULT_TOKEN not set — checked /run/secrets/vault_token + VAULT_TOKEN env"
        )

    try:
        import hvac
    except ImportError as e:
        raise ImportError(
            "hvac is required for Vault integration — add `hvac` to requirements.txt"
        ) from e

    client = hvac.Client(
        url=os.environ.get("VAULT_ADDR", DEFAULT_VAULT_ADDR),
        token=token,
        namespace=os.environ.get("VAULT_NAMESPACE") or None,
    )

    if not client.is_authenticated():
        raise RuntimeError("Vault token rejected — token may be expired or revoked")

    return client


def _read_token() -> str | None:
    if os.path.exists(VAULT_TOKEN_SECRET_PATH):
        try:
            with open(VAULT_TOKEN_SECRET_PATH) as f:
                val = f.read().strip()
            if val:
                return val
        except OSError:
            pass
    return os.environ.get("VAULT_TOKEN")


def get_secret(path: str, key: str, mount_point: str = "secret") -> Any:
    """Read a single field from a KV-v2 secret at vault.yral.com.

    `path` is the secret name (e.g. "saikat-llm-medium-fast-bearer-token").
    `key` is the field inside the secret (e.g. "token").
    `mount_point` is the KV engine mount; defaults to "secret" (Vault's
    standard).
    """
    client = get_vault_client()
    resp = client.secrets.kv.v2.read_secret_version(path=path, mount_point=mount_point)
    return resp["data"]["data"][key]


def reset_client_for_tests() -> None:
    """Test-only helper — clears the cached client so tests can swap
    VAULT_TOKEN between calls."""
    get_vault_client.cache_clear()
