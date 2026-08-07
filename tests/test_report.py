from datetime import datetime, timezone

from etekcity_bp_daemon.config import DEFAULT_REPORT_CONFIG
from etekcity_bp_daemon.report import _resolve_range, build_csv, fetch_rows
from etekcity_bp_daemon.storage import ReadingStore

_ADDRESS = "AA:BB:CC:DD:EE:FF"


def _record(store, recorded_at, user=0, profile=None, systolic=120, diastolic=80):
    store.record(
        recorded_at=recorded_at,
        address=_ADDRESS,
        user=user,
        profile=profile,
        systolic_mmhg=systolic,
        diastolic_mmhg=diastolic,
        systolic_kpa=systolic * 0.13332,
        diastolic_kpa=diastolic * 0.13332,
        pulse_bpm=70,
        irregular_heartbeat=False,
        motion_detected=False,
        display_unit="MMHG",
        error_code="OK",
    )


def test_fetch_rows_ordered_oldest_first(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-02T00:00:00+00:00")
    _record(store, "2026-01-01T00:00:00+00:00")
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    assert [row.recorded_at.day for row in rows] == [1, 2]


def test_fetch_rows_filters_by_profile(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", user=0, profile="Alice")
    _record(store, "2026-01-01T00:05:00+00:00", user=1, profile="Bob")
    store.close()

    rows = fetch_rows(db_path, None, None, None, profile="Alice")
    assert len(rows) == 1
    assert rows[0].profile == "Alice"


def test_resolve_range_period():
    start, end = _resolve_range("7d", None, None)
    assert (end - start).days == 7


def test_resolve_range_all_is_unbounded():
    assert _resolve_range("all", None, None) == (None, None)


def test_resolve_range_explicit_dates():
    start, end = _resolve_range("all", "2026-01-01", "2026-01-05")
    assert start == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 1, 6, tzinfo=timezone.utc)


def test_build_csv_writes_header_and_rows(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", systolic=190, diastolic=125)
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    output = str(tmp_path / "report.csv")
    build_csv(rows, output, DEFAULT_REPORT_CONFIG)

    content = open(output).read()
    assert "Category" in content
    assert "Hypertensive Crisis" in content
