"""Tests for ActivityRate — AC2, AC3, AC4, AC11, AC12, width-stability.

All time-sensitive tests use ``monkeypatch.setattr`` on the module-level
``time`` import inside ``agentlens.activity_rate``, or pass an explicit
``now=`` argument to ``ActivityRate.record()`` to avoid real wall-clock
dependency.
"""

from __future__ import annotations

import pytest

from agentlens.activity_rate import SPARKLINE_CHARS, ActivityRate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make(t0: float = 1_000.0) -> tuple[ActivityRate, list[float]]:
    """Return a fresh ActivityRate whose _last_second is set via a dummy record."""
    ar = ActivityRate()
    # Force _last_second to a known value without using private internals:
    # advance to t0 by recording a dummy event, then drain that count so we
    # start clean.  We access _counts only to zero it out — acceptable in tests.
    ar.record(now=t0)
    ar._counts[-1] = 0
    return ar, [t0]


# ---------------------------------------------------------------------------
# AC2, AC4 — zero-activity render
# ---------------------------------------------------------------------------

def test_empty_render_is_flat_baseline() -> None:
    """Fresh instance → render() returns '▁▁▁▁▁▁▁▁ peak: 0/s' (AC2, AC4)."""
    ar = ActivityRate()
    result = ar.render()
    assert result == "▁▁▁▁▁▁▁▁ peak: 0/s"


# ---------------------------------------------------------------------------
# AC4 — bucket rotation
# ---------------------------------------------------------------------------

def test_bucket_rotation_drops_old_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Events recorded at t=0 must be gone after 90s elapsed (AC4)."""
    import agentlens.activity_rate as ar_mod

    t_now: list[float] = [1_000.0]
    monkeypatch.setattr(ar_mod.time, "monotonic", lambda: t_now[0])

    ar = ActivityRate()
    for _ in range(5):
        ar.record()

    # Advance 90s — well past the 60s window.
    t_now[0] = 1_090.0
    result = ar.render()
    assert "peak: 0/s" in result


# ---------------------------------------------------------------------------
# AC1, AC3 — peak tracking
# ---------------------------------------------------------------------------

def test_peak_tracking_within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bursts at t=0 and t=20 still visible at t=25; tallest bar == '█' (AC1, AC3)."""
    import agentlens.activity_rate as ar_mod

    t_now: list[float] = [2_000.0]
    monkeypatch.setattr(ar_mod.time, "monotonic", lambda: t_now[0])

    ar = ActivityRate()

    # Record 50 events in second 0.
    for _ in range(50):
        ar.record()

    # Advance to t=20, record another burst.
    t_now[0] = 2_020.0
    for _ in range(30):
        ar.record()

    # Observe at t=25 — both bursts still inside the 60s window.
    t_now[0] = 2_025.0
    result = ar.render()

    bars = result.split(" peak: ")[0]
    peak_str = result.split(" peak: ")[1]

    # Peak should be > 0.
    assert peak_str != "0/s"
    # Tallest bar must be the last SPARKLINE_CHARS entry.
    assert "█" in bars


# ---------------------------------------------------------------------------
# AC1, AC3 — Unicode char mapping boundaries
# ---------------------------------------------------------------------------

def test_unicode_char_mapping_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every bar char is a valid SPARKLINE glyph; max bar == '█' (AC1, AC3)."""
    import agentlens.activity_rate as ar_mod

    base = 3_000.0
    t_now: list[float] = [base]
    monkeypatch.setattr(ar_mod.time, "monotonic", lambda: t_now[0])

    ar = ActivityRate()
    # Record at increasing rates across 8 distinct seconds so each display
    # bucket gets a different count.
    counts_per_second = [1, 5, 10, 20, 30, 40, 60, 80]
    for i, count in enumerate(counts_per_second):
        t_now[0] = base + i
        for _ in range(count):
            ar.record()

    # Observe at t = base + 8 (still within window).
    t_now[0] = base + 8
    result = ar.render()
    bars = result.split(" peak: ")[0]
    assert len(bars) == 8
    for ch in bars:
        assert ch in SPARKLINE_CHARS, f"unexpected char {ch!r}"
    assert "█" in bars


# ---------------------------------------------------------------------------
# AC12 — peak cap
# ---------------------------------------------------------------------------

def test_peak_cap_at_99(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enough events in one second to exceed 99/s rate → render contains '99+/s' (AC12).

    peak_rate = round(peak_bucket_count / slots_per_bar) where slots_per_bar = 7.5.
    To exceed 99/s we need peak_bucket_count > 99 * 7.5 = 742.5, i.e. ≥ 743 events
    in the same 8-second display bucket.  Record 800 events at the same second.
    """
    import agentlens.activity_rate as ar_mod

    t_now: list[float] = [5_000.0]
    monkeypatch.setattr(ar_mod.time, "monotonic", lambda: t_now[0])

    ar = ActivityRate()
    for _ in range(800):
        ar.record()

    result = ar.render()
    assert "99+/s" in result


# ---------------------------------------------------------------------------
# AC11 — never-raise on time regression
# ---------------------------------------------------------------------------

def test_never_raise_on_time_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monotonic returning t=100 then t=50 must not raise (AC11)."""
    import agentlens.activity_rate as ar_mod

    t_now: list[float] = [100.0]
    monkeypatch.setattr(ar_mod.time, "monotonic", lambda: t_now[0])

    ar = ActivityRate()
    ar.record()

    # Regress clock.
    t_now[0] = 50.0
    ar.record()          # must not raise
    result = ar.render()  # must not raise
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# AC11 — never-raise on edge inputs
# ---------------------------------------------------------------------------

def test_never_raise_on_record_none_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    """record(now=float('nan')) and record(now=None) must not raise (AC11)."""
    import agentlens.activity_rate as ar_mod

    monkeypatch.setattr(ar_mod.time, "monotonic", lambda: 200.0)

    ar = ActivityRate()
    ar.record(now=float("nan"))   # nan → int(nan) raises, should be swallowed
    ar.record(now=None)           # falls back to time.monotonic()
    result = ar.render()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Width stability
# ---------------------------------------------------------------------------

def test_render_width_is_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    """render() width is stable: 8 bars + ' peak: ' + label (digits vary by cap).

    The spec guarantees the segment is 18–20 chars (plus the 2-space joiner
    handled by app.py).  We just assert the structure is consistent.
    """
    import agentlens.activity_rate as ar_mod

    # Zero-activity case — no monkeypatch needed (fresh instance renders flat).
    ar_zero = ActivityRate()
    zero_result = ar_zero.render()
    assert zero_result == "▁▁▁▁▁▁▁▁ peak: 0/s"
    parts = zero_result.split(" peak: ")
    assert len(parts) == 2
    assert len(parts[0]) == 8

    # High-activity case — pin clock so render() doesn't wipe counts.
    t_now: list[float] = [7_000.0]
    monkeypatch.setattr(ar_mod.time, "monotonic", lambda: t_now[0])

    ar_high = ActivityRate()
    for _ in range(200):
        ar_high.record()
    high_result = ar_high.render()
    parts_high = high_result.split(" peak: ")
    assert len(parts_high) == 2
    assert len(parts_high[0]) == 8
    assert parts_high[1].endswith("/s")
