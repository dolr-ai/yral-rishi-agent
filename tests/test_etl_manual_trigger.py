"""Source-pin tests for scripts/etl-manual-trigger.sh + the
`--force` flag in scripts/incremental_export.py.

The wrapper is invoked by the etl-drain.yml workflow over SSH; a
silent regression in either piece would leave the workflow hanging
(wrapper missing) or emit a stale integrity payload (force flag lost).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "scripts" / "etl-manual-trigger.sh"
EXPORTER = REPO / "scripts" / "incremental_export.py"


def test_wrapper_exists_and_executable():
    """The workflow invokes `bash ~/.etl-export/etl-manual-trigger.sh`
    so the file shipping in the repo is the source of truth."""
    assert WRAPPER.exists()
    # In-repo perms: should be executable (deploy step copies it as-is).
    import os

    assert os.access(WRAPPER, os.X_OK), "wrapper must be executable"


def test_wrapper_uses_flock():
    """The wrapper takes a non-blocking flock so two concurrent manual
    triggers serialize instead of racing each other on state.json."""
    body = WRAPPER.read_text()
    assert "flock" in body
    # Non-blocking — refuse rather than queue (workflow polls)
    assert "flock -n" in body


def test_wrapper_calls_exporter_with_force():
    """The whole point of the wrapper is invoking the exporter in
    --force mode. Anything else and the integrity layers go stale."""
    body = WRAPPER.read_text()
    assert "--force" in body
    assert "incremental_export.py" in body


def test_wrapper_exits_78_on_config_refusal():
    """Same convention as scripts/ci/run-migrations.sh: EX_CONFIG = 78
    for "config error, not transient" so the workflow can distinguish
    "wrapper missing" from "export ran but failed."""
    body = WRAPPER.read_text()
    assert "exit 78" in body
    # The conditions that trigger 78: missing script + lockfile unwritable + concurrent run
    assert "exporter script not at" in body
    assert "another manual trigger is in progress" in body


def test_wrapper_logs_to_file_for_post_hoc_review():
    """The workflow log is one place; a host-side log on rishi-1 is
    the second. Without it, a successful drain has no trail an
    operator can inspect days later without re-running the workflow."""
    body = WRAPPER.read_text()
    assert "LOG_FILE=" in body
    assert "tee -a" in body


# ─── exporter --force flag ───────────────────────────────────────────────


def test_exporter_has_force_flag():
    """argparse-based --force flag exists and is documented."""
    body = EXPORTER.read_text()
    assert "--force" in body
    assert "argparse" in body
    assert "force_all_integrity" in body


def test_force_bypasses_all_time_gates():
    """The force flag must bypass each of the 3 integrity time-gates
    (sentinel + hourly + sample). Each CALL site (not the function
    definition) must be inside a `force_all_integrity or
    _is_overdue(...)` branch so a partial fix can't ship and look
    correct.

    `body.rfind(call)` lands on the LAST occurrence — i.e. the call
    inside run_once, not the function `def emit_<layer>_integrity(...)`
    earlier in the file."""
    body = EXPORTER.read_text()
    for layer in ("sentinel", "hourly", "sample"):
        call = f"emit_{layer}_integrity("
        pos = body.rfind(call)
        assert pos != -1, f"missing emit_{layer}_integrity call site"
        window = body[max(0, pos - 600) : pos]
        assert "force_all_integrity" in window, (
            f"emit_{layer}_integrity is not gated on force_all_integrity"
        )
        assert f"_last_{layer}_emit" in window, (
            f"emit_{layer}_integrity gate doesn't reference the time-gate constant"
        )


def test_exporter_main_dispatches_force_to_run_once():
    """argparse → run_once(force_all_integrity=args.force). If this
    breaks, --force becomes a no-op silently."""
    body = EXPORTER.read_text()
    assert "run_once(force_all_integrity=args.force)" in body
