"""Redis URL resolver — file-first, env-var fallback.

Pin the resolution contract so a refactor can't silently flip the
precedence (which would silently send container traffic to the
no-credential Redis env-var path instead of the Swarm-secret file)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_file_takes_priority_over_env(tmp_path, monkeypatch):
    """Swarm secret path is the production source. If a developer
    accidentally sets REDIS_URL env in a container that ALSO mounts the
    secret, the secret-file value must win."""
    from redis_config import get_redis_url

    secret = tmp_path / "REDIS_URL"
    secret.write_text("redis://:from-file@redis-primary:6379\n")
    monkeypatch.setenv("REDIS_URL_FILE", str(secret))
    monkeypatch.setenv("REDIS_URL", "redis://:from-env@should-not-win:6379")
    assert get_redis_url() == "redis://:from-file@redis-primary:6379"


def test_env_fallback_when_no_file(tmp_path, monkeypatch):
    """Local dev / CI path — no Swarm secret available, env var wins."""
    from redis_config import get_redis_url

    monkeypatch.setenv("REDIS_URL_FILE", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("REDIS_URL", "redis://:env-only@localhost:6379")
    assert get_redis_url() == "redis://:env-only@localhost:6379"


def test_none_when_neither_set(tmp_path, monkeypatch):
    """No file, no env — return None so callers can degrade gracefully
    (session_memory / rate_limiter / websocket_manager all have a
    None-handling path)."""
    from redis_config import get_redis_url

    monkeypatch.setenv("REDIS_URL_FILE", str(tmp_path / "does-not-exist"))
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert get_redis_url() is None


def test_empty_file_treated_as_unset(tmp_path, monkeypatch):
    """Whitespace-only file = secret hasn't been populated yet. Treat
    as 'unset' so the env-var fallback still gets a chance."""
    from redis_config import get_redis_url

    secret = tmp_path / "REDIS_URL"
    secret.write_text("   \n")
    monkeypatch.setenv("REDIS_URL_FILE", str(secret))
    monkeypatch.setenv("REDIS_URL", "redis://:fallback@redis-primary:6379")
    assert get_redis_url() == "redis://:fallback@redis-primary:6379"


def test_default_secret_path():
    """Pin the default mount path. Swarm always mounts secrets at
    /run/secrets/<name>; changing this default breaks production
    silently (file not found → env fallback → no Redis)."""
    from redis_config import DEFAULT_SECRET_PATH

    assert DEFAULT_SECRET_PATH == "/run/secrets/REDIS_URL"


def test_all_three_call_sites_use_get_redis_url():
    """Source-inspection: rate_limiter, session_memory, and
    websocket_manager all read Redis URL via the same helper. Without
    this, any one of them could drift back to a direct os.environ call
    and silently bypass the file-first path."""
    repo = Path(__file__).resolve().parent.parent
    for path in (
        "app/rate_limiter.py",
        "app/services/session_memory.py",
        "app/services/websocket_manager.py",
    ):
        src = (repo / path).read_text()
        assert "from redis_config import get_redis_url" in src, f"{path} missing import"
        assert "get_redis_url()" in src, f"{path} missing call"
