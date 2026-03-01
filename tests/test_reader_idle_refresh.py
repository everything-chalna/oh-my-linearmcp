from __future__ import annotations

import time

from linear_mcp_fast.reader import IDLE_REFRESH_THRESHOLD_SECONDS, LinearLocalReader


def _make_reader() -> LinearLocalReader:
    return LinearLocalReader(db_path="/nonexistent", blob_path="/nonexistent")


def test_first_call_does_not_force_refresh():
    """First ensure_fresh() call (last==0) should not set _force_next_refresh."""
    reader = _make_reader()
    assert reader._last_tool_call_at == 0.0

    reader.ensure_fresh()

    assert reader._force_next_refresh is False
    assert reader._last_tool_call_at > 0.0


def test_short_gap_does_not_force_refresh():
    """A gap shorter than threshold should not trigger refresh."""
    reader = _make_reader()
    reader._last_tool_call_at = time.time() - 5  # 5 seconds ago

    reader.ensure_fresh()

    assert reader._force_next_refresh is False


def test_long_gap_forces_refresh():
    """A gap exceeding threshold should set _force_next_refresh."""
    reader = _make_reader()
    reader._last_tool_call_at = time.time() - (IDLE_REFRESH_THRESHOLD_SECONDS + 10)

    reader.ensure_fresh()

    assert reader._force_next_refresh is True


def test_exact_threshold_forces_refresh():
    """Gap exactly equal to threshold should trigger refresh (>= semantics)."""
    reader = _make_reader()
    reader._last_tool_call_at = time.time() - IDLE_REFRESH_THRESHOLD_SECONDS

    reader.ensure_fresh()

    assert reader._force_next_refresh is True


def test_timestamp_updated_on_each_call():
    """_last_tool_call_at is updated to current time on every call."""
    reader = _make_reader()

    before = time.time()
    reader.ensure_fresh()
    after = time.time()

    assert before <= reader._last_tool_call_at <= after

    before2 = time.time()
    reader.ensure_fresh()
    after2 = time.time()

    assert before2 <= reader._last_tool_call_at <= after2


def test_consecutive_calls_no_double_refresh():
    """Two quick consecutive calls should not both trigger refresh."""
    reader = _make_reader()
    reader._last_tool_call_at = time.time() - (IDLE_REFRESH_THRESHOLD_SECONDS + 10)

    reader.ensure_fresh()
    assert reader._force_next_refresh is True

    # Reset flag as _ensure_cache would
    reader._force_next_refresh = False

    # Second immediate call should not re-trigger
    reader.ensure_fresh()
    assert reader._force_next_refresh is False


def test_default_threshold_is_60():
    """IDLE_REFRESH_THRESHOLD_SECONDS defaults to 60."""
    assert IDLE_REFRESH_THRESHOLD_SECONDS == 60


def test_get_health_includes_idle_fields():
    """get_health() should include lastToolCallAt and idleRefreshThresholdSeconds."""
    reader = _make_reader()

    health = reader.get_health()

    assert "lastToolCallAt" in health
    assert "idleRefreshThresholdSeconds" in health
    assert health["lastToolCallAt"] == 0.0
    assert health["idleRefreshThresholdSeconds"] == IDLE_REFRESH_THRESHOLD_SECONDS


def test_get_health_reflects_updated_timestamp():
    """After ensure_fresh(), get_health() shows updated lastToolCallAt."""
    reader = _make_reader()
    reader.ensure_fresh()

    health = reader.get_health()

    assert health["lastToolCallAt"] > 0.0


def test_just_under_threshold_no_refresh():
    """A gap 1 second less than threshold should not trigger refresh."""
    reader = _make_reader()
    reader._last_tool_call_at = time.time() - (IDLE_REFRESH_THRESHOLD_SECONDS - 1)

    reader.ensure_fresh()

    assert reader._force_next_refresh is False
