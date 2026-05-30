"""Phase 24.5 — daily email digest framework.

Source-inspection + pure-function tests. Live SMTP send is exercised
manually after deploy via /admin/email-digest/preview?force=1."""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def test_digest_target_is_08_ist():
    """ADHD rule: digest lands before Rishi's work window. 08:00 IST
    = 02:30 UTC. If this shifts accidentally, the email starts arriving
    too early/late."""
    src = _read("app/services/email_digest.py")
    assert "DIGEST_TARGET_HOUR_UTC = 2" in src
    assert "DIGEST_TARGET_MINUTE_UTC = 30" in src


def test_digest_target_email_is_rishi():
    """Default recipient is Rishi's address. SMTP config can override
    DIGEST_TO_EMAIL via env, but the default stays."""
    src = _read("app/services/email_digest.py")
    assert 'DIGEST_TO_EMAIL = os.environ.get("DIGEST_TO_EMAIL", "rishi@gobazzinga.io")' in src


def test_digest_renders_both_plain_and_html():
    """Plain-text body is most reliable; HTML body is easier to skim.
    Email clients pick best."""
    src = _read("app/services/email_digest.py")
    assert "def render_plain" in src
    assert "def render_html" in src


def test_digest_has_six_placeholder_sections():
    """Same systems as the dashboard tiles. If they drift apart, Rishi
    sees inconsistent state across the two surfaces."""
    src = _read("app/services/email_digest.py")
    for planned in (
        "PR Phase 19.1",  # rate limits
        "PR Phase 19.2",  # cost breaker
        "PR Phase 24.1",  # secret scan
        "PR Phase 24.2",  # safety drill
        "PR Phase 24.3",  # dep vuln
        "PR I10",  # backup restore drill
    ):
        assert planned in src, f"missing section for {planned}"


def test_digest_builds_even_without_smtp():
    """The cron loop must keep building + recording digests even when
    SMTP isn't configured yet — so the preview endpoint works from
    day one, and when SMTP is wired later we don't lose historical days."""
    src = _read("app/services/email_digest.py")
    assert "SMTP_HOST not configured" in src
    # The function path that records the run must run regardless of
    # whether SMTP send succeeded
    assert "await _record_run(pool, digest, sent, error)" in src


def test_digest_idempotent_per_date():
    """If the loop wakes twice in the 02:30-02:35 window, it must NOT
    send two emails. The for_date check enforces single-fire."""
    src = _read("app/services/email_digest.py")
    assert "WHERE for_date = $1 LIMIT 1" in src


def test_digest_history_bounded():
    """The runs table grows by one row per day. Trim cap keeps it
    bounded so it doesn't grow unbounded over months."""
    src = _read("app/services/email_digest.py")
    assert "DIGEST_HISTORY_KEEP = 30" in src
    assert "DELETE FROM email_digest_runs" in src


def test_main_py_wires_digest_loop():
    """If the create_task line is missing, the cron never runs. Pin it."""
    src = _read("app/main.py")
    assert "from services.email_digest import digest_loop" in src
    assert "digest_task = asyncio.create_task(digest_loop())" in src
    # Shutdown cleanup symmetry — without await the cancel doesn't fully
    # drain and we get noisy shutdown logs
    assert "digest_task.cancel()" in src
    assert "await digest_task" in src


def test_preview_endpoint_exists():
    """Rishi reads the digest in browser via the preview endpoint when
    email goes to spam or SMTP isn't yet wired."""
    src = _read("app/routes/admin_dashboard.py")
    assert '@router.get("/admin/email-digest/preview")' in src


def test_dashboard_includes_email_digest_tile():
    """Every protective system surfaces on the dashboard AND email.
    The digest itself counts — must have its own dashboard tile so
    Rishi sees whether the cron + SMTP are healthy."""
    src = _read("app/routes/admin_dashboard.py")
    assert "_email_digest_tile" in src


def test_migration_024_creates_email_digest_runs():
    src = _read("migrations/024_email_digest_runs.sql")
    assert "CREATE TABLE IF NOT EXISTS email_digest_runs" in src
    assert "rendered_at TIMESTAMPTZ NOT NULL" in src
    assert "for_date TEXT NOT NULL" in src
    assert "body_json JSONB NOT NULL" in src
    assert "sent BOOLEAN" in src


def test_record_run_parses_rendered_at_to_datetime():
    """Regression for the asyncpg TIMESTAMPTZ codec bug (this hit
    production on first force-build immediately after #230 deploy).
    The SQL ::timestamptz cast doesn't save us — asyncpg validates
    param types client-side before Postgres sees the cast. Must pass
    a datetime instance, not a string."""
    src = _read("app/services/email_digest.py")
    # The helper that converts string → tz-aware datetime
    assert "def _parse_rendered_at" in src
    # And the call site uses it instead of passing the raw string
    assert '_parse_rendered_at(digest["rendered_at"])' in src
    # The dead ::timestamptz cast is removed (asyncpg never sees it)
    assert "$1::timestamptz" not in src


def test_subject_prefix_makes_filtering_easy():
    """Rishi can set a Gmail filter on the prefix to bypass spam."""
    src = _read("app/services/email_digest.py")
    assert 'DIGEST_SUBJECT_PREFIX = "[yral-rishi-agent]"' in src
