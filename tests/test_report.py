from dataclasses import replace
from datetime import datetime, timezone

from etekcity_bp_daemon.config import DEFAULT_PATIENT_CONFIG, DEFAULT_REPORT_CONFIG
from etekcity_bp_daemon.report import (
    _apply_profile_overrides,
    _build_chart,
    _build_compact_table,
    _build_rollup_buckets,
    _build_rollup_table,
    _build_table,
    _estimate_rate_per_day,
    _goal_progress_lines,
    _range_str,
    _resolve_range,
    _rollup_key,
    _rollup_label,
    _summary_paragraphs,
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


def test_include_profile_defaults_to_hidden(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", profile="Alice")
    store.close()
    rows = fetch_rows(db_path, None, None, None)

    csv_output = str(tmp_path / "report.csv")
    build_csv(rows, csv_output, DEFAULT_REPORT_CONFIG)
    assert "Who" not in open(csv_output).read()
    assert "Alice" not in open(csv_output).read()

    table = _build_table(rows, DEFAULT_REPORT_CONFIG)
    header = table._cellvalues[0]
    assert "Who" not in header
    assert "Alice" not in table._cellvalues[1]


def test_include_profile_yes_shows_who_column(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", profile="Alice")
    store.close()
    rows = fetch_rows(db_path, None, None, None)
    report_config = replace(DEFAULT_REPORT_CONFIG, include_profile=True)

    csv_output = str(tmp_path / "report.csv")
    build_csv(rows, csv_output, report_config)
    content = open(csv_output).read()
    assert "Who" in content
    assert "Alice" in content

    table = _build_table(rows, report_config)
    assert "Who" in table._cellvalues[0]
    assert "Alice" in table._cellvalues[1]


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
    patient = replace(DEFAULT_PATIENT_CONFIG, name="Alice")
    lines = _goal_progress_lines(rows, DEFAULT_REPORT_CONFIG, patient)
    assert (
        "No goal_systolic_mmhg/goal_diastolic_mmhg/goal_pulse_bpm set for this profile."
        in lines
    )


def test_goal_progress_lines_over_goal_and_trending_toward_it(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", systolic=150, diastolic=95)
    _record(store, "2026-01-06T00:00:00+00:00", systolic=140, diastolic=90)
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    patient = replace(
        DEFAULT_PATIENT_CONFIG, name="Alice", goal_systolic_mmhg=130, goal_diastolic_mmhg=80
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
    patient = replace(
        DEFAULT_PATIENT_CONFIG, name="Alice", goal_systolic_mmhg=130, goal_diastolic_mmhg=80
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
    patient = replace(
        DEFAULT_PATIENT_CONFIG,
        name="Alice Smith",
        email="alice@example.com",
        notes="On lisinopril 10mg",
        goal_systolic_mmhg=130,
        goal_diastolic_mmhg=80,
        goal_pulse_bpm=70,
    )
    output = str(tmp_path / "report.pdf")
    build_pdf(rows, output, report_config, patient)

    with open(output, "rb") as pdf_file:
        assert pdf_file.read(4) == b"%PDF"


def test_goal_progress_lines_includes_pulse_goal_without_unit_conversion(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    store.record(
        recorded_at="2026-01-01T00:00:00+00:00",
        address=_ADDRESS,
        user=0,
        profile=None,
        systolic_mmhg=120,
        diastolic_mmhg=80,
        systolic_kpa=16.0,
        diastolic_kpa=10.7,
        pulse_bpm=95,
        irregular_heartbeat=False,
        motion_detected=False,
        display_unit="MMHG",
        error_code="OK",
    )
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    kpa_report_config = replace(DEFAULT_REPORT_CONFIG, unit="kpa")
    patient = replace(DEFAULT_PATIENT_CONFIG, goal_pulse_bpm=70)
    lines = _goal_progress_lines(rows, kpa_report_config, patient)
    joined = " ".join(lines)
    assert "Pulse: current 95, goal 70 bpm (25 bpm over goal)" in joined


def test_apply_profile_overrides_only_applies_set_fields():
    patient = replace(DEFAULT_PATIENT_CONFIG, unit="kpa")
    result = _apply_profile_overrides(DEFAULT_REPORT_CONFIG, patient)
    assert result.unit == "kpa"
    assert result.date_format == DEFAULT_REPORT_CONFIG.date_format
    assert result.page_size == DEFAULT_REPORT_CONFIG.page_size


def test_apply_profile_overrides_noop_when_nothing_set():
    result = _apply_profile_overrides(DEFAULT_REPORT_CONFIG, DEFAULT_PATIENT_CONFIG)
    assert result == DEFAULT_REPORT_CONFIG


def test_build_compact_table_fills_column_major(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    for i in range(5):
        _record(store, f"2026-01-0{i + 1}T00:00:00+00:00", systolic=120 + i, diastolic=80 + i)
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    table = _build_compact_table(rows, DEFAULT_REPORT_CONFIG)
    body = table._cellvalues

    assert body[0] == ["Date/Time", "Sys (mmHg)", "Dia (mmHg)", "Pulse"] * 2
    # 5 rows / 2 groups = ceil(2.5) = 3 rows per column; group 1 gets the
    # first 3 readings top-to-bottom, group 2 gets the remaining 2 plus a
    # blank pad row.
    assert len(body) == 4
    assert body[1][1] == "120"  # first reading, first group
    assert body[1][5] == "123"  # fourth reading, second group
    assert body[3][4:] == ["", "", "", ""]  # padded blank row


def test_build_compact_table_fewer_rows_than_groups(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00")
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    table = _build_compact_table(rows, DEFAULT_REPORT_CONFIG)
    # Only one reading -- should collapse to a single column group, not pad
    # out a second empty one.
    assert len(table._cellvalues[0]) == 4


def test_rollup_key_and_label_week():
    dt = datetime(2026, 1, 8, tzinfo=timezone.utc)
    key = _rollup_key(dt, "week")
    assert key == dt.astimezone().isocalendar()[:2]
    label = _rollup_label(key, "week")
    assert "/" in label and "-" in label


def test_rollup_key_and_label_month():
    dt = datetime(2026, 3, 15, tzinfo=timezone.utc)
    key = _rollup_key(dt, "month")
    local = dt.astimezone()
    assert key == (local.year, local.month)
    assert _rollup_label(key, "month") == local.strftime("%B %Y")


def test_range_str():
    assert _range_str([]) == "-"
    assert _range_str([120.0]) == "120 (120-120)"
    assert _range_str([120.0, 130.0, 110.0]) == "120 (110-130)"


def test_build_rollup_table_buckets_by_week_and_flags_worst_category(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    # Same ISO week (Thu + Fri).
    _record(store, "2026-01-01T00:00:00+00:00", systolic=115, diastolic=75)
    _record(store, "2026-01-02T00:00:00+00:00", systolic=190, diastolic=125)
    # Following week.
    _record(store, "2026-01-08T00:00:00+00:00", systolic=120, diastolic=80)
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    table = _build_rollup_table(rows, DEFAULT_REPORT_CONFIG)
    body = table._cellvalues

    assert len(body) == 3  # header + 2 weekly buckets
    assert body[1][1] == 2  # first bucket has 2 readings
    assert body[1][-1] == "Hypertensive Crisis"  # worst of Normal/Crisis
    assert body[2][1] == 1


def test_build_rollup_table_monthly(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-05T00:00:00+00:00")
    _record(store, "2026-02-05T00:00:00+00:00")
    store.close()

    rows = fetch_rows(db_path, None, None, None)
    monthly_config = replace(DEFAULT_REPORT_CONFIG, rollup_period="month")
    table = _build_rollup_table(rows, monthly_config)
    assert len(table._cellvalues) == 3  # header + 2 monthly buckets


def test_build_pdf_layout_permutations(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    for i in range(3):
        _record(store, f"2026-01-0{i + 1}T00:00:00+00:00", systolic=120 + i, diastolic=80 + i)
    store.close()
    rows = fetch_rows(db_path, None, None, None)

    permutations = {
        "chart_only": replace(DEFAULT_REPORT_CONFIG, include_table=False),
        "table_only": replace(DEFAULT_REPORT_CONFIG, include_chart=False),
        "compact": replace(DEFAULT_REPORT_CONFIG, table_layout="compact"),
        "rollup": replace(DEFAULT_REPORT_CONFIG, table_layout="rollup"),
        "neither": replace(DEFAULT_REPORT_CONFIG, include_chart=False, include_table=False),
    }
    for name, config in permutations.items():
        output = str(tmp_path / f"{name}.pdf")
        build_pdf(rows, output, config)
        with open(output, "rb") as pdf_file:
            assert pdf_file.read(4) == b"%PDF"


def _two_person_rows(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-05T08:00:00+00:00", user=0, profile="Alice", systolic=145, diastolic=92)
    _record(store, "2026-01-06T08:00:00+00:00", user=1, profile="Bob", systolic=118, diastolic=76)
    store.close()
    return fetch_rows(db_path, None, None, None)


def test_rollup_buckets_split_same_period_by_person(tmp_path):
    rows = _two_person_rows(tmp_path)
    buckets = _build_rollup_buckets(rows, "week")
    # Same ISO week, but two distinct people -- must not be one blended bucket.
    assert len(buckets) == 2
    keys = list(buckets)
    assert keys[0][:2] == keys[1][:2]
    assert {key[2] for key in keys} == {"Alice", "Bob"}
    for bucket_rows in buckets.values():
        assert len(bucket_rows) == 1


def test_rollup_table_adds_who_column_when_multi_person(tmp_path):
    rows = _two_person_rows(tmp_path)
    table = _build_rollup_table(rows, DEFAULT_REPORT_CONFIG)
    header = table._cellvalues[0]
    assert "Who" in header
    who_col = header.index("Who")
    people = {row[who_col] for row in table._cellvalues[1:]}
    assert people == {"Alice", "Bob"}
    # Neither person's numbers should equal a blended average of both.
    systolic_col = header.index("Systolic\navg (min-max) mmHg")
    values = [row[systolic_col] for row in table._cellvalues[1:]]
    assert "145 (145-145)" in values
    assert "118 (118-118)" in values


def test_rollup_table_omits_who_column_for_single_person(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-05T08:00:00+00:00", profile="Alice")
    store.close()
    rows = fetch_rows(db_path, None, None, None)
    table = _build_rollup_table(rows, DEFAULT_REPORT_CONFIG)
    assert "Who" not in table._cellvalues[0]


def test_summary_paragraphs_split_by_person(tmp_path):
    rows = _two_person_rows(tmp_path)
    from reportlab.lib.styles import getSampleStyleSheet

    styles = getSampleStyleSheet()
    elements = _summary_paragraphs(rows, DEFAULT_REPORT_CONFIG, styles)
    text = " ".join(el.text for el in elements)
    assert "Alice" in text
    assert "Bob" in text
    assert "avg 145" in text
    assert "avg 118" in text
    # Never a blended combined average across both people.
    assert "avg 132" not in text and "avg 131" not in text


def test_summary_paragraphs_single_block_for_one_person(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-05T08:00:00+00:00", profile="Alice", systolic=145, diastolic=92)
    store.close()
    rows = fetch_rows(db_path, None, None, None)
    from reportlab.lib.styles import getSampleStyleSheet

    styles = getSampleStyleSheet()
    elements = _summary_paragraphs(rows, DEFAULT_REPORT_CONFIG, styles)
    text = " ".join(el.text for el in elements)
    assert "<b>Alice</b>" not in text
    assert "avg 145" in text


def test_chart_draws_one_line_pair_per_person(tmp_path):
    rows = _two_person_rows(tmp_path)
    drawing = _build_chart(rows, DEFAULT_REPORT_CONFIG)
    # One LinePlot with 4 series (Alice systolic/diastolic, Bob systolic/diastolic).
    chart = drawing.contents[0]
    assert len(chart.data) == 4


def test_chart_single_person_has_two_series(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-05T08:00:00+00:00", profile="Alice", systolic=145, diastolic=92)
    _record(store, "2026-01-06T08:00:00+00:00", profile="Alice", systolic=140, diastolic=90)
    store.close()
    rows = fetch_rows(db_path, None, None, None)
    drawing = _build_chart(rows, DEFAULT_REPORT_CONFIG)
    chart = drawing.contents[0]
    assert len(chart.data) == 2


def test_build_pdf_includes_multi_person_guidance_note(tmp_path):
    rows = _two_person_rows(tmp_path)
    output = str(tmp_path / "report.pdf")
    build_pdf(rows, output, DEFAULT_REPORT_CONFIG)
    with open(output, "rb") as pdf_file:
        assert pdf_file.read(4) == b"%PDF"


def test_build_pdf_no_guidance_note_for_single_person(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-05T08:00:00+00:00", profile="Alice")
    store.close()
    rows = fetch_rows(db_path, None, None, None)
    output = str(tmp_path / "report.pdf")
    build_pdf(rows, output, DEFAULT_REPORT_CONFIG)
    with open(output, "rb") as pdf_file:
        assert pdf_file.read(4) == b"%PDF"
