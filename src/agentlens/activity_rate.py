"""Rolling 60s events/sec aggregator for the footer sparkline.

Never-raise: all public methods swallow exceptions and fall back to
zero-activity render rather than propagate (parser.py rule).
"""
from __future__ import annotations

import time
from collections import deque

WINDOW_SECONDS = 60
NUM_BARS = 8
# 8 glyphs — index 0 is the baseline "▁", 1..7 ramp up through "█".
# Empty buckets render as ▁ (index 0) so the sparkline width is stable.
SPARKLINE_CHARS = "▁▂▃▄▅▆▇█"  # 8 levels; baseline reuses chars[0]
PEAK_CAP = 99


class ActivityRate:
    __slots__ = ("_counts", "_last_second", "_window")

    def __init__(self, window: int = WINDOW_SECONDS) -> None:
        self._window = max(1, int(window))
        self._counts: deque[int] = deque([0] * self._window, maxlen=self._window)
        self._last_second: int = int(time.monotonic())

    def record(self, now: float | None = None) -> None:
        try:
            t = int(time.monotonic() if now is None else now)
            self._advance(t)
            self._counts[-1] += 1
        except Exception:
            return  # never-raise

    def render(self) -> str:
        """Returns e.g. '▁▂▃▄▆█▅▂ peak: 32/s'. Always 8 bars + label."""
        try:
            self._advance(int(time.monotonic()))
            buckets = self._aggregate_to_bars()
            peak_count = max(buckets) if buckets else 0
            # peak rate is events/sec in the hottest bucket
            slots_per_bar = self._window / NUM_BARS  # 7.5 for 60/8
            peak_rate = round(peak_count / slots_per_bar) if peak_count else 0
            if peak_count == 0:
                bars = SPARKLINE_CHARS[0] * NUM_BARS
            else:
                bars = "".join(
                    SPARKLINE_CHARS[min(7, round(7 * b / peak_count))]
                    for b in buckets
                )
            label = f"{PEAK_CAP}+/s" if peak_rate > PEAK_CAP else f"{peak_rate}/s"
            return f"{bars} peak: {label}"
        except Exception:
            return SPARKLINE_CHARS[0] * NUM_BARS + " peak: 0/s"

    # --- internals --------------------------------------------------------

    def _advance(self, now_second: int) -> None:
        delta = now_second - self._last_second
        if delta <= 0:
            # Time regression or same second: no rotation.
            # (Do NOT reset — never-raise, also protects against NTP jitter.)
            if delta < 0:
                self._last_second = now_second
            return
        if delta >= self._window:
            self._counts = deque([0] * self._window, maxlen=self._window)
        else:
            for _ in range(delta):
                self._counts.append(0)
        self._last_second = now_second

    def _aggregate_to_bars(self) -> list[int]:
        """Fold 60 per-second slots into NUM_BARS display buckets.

        Uses floor division; the leftover (60 % 8 = 4) spreads across the
        first 4 buckets so every slot is counted exactly once.
        """
        size = self._window // NUM_BARS       # 7
        leftover = self._window % NUM_BARS    # 4
        out: list[int] = []
        idx = 0
        for i in range(NUM_BARS):
            span = size + (1 if i < leftover else 0)
            out.append(sum(list(self._counts)[idx:idx + span]))
            idx += span
        return out
