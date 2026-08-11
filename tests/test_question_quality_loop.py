from __future__ import annotations

import pytest

from scripts.run_question_quality_loop import _cycle_directory_name, _stamp, resolve_interval_minutes


def test_finite_quality_cycles_run_back_to_back_by_default() -> None:
    assert resolve_interval_minutes(1, None) == 0.0
    assert resolve_interval_minutes(2, None) == 0.0


def test_unbounded_quality_loop_remains_daily_by_default() -> None:
    assert resolve_interval_minutes(0, None) == 1440.0


def test_explicit_quality_loop_interval_is_preserved() -> None:
    assert resolve_interval_minutes(2, 5.5) == 5.5
    assert resolve_interval_minutes(0, 0) == 0.0


def test_negative_quality_loop_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="zero or greater"):
        resolve_interval_minutes(2, -1)


def test_quality_cycle_stamp_has_subsecond_collision_resistance() -> None:
    stamp = _stamp()
    date_part, time_part, microseconds = stamp.split("_")

    assert len(date_part) == 8
    assert len(time_part) == 6
    assert len(microseconds) == 6
    assert microseconds.isdigit()


def test_quality_cycle_directory_names_remain_unique_in_rapid_loop() -> None:
    names = [_cycle_directory_name() for _ in range(100)]

    assert len(set(names)) == 100
    assert all(name.startswith("cycle-") for name in names)
