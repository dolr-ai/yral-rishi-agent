"""Phase 2 — V2 S3 ETL fetcher pinning.

The live S3-fetch behavior is exercised after deploy with the operator
mounted credentials; here we pin static configuration + the pure
helpers (credential loader, filename regex, CSV parse, table specs).
"""

import gzip


# ─── table specs ──────────────────────────────────────────────────────────


def test_synced_tables_in_dependency_order():
    """ai_influencers (no FK), conversations (FK to it), messages (FK
    to conversations). rishi-1's exporter doesn't care about order; V2
    apply does, since CSV-per-file means we apply one table per file."""
    from services.etl_chat_ai import SYNCED_TABLES

    names = [t["name"] for t in SYNCED_TABLES]
    assert names == ["ai_influencers", "conversations", "messages"]


def test_table_specs_have_required_keys():
    from services.etl_chat_ai import SYNCED_TABLES

    for spec in SYNCED_TABLES:
        assert "name" in spec
        assert isinstance(spec["columns"], list) and len(spec["columns"]) > 0
        assert "id" in spec["columns"]
        assert "created_at" in spec["columns"]
        assert spec["id_column"] in spec["columns"]


# ─── interval + S3 layout ─────────────────────────────────────────────────


def test_interval_5_min():
    """Below 1 min would hammer S3; above 15 stales out."""
    from services.etl_chat_ai import SYNC_INTERVAL_SEC

    assert 60 <= SYNC_INTERVAL_SEC <= 15 * 60


def test_s3_layout_matches_exporter():
    """The fetcher and the rishi-1 exporter must agree on bucket +
    prefix. Drift here = silent data gap."""
    from services.etl_chat_ai import S3_BUCKET, S3_PREFIX, S3_ENDPOINT

    assert S3_BUCKET == "rishi-yral"
    assert S3_PREFIX == "yral-chat-ai/incremental-sync"
    assert S3_ENDPOINT.startswith("https://")


def test_heartbeat_threshold_reasonable():
    """rishi-1 emits a heartbeat every 5 min; 15 min = 3 missed ticks
    before we flag stale. Below 10 min = false positives from network
    jitter. Above 30 = exporter could be dead an hour and we wouldn't
    notice."""
    from services.etl_chat_ai import HEARTBEAT_STALE_SEC

    assert 10 * 60 <= HEARTBEAT_STALE_SEC <= 30 * 60


# ─── filename regex ───────────────────────────────────────────────────────


def test_filename_regex_matches_exporter_output():
    """The exporter writes <YYYYMMDDTHHMMSSZ>_<table>.csv.gz. If the
    regex drifts, the fetcher silently skips files."""
    from services.etl_chat_ai import _FILENAME_RE

    m = _FILENAME_RE.match("20260529T144530Z_messages.csv.gz")
    assert m is not None
    assert m["table"] == "messages"
    assert m["ts"] == "20260529T144530Z"

    # Heartbeat + STUCK markers must NOT match — they're handled
    # separately and would crash the apply path.
    assert _FILENAME_RE.match("_heartbeat") is None
    assert _FILENAME_RE.match("STUCK") is None
    # Old/garbage filenames don't match
    assert _FILENAME_RE.match("2026-05-29_messages.csv.gz") is None
    assert _FILENAME_RE.match("20260529T144530Z_messages.csv") is None


# ─── credentials loader ───────────────────────────────────────────────────


def test_s3_creds_loader_missing_file_returns_none(tmp_path, monkeypatch):
    """No mounted secret = ETL disabled gracefully. Loop logs once,
    keeps idling. This is the pre-activation state."""
    from services.etl_chat_ai import _load_s3_credentials

    monkeypatch.setenv("CHAT_AI_S3_CREDENTIALS_FILE", str(tmp_path / "nope"))
    assert _load_s3_credentials() is None


def test_s3_creds_loader_parses_shell_style(tmp_path, monkeypatch):
    from services.etl_chat_ai import _load_s3_credentials

    cred_file = tmp_path / "creds"
    cred_file.write_text(
        "# header\n"
        "BACKUP_S3_ACCESS_KEY=key\n"
        "BACKUP_S3_SECRET_KEY='secret-with-quotes'\n"
        "EXTRA_IGNORED=whatever\n"
    )
    monkeypatch.setenv("CHAT_AI_S3_CREDENTIALS_FILE", str(cred_file))
    creds = _load_s3_credentials()
    assert creds["BACKUP_S3_ACCESS_KEY"] == "key"
    assert creds["BACKUP_S3_SECRET_KEY"] == "secret-with-quotes"


def test_s3_creds_loader_missing_keys_returns_none(tmp_path, monkeypatch, caplog):
    """Half-configured file is treated as 'not mounted' rather than
    erroring — the loop stays alive and the operator can fix it
    without restarting V2."""
    from services.etl_chat_ai import _load_s3_credentials

    cred_file = tmp_path / "creds"
    cred_file.write_text("BACKUP_S3_ACCESS_KEY=only-this\n")
    monkeypatch.setenv("CHAT_AI_S3_CREDENTIALS_FILE", str(cred_file))
    assert _load_s3_credentials() is None


# ─── CSV parser ───────────────────────────────────────────────────────────


def test_parse_csv_extracts_header_and_rows():
    """The header line is treated as authoritative for column ordering
    in the gzipped CSV. A malformed header would silently misalign
    column values during apply."""
    from services.etl_chat_ai import _parse_csv

    csv_text = "id,content,created_at\nabc,hello,2026-05-29T14:00:00Z\ndef,world,2026-05-29T14:01:00Z\n"
    body = gzip.compress(csv_text.encode())
    header, rows = _parse_csv(body)
    assert header == ["id", "content", "created_at"]
    assert len(rows) == 2
    assert rows[0] == ["abc", "hello", "2026-05-29T14:00:00Z"]


def test_parse_csv_empty_file_returns_empty():
    from services.etl_chat_ai import _parse_csv

    body = gzip.compress(b"")
    header, rows = _parse_csv(body)
    assert header == [] and rows == []


def test_parse_csv_header_only_no_rows():
    from services.etl_chat_ai import _parse_csv

    body = gzip.compress(b"id,content,created_at\n")
    header, rows = _parse_csv(body)
    assert header == ["id", "content", "created_at"]
    assert rows == []


# ─── Option A skip-and-log (signatures + helpers exist) ──────────────────


def test_apply_csv_returns_skip_dict_shape():
    """Option A semantics — _apply_csv must return rows_applied +
    skipped_conflict + skipped_orphan, not a bare int. Caller wires
    those into etl_skipped_rows."""
    import asyncio
    from services.etl_chat_ai import _apply_csv

    # Empty data_rows is the cheap unit case — no DB needed.
    result = asyncio.run(_apply_csv(None, "messages", ["id"], []))
    assert set(result.keys()) == {
        "rows_applied",
        "skipped_conflict",
        "skipped_orphan",
    }
    assert result["rows_applied"] == 0
    assert result["skipped_conflict"] == []
    assert result["skipped_orphan"] == []


def test_record_skipped_exists_and_signature():
    """Bulk-insert helper. Empty list short-circuits without touching DB."""
    import asyncio
    from services.etl_chat_ai import _record_skipped

    # Empty row_ids list must short-circuit (no DB call) — proves the
    # early-return path so an empty file doesn't crash on missing pool.
    asyncio.run(_record_skipped(None, "fname", "messages", [], "orphan"))


def test_get_skipped_exists():
    """Helper for /admin/etl-skipped — used by the audit endpoint."""
    from services.etl_chat_ai import get_skipped

    assert callable(get_skipped)


def test_advance_cursor_removed_in_favor_of_derived():
    """_advance_cursor + _parse_until_iso were removed — cursors are now
    derived from etl_processed_files at query time inside get_status.
    Single source of truth, no more asyncpg type quirks on the write
    path. If a future refactor adds them back, this test fails and
    you have to think about it."""
    from services import etl_chat_ai

    assert not hasattr(etl_chat_ai, "_advance_cursor")
    assert not hasattr(etl_chat_ai, "_parse_until_iso")


def test_status_dict_includes_skip_fields():
    """get_status() shape must include skipped_rows_24h + skipped_by_reason
    so the /admin/etl-status JSON contract stays stable."""
    import inspect
    from services.etl_chat_ai import get_status

    # Inspect the source to confirm the return literal mentions the new
    # keys (hermetic — no DB roundtrip required for this contract check).
    source = inspect.getsource(get_status)
    assert "skipped_rows_24h" in source
    assert "skipped_by_reason" in source
    assert "skipped_by_table" in source


def test_status_derives_cursors_from_processed_files():
    """The cursor SQL in get_status must source from etl_processed_files,
    not the vestigial etl_sync_state. Catches accidental revert."""
    import inspect
    from services.etl_chat_ai import get_status

    source = inspect.getsource(get_status)
    # Derived approach uses s3_metadata->>'until' over the join
    assert "s3_metadata->>'until'" in source
    # Old approach used a direct SELECT from etl_sync_state for cursors
    assert "FROM etl_sync_state" not in source
