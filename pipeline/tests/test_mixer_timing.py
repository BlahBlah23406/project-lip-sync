"""The timing invariants the mixer is built around.

These are the rules a broken dub violates: clips never start before their
caption, speed never exceeds the hard ceiling, and the layout never invents
time that the rendered audio does not contain.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mixer import (
    HARD_MAX_SPEED,
    LAG_HARD,
    LAG_SOFT,
    MAX_SPEED,
    available_time_for,
    place,
    speed_ceiling,
    trim_overlaps,
)


class TestSpeedCeiling:
    def test_on_time_speech_stays_at_the_normal_ceiling(self):
        assert speed_ceiling(0.0) == MAX_SPEED
        assert speed_ceiling(LAG_SOFT) == MAX_SPEED

    def test_far_behind_reaches_but_never_passes_the_hard_ceiling(self):
        assert speed_ceiling(LAG_HARD) == HARD_MAX_SPEED
        assert speed_ceiling(LAG_HARD * 10) == HARD_MAX_SPEED

    def test_ramps_monotonically_between_the_two(self):
        lags = [LAG_SOFT + i * (LAG_HARD - LAG_SOFT) / 10 for i in range(11)]
        speeds = [speed_ceiling(x) for x in lags]
        assert speeds == sorted(speeds)

    @pytest.mark.parametrize("lag", [-5.0, 0.0, 0.7, 1.5, 3.0, 100.0])
    def test_is_always_within_the_declared_bounds(self, lag):
        # The 4.0x incident came from two speed-ups compounding; nothing may
        # ever return more than HARD_MAX_SPEED.
        assert MAX_SPEED <= speed_ceiling(lag) <= HARD_MAX_SPEED


class TestPlace:
    def test_a_clip_never_starts_before_its_caption(self):
        # A negative offset is the one thing the layout makes impossible.
        assert place(cap_start=10.0, cursor=5.0) == 10.0

    def test_an_overrun_butts_up_against_the_previous_clip(self):
        assert place(cap_start=10.0, cursor=12.0) == 12.0

    def test_a_natural_pause_forgives_accumulated_lag(self):
        # Cursor behind the caption means we resync to the original timeline.
        assert place(cap_start=30.0, cursor=29.9) == 30.0

    @pytest.mark.parametrize("cap_start,cursor", [
        (0.0, 0.0), (5.0, 5.0), (1.5, 9.9), (100.0, 0.1),
    ])
    def test_never_returns_a_time_before_the_cursor(self, cap_start, cursor):
        assert place(cap_start, cursor) >= cursor


class TestTrimOverlaps:
    def test_clamps_a_segment_that_runs_into_the_next(self):
        segs = [
            {"start": 0.0, "duration": 10.0},
            {"start": 2.0, "duration": 1.0},
        ]
        assert trim_overlaps(segs)[0]["duration"] == pytest.approx(2.0)

    def test_leaves_non_overlapping_segments_alone(self):
        segs = [
            {"start": 0.0, "duration": 1.0},
            {"start": 5.0, "duration": 1.0},
        ]
        assert trim_overlaps(segs)[0]["duration"] == pytest.approx(1.0)

    def test_the_last_segment_is_never_trimmed(self):
        segs = [{"start": 0.0, "duration": 1.0}, {"start": 5.0, "duration": 99.0}]
        assert trim_overlaps(segs)[-1]["duration"] == pytest.approx(99.0)

    def test_does_not_mutate_its_input(self):
        segs = [{"start": 0.0, "duration": 10.0}, {"start": 2.0, "duration": 1.0}]
        before = [dict(s) for s in segs]
        trim_overlaps(segs)
        assert segs == before

    def test_never_produces_a_zero_or_negative_duration(self):
        # Two captions starting at the same instant must not yield a 0s clip.
        segs = [{"start": 4.0, "duration": 3.0}, {"start": 4.0, "duration": 1.0}]
        assert all(s["duration"] > 0 for s in trim_overlaps(segs))


class TestAvailableTimeFor:
    def test_a_segment_may_use_the_room_up_to_the_next_one(self):
        segs = [{"start": 0.0, "duration": 1.0}, {"start": 4.0, "duration": 1.0}]
        assert available_time_for(segs, 0) == pytest.approx(4.0)

    def test_the_last_segment_gets_its_own_duration(self):
        segs = [{"start": 0.0, "duration": 1.0}, {"start": 4.0, "duration": 2.5}]
        assert available_time_for(segs, 1) == pytest.approx(2.5)

    def test_never_returns_less_than_the_segments_own_duration(self):
        # Overlapping captions must not shrink the window below the clip itself.
        segs = [{"start": 0.0, "duration": 5.0}, {"start": 1.0, "duration": 1.0}]
        assert available_time_for(segs, 0) >= 5.0
