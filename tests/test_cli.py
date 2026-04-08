"""CLI tests — exit code propagation."""
from __future__ import annotations

import pytest
from harness_visual import cli


def test_cli_main_returns_zero_on_clean_run():
    # --self-test + --no-attach should render one frame then exit.
    rc = cli.main(["--self-test", "--no-attach"])
    assert rc == 0


def test_cli_main_preserves_nonzero_exit(monkeypatch):
    # If the app's run() returns a non-zero int, main must propagate it.
    from harness_visual.app import HarnessVisualApp
    monkeypatch.setattr(HarnessVisualApp, "run", lambda self: 42)
    rc = cli.main(["--self-test", "--no-attach"])
    assert rc == 42


def test_cli_main_returns_zero_when_run_returns_none(monkeypatch):
    # Textual App.run() can legitimately return None when exit() was never called.
    from harness_visual.app import HarnessVisualApp
    monkeypatch.setattr(HarnessVisualApp, "run", lambda self: None)
    rc = cli.main(["--self-test", "--no-attach"])
    assert rc == 0
