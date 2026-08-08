"""Guard: every watchdog module must be COPY'd into the image.

The Dockerfile lists modules by name rather than globbing, so adding a
module without adding its COPY line builds an image that dies on
ImportError the moment it boots. Nothing else catches it — the test
suite imports from the source tree via `pythonpath`, and the image build
only runs on main.

That is exactly what happened when heartbeat.py was added (2026-08-08):
the full suite passed against a Dockerfile that would have crash-looped
the watchdog in production. This test closes that gap.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WATCHDOG_DIR = REPO / "watchdog"
DOCKERFILE = WATCHDOG_DIR / "Dockerfile"


def _copied_modules() -> set[str]:
    """Filenames on `COPY <src> .` lines. Only single-source COPY lines
    matter here — the Dockerfile uses one per module."""
    text = DOCKERFILE.read_text()
    return set(re.findall(r"^COPY\s+(\S+\.py)\s", text, flags=re.MULTILINE))


def _source_modules() -> set[str]:
    """Every top-level .py in watchdog/. The entrypoint imports them as
    flat top-level modules (WORKDIR is the image root), so all of them
    have to be present."""
    return {p.name for p in WATCHDOG_DIR.glob("*.py")}


def test_every_watchdog_module_is_copied_into_the_image():
    missing = _source_modules() - _copied_modules()
    assert not missing, (
        f"watchdog/Dockerfile is missing COPY lines for: {sorted(missing)}. "
        "The image would boot and die on ImportError. Add `COPY <name> .`"
    )


def test_no_copy_line_points_at_a_deleted_module():
    """The mirror failure: a COPY for a module that no longer exists makes
    the image build fail outright."""
    stale = _copied_modules() - _source_modules()
    assert not stale, f"watchdog/Dockerfile COPYs non-existent modules: {sorted(stale)}"


def test_heartbeat_is_shipped():
    """Named explicitly because it is the module whose omission motivated
    this file — a regression here silently disables the only alert path
    that survives a Sentry outage."""
    assert "heartbeat.py" in _copied_modules()
