from dataclasses import replace
from datetime import datetime, timezone

from etekcity_bp_daemon.config import DEFAULT_REPORT_CONFIG, PatientConfig
from etekcity_bp_daemon.report import (
    _estimate_rate_per_day,
    _goal_progress_lines,
    _resolve_range,
    build_csv,
    build_pdf,
    fetch_rows,
)
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


def test_estimate_rate_per_day_falling(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", systolic=150, diastolic=95)
    _record(store, "2026-01-06T00:00:00+00:00", systolic=140, diastolic=90)
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    rate = _estimate_rate_per_day(rows, lambda r: r.systolic_mmhg)
    assert rate == -2.0  # -10 mmHg over 5 days


def test_estimate_rate_per_day_single_point_is_zero(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00")
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    assert _estimate_rate_per_day(rows, lambda r: r.systolic_mmhg) == 0.0


def test_goal_progress_lines_no_goal_set(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00")
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    patient = PatientConfig(
        name="Alice", email="", unit="", goal_systolic_mmhg=None, goal_diastolic_mmhg=None
    )
    lines = _goal_progress_lines(rows, DEFAULT_REPORT_CONFIG, patient)
    assert "No goal_systolic_mmhg/goal_diastolic_mmhg set for this profile." in lines


def test_goal_progress_lines_over_goal_and_trending_toward_it(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", systolic=150, diastolic=95)
    _record(store, "2026-01-06T00:00:00+00:00", systolic=140, diastolic=90)
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    patient = PatientConfig(
        name="Alice", email="", unit="", goal_systolic_mmhg=130, goal_diastolic_mmhg=80
    )
    lines = _goal_progress_lines(rows, DEFAULT_REPORT_CONFIG, patient)
    joined = " ".join(lines)
    assert "Systolic: current 140, goal 130 mmHg (10 mmHg over goal)" in joined
    assert "Trending toward goal" in joined
    assert "Diastolic: current 90, goal 80 mmHg (10 mmHg over goal)" in joined


def test_goal_progress_lines_respects_kpa_display_unit(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", systolic=150, diastolic=95)
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    patient = PatientConfig(
        name="Alice", email="", unit="", goal_systolic_mmhg=130, goal_diastolic_mmhg=80
    )
    kpa_report_config = replace(DEFAULT_REPORT_CONFIG, unit="kpa")
    lines = _goal_progress_lines(rows, kpa_report_config, patient)
    joined = " ".join(lines)
    assert "mmHg" not in joined
    # 150 mmHg -> 20 kPa, 130 mmHg goal -> 17 kPa (both rounded via :.0f)
    assert "Systolic: current 20, goal 17 kPa" in joined


def test_build_pdf_with_patient_config_and_goal_progress(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", systolic=150, diastolic=95)
    _record(store, "2026-01-06T00:00:00+00:00", systolic=135, diastolic=85)
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    report_config = replace(DEFAULT_REPORT_CONFIG, include_goal_progress=True)
    patient = PatientConfig(
        name="Alice Smith",
        email="alice@example.com",
        unit="",
        goal_systolic_mmhg=130,
        goal_diastolic_mmhg=80,
    )
    output = str(tmp_path / "report.pdf")
    build_pdf(rows, output, report_config, patient)

    with open(output, "rb") as pdf_file:
        assert pdf_file.read(4) == b"%PDF"
