"""Automated verification for AC8 / M-AC8-idle.

The spec's Manual Verification M-AC8-idle was defined as a human
procedure: stop activity for 35 seconds and expect the footer to
append ``— session idle``. These tests exercise the same logic
programmatically by monkeypatching ``time.monotonic`` and stubbing
the footer widget so the test completes in milliseconds without
needing a full Textual app lifecycle.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentlens import app as app_module
from agentlens.app import AgentlensApp


def _make_app(tmp_path: Path) -> AgentlensApp:
    """Build an AgentlensApp without entering its Textual lifecycle.

    We manually populate the fields that ``_refresh_idle_footer``
    touches so the method can run in isolation.
    """
    session = tmp_path / "sample.jsonl"
    session.write_text("")
    app = AgentlensApp(
        session_override=session,
        state_dir_override=tmp_path / "state-absent",
        no_attach=True,
    )
    # Manually wire the bits _refresh_idle_footer reads from / writes to.
    # MagicMock lets us assert on .update(...) calls without mounting.
    app._footer = MagicMock()
    app.active_session_path = session
    app.locator_reason = "slug"
    # Flowchart is optional — the footer suffix helper guards for None.
    app._flowchart = None
    return app


def _last_footer_text(app: AgentlensApp) -> str:
    """Return the most recent string passed to footer.update()."""
    assert app._footer is not None
    calls = app._footer.update.call_args_list
    assert calls, "footer.update was never called"
    args, _ = calls[-1]
    assert args, "footer.update called with no arguments"
    return str(args[0])


def test_footer_shows_session_idle_after_30s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-AC8-idle: after >30s without events, the footer appends
    ``— session idle``.
    """
    app = _make_app(tmp_path)
    app.last_event_monotonic = 100.0
    monkeypatch.setattr(app_module.time, "monotonic", lambda: 141.0)

    app._refresh_idle_footer()

    assert "session idle" in _last_footer_text(app)


def test_footer_hides_idle_within_30s_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative case: before 30s the footer must NOT say idle."""
    app = _make_app(tmp_path)
    app.last_event_monotonic = 100.0
    monkeypatch.setattr(app_module.time, "monotonic", lambda: 125.0)

    app._refresh_idle_footer()

    assert "session idle" not in _last_footer_text(app)


def test_footer_skips_idle_when_no_event_ever_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Edge case: if last_event_monotonic is still 0 (no event seen),
    the footer must NOT claim idle — otherwise every fresh session
    would start in the idle state immediately.
    """
    app = _make_app(tmp_path)
    # last_event_monotonic left at its default 0.0.
    monkeypatch.setattr(app_module.time, "monotonic", lambda: 10_000.0)

    app._refresh_idle_footer()

    assert "session idle" not in _last_footer_text(app)


def test_footer_idle_threshold_is_exactly_thirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The idle threshold is > 30s (strict). 30s exactly stays non-idle."""
    app = _make_app(tmp_path)
    app.last_event_monotonic = 100.0

    # 30 seconds — still not idle.
    monkeypatch.setattr(app_module.time, "monotonic", lambda: 130.0)
    app._refresh_idle_footer()
    assert "session idle" not in _last_footer_text(app)

    # 30.001 seconds — crosses the threshold.
    monkeypatch.setattr(app_module.time, "monotonic", lambda: 130.001)
    app._refresh_idle_footer()
    assert "session idle" in _last_footer_text(app)
