"""Generate a PDF or CSV report of blood pressure readings from the SQLite database."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape

from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ._version import __version__
from .categories import CRISIS, ELEVATED, NORMAL, STAGE_1, STAGE_2, classify
from .config import (
    DEFAULT_PATIENT_CONFIG,
    DEFAULT_REPORT_CONFIG,
    ConfigError,
    PatientConfig,
    ReportConfig,
    load_config,
    load_profile_details,
    load_report_config,
)
from .storage import ensure_schema

_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}

_PAGE_SIZES = {"letter": letter, "a4": A4}

# Date/time strftime patterns for each date_format preset.
_DATE_TIME_FORMATS = {
    "us": "%m/%d/%Y %I:%M:%S %p",
    "world": "%d/%m/%Y %H:%M:%S",
}

# Maximum number of x-axis date labels to show on the chart before thinning
# them out, so labels don't overlap when there are many readings.
_CHART_MAX_LABELS = 10

# AHA category -> background color for table rows / chart legend.
_CATEGORY_COLORS = {
    NORMAL: colors.HexColor("#d9ead3"),
    ELEVATED: colors.HexColor("#fff2cc"),
    STAGE_1: colors.HexColor("#fce5cd"),
    STAGE_2: colors.HexColor("#f4cccc"),
    CRISIS: colors.HexColor("#cc0000"),
}

# AHA category -> severity rank, higher is worse. Used to pick the "worst"
# category within a rollup period.
_CATEGORY_SEVERITY = {
    NORMAL: 0,
    ELEVATED: 1,
    STAGE_1: 2,
    STAGE_2: 3,
    CRISIS: 4,
}


def _format_datetime(recorded_at: datetime, date_format: str) -> str:
    """Format a UTC timestamp in local time using the given date_format preset.

    Args:
        recorded_at: A timezone-aware UTC datetime.
        date_format: "us" (MM/DD/YYYY, 12-hour) or "world" (DD/MM/YYYY, 24-hour).

    Returns:
        The formatted local date/time string.
    """
    return recorded_at.astimezone().strftime(_DATE_TIME_FORMATS[date_format])


@dataclass
class ReportRow:
    """One reading row as read back from the database."""

    recorded_at: datetime
    address: str
    user: int
    profile: str | None
    systolic_mmhg: int | None
    diastolic_mmhg: int | None
    systolic_kpa: float | None
    diastolic_kpa: float | None
    pulse_bpm: int | None
    irregular_heartbeat: bool
    motion_detected: bool
    error_code: str | None


def _resolve_range(
    period: str, from_date: str | None, to_date: str | None
) -> tuple[datetime | None, datetime | None]:
    """Resolve the requested period/from/to options into a UTC datetime range.

    Args:
        period: One of "7d", "30d", "90d", "1y", "all".
        from_date: Explicit start date (YYYY-MM-DD), overrides ``period``.
        to_date: Explicit end date (YYYY-MM-DD), inclusive. Defaults to now
            if omitted while ``from_date`` is set.

    Returns:
        A ``(start, end)`` tuple of timezone-aware UTC datetimes. Both are
        None when the range is unbounded ("all" with no explicit dates).
    """
    if from_date:
        start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = (
            datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            + timedelta(days=1)
            if to_date
            else datetime.now(timezone.utc)
        )
        return start, end

    if period == "all":
        return None, None

    days = _PERIOD_DAYS[period]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start, end


def fetch_rows(
    db_path: str,
    address: str | None,
    start: datetime | None,
    end: datetime | None,
    profile: str | None = None,
) -> list[ReportRow]:
    """Query readings from the database within an optional address/date range.

    Args:
        db_path: Path to the SQLite database file.
        address: Restrict to a single device's BLE address, if given.
        start: Inclusive UTC start of the date range, or None for no lower bound.
        end: Exclusive UTC end of the date range, or None for no upper bound.
        profile: Restrict to readings tagged with this profile name, if given.

    Returns:
        Matching rows ordered oldest first.
    """
    query = (
        "SELECT recorded_at, address, user, profile, systolic_mmhg, "
        "diastolic_mmhg, systolic_kpa, diastolic_kpa, pulse_bpm, "
        "irregular_heartbeat, motion_detected, error_code FROM readings"
    )
    clauses: list[str] = []
    params: list[str] = []

    if address:
        clauses.append("address = ?")
        params.append(address)
    if profile:
        clauses.append("profile = ?")
        params.append(profile)
    if start is not None:
        clauses.append("recorded_at >= ?")
        params.append(start.isoformat())
    if end is not None:
        clauses.append("recorded_at < ?")
        params.append(end.isoformat())

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY recorded_at ASC"

    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(query, params)
        return [
            ReportRow(
                recorded_at=datetime.fromisoformat(row[0]),
                address=row[1],
                user=row[2],
                profile=row[3],
                systolic_mmhg=row[4],
                diastolic_mmhg=row[5],
                systolic_kpa=row[6],
                diastolic_kpa=row[7],
                pulse_bpm=row[8],
                irregular_heartbeat=bool(row[9]),
                motion_detected=bool(row[10]),
                error_code=row[11],
            )
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def _pressure_values(row: ReportRow, unit: str) -> tuple[float | None, float | None, str]:
    """Return (systolic, diastolic, unit_label) in the report's configured unit.

    Categorization always uses the mmHg values (the AHA thresholds are
    defined in mmHg), regardless of which unit is displayed.
    """
    if unit == "kpa":
        return row.systolic_kpa, row.diastolic_kpa, "kPa"
    return row.systolic_mmhg, row.diastolic_mmhg, "mmHg"


def _who(row: ReportRow) -> str:
    """Return the profile name if set, else "User N"."""
    return row.profile or f"User {row.user + 1}"


def _apply_profile_overrides(
    report_config: ReportConfig, patient_config: PatientConfig
) -> ReportConfig:
    """Apply a profile's unit/date_format/page_size overrides onto report_config.

    Each override is independent and only applied if the profile actually
    set it, so e.g. one household member can override just the unit while
    still using the shared date_format and page_size.

    Args:
        report_config: The base (shared) report configuration.
        patient_config: Supplies the profile's overrides, if any.

    Returns:
        A copy of ``report_config`` with the profile's overrides applied,
        or ``report_config`` unchanged if the profile set none of them.
    """
    overrides = {}
    if patient_config.unit:
        overrides["unit"] = patient_config.unit
    if patient_config.date_format:
        overrides["date_format"] = patient_config.date_format
    if patient_config.page_size:
        overrides["page_size"] = patient_config.page_size
    return replace(report_config, **overrides) if overrides else report_config


def build_csv(rows: list[ReportRow], output_path: str, report_config: ReportConfig) -> None:
    """Write reading rows to a CSV file.

    Args:
        rows: Reading rows to include, oldest first.
        output_path: Filesystem path to write the CSV to.
        report_config: Controls which columns are shown, the pressure unit,
            and the date/time format.
    """
    _, _, unit_label = _pressure_values(rows[0], report_config.unit) if rows else (
        None,
        None,
        "mmHg",
    )

    header = ["Date/Time (local)"]
    if report_config.include_address:
        header.append("Address")
    if report_config.include_profile:
        header.append("Who")
    header.extend([f"Systolic ({unit_label})", f"Diastolic ({unit_label})", "Pulse (bpm)"])
    if report_config.include_categories:
        header.append("Category")
    header.extend(["Irregular Heartbeat", "Motion Detected", "Error"])

    with open(output_path, "w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)
        for row in rows:
            systolic, diastolic, _ = _pressure_values(row, report_config.unit)
            values: list[object] = [_format_datetime(row.recorded_at, report_config.date_format)]
            if report_config.include_address:
                values.append(row.address)
            if report_config.include_profile:
                values.append(_who(row))
            values.extend(
                [
                    systolic if systolic is not None else "",
                    diastolic if diastolic is not None else "",
                    row.pulse_bpm if row.pulse_bpm is not None else "",
                ]
            )
            if report_config.include_categories:
                category = classify(row.systolic_mmhg, row.diastolic_mmhg)
                values.append(category or "")
            values.extend(
                [
                    "yes" if row.irregular_heartbeat else "no",
                    "yes" if row.motion_detected else "no",
                    row.error_code or "",
                ]
            )
            writer.writerow(values)


def _header_style_commands() -> list[tuple]:
    """Return the header/grid/font style commands shared by every table layout."""
    return [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f5d8a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]


def _build_table(rows: list[ReportRow], report_config: ReportConfig) -> Table:
    """Build the PDF reading table, with rows shaded by AHA category.

    Args:
        rows: Reading rows to include, oldest first.
        report_config: Controls which columns are shown, the pressure unit,
            and the date/time format.

    Returns:
        A styled reportlab Table.
    """
    _, _, unit_label = _pressure_values(rows[0], report_config.unit)

    header = ["Date/Time (local)"]
    if report_config.include_address:
        header.append("Address")
    if report_config.include_profile:
        header.append("Who")
    header.extend([f"Systolic\n({unit_label})", f"Diastolic\n({unit_label})", "Pulse\n(bpm)"])
    if report_config.include_categories:
        header.append("Category")
    header.extend(["Irregular\nHB", "Motion"])

    data = [header]
    categories: list[str | None] = []
    for row in rows:
        systolic, diastolic, _ = _pressure_values(row, report_config.unit)
        category = classify(row.systolic_mmhg, row.diastolic_mmhg)
        categories.append(category)

        values: list[object] = [_format_datetime(row.recorded_at, report_config.date_format)]
        if report_config.include_address:
            values.append(row.address)
        if report_config.include_profile:
            values.append(_who(row))
        values.extend(
            [
                f"{systolic:.0f}" if systolic is not None else "-",
                f"{diastolic:.0f}" if diastolic is not None else "-",
                row.pulse_bpm if row.pulse_bpm is not None else "-",
            ]
        )
        if report_config.include_categories:
            values.append(category or "-")
        values.extend(
            ["yes" if row.irregular_heartbeat else "-", "yes" if row.motion_detected else "-"]
        )
        data.append(values)

    numeric_cols = [len(header) - 4, len(header) - 3, len(header) - 2]
    style_commands = _header_style_commands()
    style_commands.extend(("ALIGN", (idx, 1), (idx, -1), "RIGHT") for idx in numeric_cols)
    for row_index, category in enumerate(categories, start=1):
        color = _CATEGORY_COLORS.get(category, colors.white)
        style_commands.append(("BACKGROUND", (0, row_index), (-1, row_index), color))
        if category == CRISIS:
            style_commands.append(("TEXTCOLOR", (0, row_index), (-1, row_index), colors.white))

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle(style_commands))
    return table


_COMPACT_LAYOUT_COLUMN_GROUPS = 2


def _build_compact_table(rows: list[ReportRow], report_config: ReportConfig) -> Table:
    """Build the compact layout: Date/Systolic/Diastolic/Pulse only, side by side.

    Readings fill one column group top-to-bottom before moving to the next
    group, so a full page of readings doesn't leave most of the page width
    empty the way a single narrow table would. Two groups (not three, like
    the scale daemon's weight-only "simple" layout) since each BP reading
    needs more columns to mean anything on its own.

    Args:
        rows: Reading rows to include, oldest first.
        report_config: Controls the pressure unit and date/time format.

    Returns:
        A styled reportlab Table.
    """
    _, _, unit_label = _pressure_values(rows[0], report_config.unit)
    groups = min(_COMPACT_LAYOUT_COLUMN_GROUPS, len(rows))
    rows_per_column = -(-len(rows) // groups)  # ceil division

    group_header = ["Date/Time", f"Sys ({unit_label})", f"Dia ({unit_label})", "Pulse"]
    header = group_header * groups
    data = [header]
    for r in range(rows_per_column):
        line: list[object] = []
        for g in range(groups):
            idx = g * rows_per_column + r
            if idx < len(rows):
                row = rows[idx]
                systolic, diastolic, _ = _pressure_values(row, report_config.unit)
                line.append(_format_datetime(row.recorded_at, report_config.date_format))
                line.append(f"{systolic:.0f}" if systolic is not None else "-")
                line.append(f"{diastolic:.0f}" if diastolic is not None else "-")
                line.append(row.pulse_bpm if row.pulse_bpm is not None else "-")
            else:
                line.extend(["", "", "", ""])
        data.append(line)

    align_cols = [i for i in range(len(header)) if i % 4 in (1, 2, 3)]
    style_commands = _header_style_commands()
    style_commands.extend(("ALIGN", (idx, 1), (idx, -1), "RIGHT") for idx in align_cols)

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle(style_commands))
    return table


def _rollup_key(recorded_at: datetime, period: str) -> tuple[int, int]:
    """Return the (year, period-number) bucket a reading's local time falls in."""
    local = recorded_at.astimezone()
    if period == "month":
        return (local.year, local.month)
    iso_year, iso_week, _ = local.isocalendar()
    return (iso_year, iso_week)


def _rollup_label(key: tuple[int, int], period: str) -> str:
    """Render a rollup bucket key as a human-readable period label."""
    if period == "month":
        year, month = key
        return date(year, month, 1).strftime("%B %Y")
    iso_year, iso_week = key
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    sunday = monday + timedelta(days=6)
    return f"{monday.strftime('%m/%d')}-{sunday.strftime('%m/%d')}/{iso_year}"


def _range_str(values: list[float]) -> str:
    """Format a list of values as "avg (min-max)", or "-" if empty."""
    if not values:
        return "-"
    return f"{sum(values) / len(values):.0f} ({min(values):.0f}-{max(values):.0f})"


def _build_rollup_buckets(
    rows: list[ReportRow], period: str
) -> dict[tuple[int, int, str], list[ReportRow]]:
    """Group reading rows into per-person weekly or monthly buckets.

    Bucketed by (period, person) rather than just period -- averaging two
    different people's readings into one "week" row would be medically
    meaningless, the same reasoning as the chart's per-person lines.
    ``_who`` covers both tagged profiles and the untagged "User N" fallback,
    so a device shared via hardware slots alone still gets split correctly.

    Args:
        rows: Reading rows to include, oldest first.
        period: "week" (ISO calendar week) or "month" (calendar month).

    Returns:
        (year, period-number, person) -> rows in that bucket, sorted
        period-major then person-minor (not insertion order, since one
        person's readings can interleave with another's across weeks).
    """
    buckets: dict[tuple[int, int, str], list[ReportRow]] = {}
    for row in rows:
        key = (*_rollup_key(row.recorded_at, period), _who(row))
        buckets.setdefault(key, []).append(row)
    return dict(sorted(buckets.items(), key=lambda item: item[0]))


def _build_rollup_table(rows: list[ReportRow], report_config: ReportConfig) -> Table:
    """Build the rollup layout: one row per week/month per person instead of per reading.

    Each row shows the reading count, avg/min/max systolic and diastolic,
    average pulse, and (if enabled) the worst AHA category seen in that
    period -- a year of daily readings becomes ~52 rows instead of 365. A
    "Who" column is included whenever the report spans more than one
    person, so same-period rows for different people aren't indistinguishable.

    Args:
        rows: Reading rows to include, oldest first.
        report_config: Controls the pressure unit, date/time format, rollup
            period, and whether the category column/shading is included.

    Returns:
        A styled reportlab Table.
    """
    _, _, unit_label = _pressure_values(rows[0], report_config.unit)
    buckets = _build_rollup_buckets(rows, report_config.rollup_period)
    multi_person = len({_who(row) for row in rows}) > 1

    header = ["Period"]
    if multi_person:
        header.append("Who")
    header.extend(
        [
            "Readings",
            f"Systolic\navg (min-max) {unit_label}",
            f"Diastolic\navg (min-max) {unit_label}",
            "Pulse avg\n(bpm)",
        ]
    )
    if report_config.include_categories:
        header.append("Worst\nCategory")

    data = [header]
    worst_categories: list[str | None] = []
    for key, bucket_rows in buckets.items():
        period_key, who = key[:2], key[2]
        pairs = [_pressure_values(r, report_config.unit)[:2] for r in bucket_rows]
        systolic_values = [s for s, _ in pairs if s is not None]
        diastolic_values = [d for _, d in pairs if d is not None]
        pulse_values = [r.pulse_bpm for r in bucket_rows if r.pulse_bpm is not None]

        worst_category = None
        worst_rank = -1
        for row in bucket_rows:
            category = classify(row.systolic_mmhg, row.diastolic_mmhg)
            rank = _CATEGORY_SEVERITY.get(category, -1)
            if rank > worst_rank:
                worst_rank = rank
                worst_category = category
        worst_categories.append(worst_category)

        values: list[object] = [_rollup_label(period_key, report_config.rollup_period)]
        if multi_person:
            values.append(who)
        values.extend(
            [
                len(bucket_rows),
                _range_str(systolic_values),
                _range_str(diastolic_values),
                f"{sum(pulse_values) / len(pulse_values):.0f}" if pulse_values else "-",
            ]
        )
        if report_config.include_categories:
            values.append(worst_category or "-")
        data.append(values)

    numeric_start = 2 if multi_person else 1
    numeric_cols = list(range(numeric_start, numeric_start + 4))
    style_commands = _header_style_commands()
    style_commands.extend(("ALIGN", (idx, 1), (idx, -1), "RIGHT") for idx in numeric_cols)
    if report_config.include_categories:
        for row_index, category in enumerate(worst_categories, start=1):
            color = _CATEGORY_COLORS.get(category, colors.white)
            style_commands.append(("BACKGROUND", (0, row_index), (-1, row_index), color))
            if category == CRISIS:
                style_commands.append(
                    ("TEXTCOLOR", (0, row_index), (-1, row_index), colors.white)
                )

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle(style_commands))
    return table


# (systolic color, diastolic color) per person, cycled if there are more
# people than colors. The first pair matches the long-standing single-person
# red/diastolic-blue convention.
_CHART_COLOR_PAIRS = [
    (colors.HexColor("#cc0000"), colors.HexColor("#2f5d8a")),
    (colors.HexColor("#e69138"), colors.HexColor("#45818e")),
    (colors.HexColor("#a64d79"), colors.HexColor("#6aa84f")),
    (colors.HexColor("#674ea7"), colors.HexColor("#bf9000")),
]

_LEGEND_ROW_HEIGHT = 14
_LEGEND_SWATCH_WIDTH = 14


def _add_chart_legend(
    drawing: Drawing, entries: list[tuple[str, tuple]], x: float, top_y: float
) -> None:
    """Draw one legend row per (person, (systolic_color, diastolic_color)) entry."""
    for i, (person, (systolic_color, diastolic_color)) in enumerate(entries):
        row_y = top_y - i * _LEGEND_ROW_HEIGHT
        drawing.add(String(x, row_y, person, fontName="Helvetica-Bold", fontSize=8))
        systolic_x = x + 65
        drawing.add(
            Line(
                systolic_x,
                row_y + 3,
                systolic_x + _LEGEND_SWATCH_WIDTH,
                row_y + 3,
                strokeColor=systolic_color,
                strokeWidth=3,
            )
        )
        drawing.add(String(systolic_x + _LEGEND_SWATCH_WIDTH + 4, row_y, "Systolic", fontSize=8))
        diastolic_x = systolic_x + _LEGEND_SWATCH_WIDTH + 58
        drawing.add(
            Line(
                diastolic_x,
                row_y + 3,
                diastolic_x + _LEGEND_SWATCH_WIDTH,
                row_y + 3,
                strokeColor=diastolic_color,
                strokeWidth=3,
            )
        )
        drawing.add(String(diastolic_x + _LEGEND_SWATCH_WIDTH + 4, row_y, "Diastolic", fontSize=8))


def _build_chart(rows: list[ReportRow], report_config: ReportConfig) -> Drawing:
    """Build a line chart of systolic and diastolic pressure over time.

    One systolic/diastolic line pair per distinct person (see ``_who``),
    each in its own color -- averaging or interleaving different people's
    readings onto one line would be medically meaningless. All series
    share a common numeric x-axis (days since the earliest reading in the
    report) rather than a shared category-per-reading axis, so gaps in one
    person's readings don't distort another's, and actual elapsed time
    between readings is reflected instead of treating every reading as
    equally spaced.

    Args:
        rows: Reading rows to include, oldest first.
        report_config: Supplies the pressure unit and date/time format.

    Returns:
        A reportlab Drawing containing the chart, or just a "not enough
        data" note if fewer than two readings have pressure values.
    """
    valued_rows = [
        row for row in rows if row.systolic_mmhg is not None and row.diastolic_mmhg is not None
    ]
    _, _, unit_label = _pressure_values(rows[0], report_config.unit) if rows else (None, None, "")

    if len(valued_rows) < 2:
        drawing = Drawing(480, 260)
        drawing.add(String(10, 130, "Not enough pressure data to plot a chart."))
        return drawing

    by_person: dict[str, list[ReportRow]] = {}
    for row in valued_rows:
        by_person.setdefault(_who(row), []).append(row)
    people = sorted(by_person)

    reference_date = valued_rows[0].recorded_at

    def day_offset(row: ReportRow) -> float:
        return (row.recorded_at - reference_date).total_seconds() / 86400

    series_data = []
    legend_entries = []
    for i, person in enumerate(people):
        systolic_color, diastolic_color = _CHART_COLOR_PAIRS[i % len(_CHART_COLOR_PAIRS)]
        systolic_points = []
        diastolic_points = []
        for row in by_person[person]:
            systolic, diastolic, _ = _pressure_values(row, report_config.unit)
            x = day_offset(row)
            systolic_points.append((x, systolic))
            diastolic_points.append((x, diastolic))
        series_data.append(systolic_points)
        series_data.append(diastolic_points)
        legend_entries.append((person, (systolic_color, diastolic_color)))

    multi_person = len(people) > 1
    legend_height = (len(people) * _LEGEND_ROW_HEIGHT + 10) if multi_person else 0
    drawing = Drawing(480, 260 + legend_height)

    chart = LinePlot()
    chart.x = 50
    chart.y = 40 + legend_height
    chart.width = 400
    chart.height = 180
    chart.data = series_data

    all_values = [value for series in series_data for _, value in series]
    chart.yValueAxis.valueMin = min(all_values) - 5
    chart.yValueAxis.valueMax = max(all_values) + 5

    all_days = [x for series in series_data for x, _ in series]
    chart.xValueAxis.valueMin = min(all_days)
    chart.xValueAxis.valueMax = max(all_days)
    span_days = max(all_days) - min(all_days)
    if span_days > 0:
        chart.xValueAxis.valueStep = max(1, span_days // _CHART_MAX_LABELS + 1)
    date_pattern = "%m/%d" if report_config.date_format == "us" else "%d/%m"
    chart.xValueAxis.labelTextFormat = lambda value: (
        (reference_date + timedelta(days=value)).astimezone().strftime(date_pattern)
    )

    for i, (_, (systolic_color, diastolic_color)) in enumerate(legend_entries):
        chart.lines[i * 2].strokeColor = systolic_color
        chart.lines[i * 2].strokeWidth = 1.5
        chart.lines[i * 2 + 1].strokeColor = diastolic_color
        chart.lines[i * 2 + 1].strokeWidth = 1.5

    drawing.add(chart)
    caption = "Multiple people" if multi_person else "Systolic (red) / Diastolic (blue)"
    drawing.add(
        String(
            chart.x,
            chart.y + chart.height + 25,
            f"{caption}, {unit_label}, over time",
            fontName="Helvetica-Bold",
            fontSize=10,
        )
    )
    if multi_person:
        _add_chart_legend(drawing, legend_entries, chart.x, legend_height - 5)
    return drawing


def _summary_lines(rows: list[ReportRow], report_config: ReportConfig) -> list[str]:
    """Build min/max/average text lines for systolic, diastolic, and category breakdown.

    Args:
        rows: Reading rows to include, oldest first. Should already be
            restricted to one person -- averaging different people's
            readings together would be medically meaningless.
        report_config: Supplies the pressure unit and whether the category
            breakdown is included.

    Returns:
        Text lines, empty if no row has pressure data.
    """
    pairs = [_pressure_values(row, report_config.unit)[:2] for row in rows]
    systolic_values = [s for s, _ in pairs if s is not None]
    diastolic_values = [d for _, d in pairs if d is not None]
    if not systolic_values or not diastolic_values:
        return []

    _, _, unit_label = _pressure_values(rows[0], report_config.unit)
    lines = [
        f"Systolic: avg {sum(systolic_values) / len(systolic_values):.0f}, "
        f"min {min(systolic_values):.0f}, max {max(systolic_values):.0f} {unit_label}",
        f"Diastolic: avg {sum(diastolic_values) / len(diastolic_values):.0f}, "
        f"min {min(diastolic_values):.0f}, max {max(diastolic_values):.0f} {unit_label}",
    ]

    if report_config.include_categories:
        counts: dict[str, int] = {}
        for row in rows:
            category = classify(row.systolic_mmhg, row.diastolic_mmhg)
            if category:
                counts[category] = counts.get(category, 0) + 1
        if counts:
            breakdown = ", ".join(f"{name}: {count}" for name, count in counts.items())
            lines.append(f"Category breakdown: {breakdown}")

    return lines


def _summary_paragraphs(rows: list[ReportRow], report_config: ReportConfig, styles) -> list:
    """Build the summary section: one avg/min/max block per person if more than one.

    Blending different people's systolic/diastolic averages together would
    be medically meaningless (see the chart and rollup layout, which apply
    the same per-person split), so this prints one labeled block per
    distinct person -- see ``_who`` -- when the report spans more than one,
    and the original unlabeled single block otherwise.

    Args:
        rows: Reading rows to include, oldest first.
        report_config: Supplies the pressure unit and whether the category
            breakdown is included.
        styles: A reportlab stylesheet, as returned by getSampleStyleSheet().

    Returns:
        Paragraph elements, empty if no row has pressure data.
    """
    people = sorted({_who(row) for row in rows})
    if len(people) <= 1:
        return [Paragraph(line, styles["Normal"]) for line in _summary_lines(rows, report_config)]

    elements = []
    for person in people:
        person_rows = [row for row in rows if _who(row) == person]
        lines = _summary_lines(person_rows, report_config)
        if not lines:
            continue
        elements.append(Paragraph(f"<b>{escape(person)}</b>", styles["Normal"]))
        elements.extend(Paragraph(line, styles["Normal"]) for line in lines)
    return elements


def _estimate_rate_per_day(rows: list[ReportRow], value_of) -> float:
    """Estimate the rate of change per day via least-squares linear regression.

    Fits a line through every row's (days since the first row, value(row))
    rather than just the first and last point, so one outlier can't single-
    handedly swing the whole estimate the way a bare 2-point slope would.
    With exactly two points this reduces to that same 2-point slope, since
    a line through two points *is* their least-squares fit.

    Args:
        rows: Rows to fit, at least one entry, already filtered to those
            where ``value_of`` returns a non-None value.
        value_of: Extracts the numeric value to trend from a ReportRow.

    Returns:
        Units per day, positive for rising, negative for falling. 0.0 if
        fewer than two distinct timestamps are present (a rate can't be
        estimated from a single point in time).
    """
    if len(rows) < 2:
        return 0.0

    t0 = rows[0].recorded_at
    xs = [(row.recorded_at - t0).total_seconds() / 86400 for row in rows]
    ys = [value_of(row) for row in rows]

    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = sum((x - mean_x) ** 2 for x in xs)

    return numerator / denominator if denominator > 0 else 0.0


# mmHg -> kPa conversion factor for goal display. Matches
# etekcity_bp_ble.const.MMHG_TO_KPA; goals are always entered in mmHg (the
# clinical standard doctors use) and converted here for display only.
_MMHG_TO_KPA = 0.13332


def _convert_mmhg(value_mmhg: float, unit: str) -> float:
    """Convert a mmHg value to the report's display unit."""
    return value_mmhg * _MMHG_TO_KPA if unit == "kpa" else value_mmhg


def _goal_metric_lines(
    label: str, rows: list[ReportRow], goal_native: float, unit_label: str, convert, value_of
) -> list[str]:
    """Build current/goal/remaining/trend lines for one metric.

    Args:
        label: "Systolic", "Diastolic", or "Pulse", for the rendered text.
        rows: Reading rows to include, oldest first.
        goal_native: The profile's goal for this metric, in its native unit
            (mmHg for pressure, bpm for pulse) regardless of the report's
            display unit.
        unit_label: The unit label to render (e.g. "mmHg", "kPa", "bpm").
        convert: Converts a native-unit value to the display unit (identity
            for pulse, which has no unit to convert).
        value_of: Extracts this metric's native-unit value from a ReportRow.

    Returns:
        Text lines, empty if no row has a value for this metric.
    """
    valued = [row for row in rows if value_of(row) is not None]
    if not valued:
        return []

    current_native = value_of(valued[-1])
    remaining_native = current_native - goal_native

    if remaining_native > 0:
        status = f"{convert(remaining_native):.0f} {unit_label} over goal"
    else:
        status = "at or under goal"

    current_display = convert(current_native)
    goal_display = convert(goal_native)
    lines = [
        f"{label}: current {current_display:.0f}, goal {goal_display:.0f} "
        f"{unit_label} ({status})"
    ]

    if len(valued) >= 2:
        # The goal is a ceiling (e.g. "keep it under 130/80"), so a falling
        # rate is favorable and a rising rate is unfavorable regardless of
        # whether the current reading happens to be over or under it yet.
        # Computed in the native unit -- a positive conversion factor can't
        # flip its sign.
        rate = _estimate_rate_per_day(valued, value_of)
        if rate < 0:
            lines.append("  Trending toward goal (decreasing) at the current rate of change.")
        elif rate > 0:
            lines.append("  Trending away from goal (increasing) at the current rate of change.")

    return lines


def _goal_progress_lines(
    rows: list[ReportRow], report_config: ReportConfig, patient_config: PatientConfig
) -> list[str]:
    """Build the text lines for the "Goal Progress" report section.

    Args:
        rows: Reading rows to include, oldest first.
        report_config: Supplies the display unit to render current/goal in.
        patient_config: Supplies the profile's goal_systolic_mmhg /
            goal_diastolic_mmhg / goal_pulse_bpm, any of which may be unset.

    Returns:
        Text lines. A note if no goal is set or no readings have data for
        any goal metric, otherwise one summary per configured goal.
    """
    if (
        patient_config.goal_systolic_mmhg is None
        and patient_config.goal_diastolic_mmhg is None
        and patient_config.goal_pulse_bpm is None
    ):
        return [
            "No goal_systolic_mmhg/goal_diastolic_mmhg/goal_pulse_bpm set for this profile."
        ]

    unit = report_config.unit
    pressure_unit_label = "kPa" if unit == "kpa" else "mmHg"

    def convert_pressure(value_mmhg: float) -> float:
        return _convert_mmhg(value_mmhg, unit)

    lines: list[str] = []
    if patient_config.goal_systolic_mmhg is not None:
        lines.extend(
            _goal_metric_lines(
                "Systolic",
                rows,
                patient_config.goal_systolic_mmhg,
                pressure_unit_label,
                convert_pressure,
                lambda r: r.systolic_mmhg,
            )
        )
    if patient_config.goal_diastolic_mmhg is not None:
        lines.extend(
            _goal_metric_lines(
                "Diastolic",
                rows,
                patient_config.goal_diastolic_mmhg,
                pressure_unit_label,
                convert_pressure,
                lambda r: r.diastolic_mmhg,
            )
        )
    if patient_config.goal_pulse_bpm is not None:
        lines.extend(
            _goal_metric_lines(
                "Pulse",
                rows,
                patient_config.goal_pulse_bpm,
                "bpm",
                lambda value: value,
                lambda r: r.pulse_bpm,
            )
        )

    return lines or ["No readings with data for the configured goal(s) in this report's range."]


def _build_goal_progress_elements(
    rows: list[ReportRow], report_config: ReportConfig, patient_config: PatientConfig, styles
) -> list:
    """Build a "Goal Progress" heading and summary for this profile's BP goal.

    Args:
        rows: Reading rows to include, oldest first.
        report_config: Supplies the display unit to render current/goal in.
        patient_config: Supplies the profile's goal, if set.
        styles: The document's reportlab stylesheet.

    Returns:
        Flowables to append: a heading plus one line per metric goal.
    """
    lines = _goal_progress_lines(rows, report_config, patient_config)
    return [
        Paragraph("Goal Progress", styles["Heading2"]),
        Spacer(1, 0.05 * inch),
        *(Paragraph(line, styles["Normal"]) for line in lines),
        Spacer(1, 0.15 * inch),
    ]


def build_pdf(
    rows: list[ReportRow],
    output_path: str,
    report_config: ReportConfig = DEFAULT_REPORT_CONFIG,
    patient_config: PatientConfig = DEFAULT_PATIENT_CONFIG,
) -> None:
    """Render reading rows as a chart, summary, and table in a PDF file.

    Args:
        rows: Reading rows to include, oldest first.
        output_path: Filesystem path to write the PDF to.
        report_config: Controls which columns are shown, the pressure unit,
            the date/time format, the page size, whether a summary is
            printed, whether goal progress is included, whether the chart
            and/or table are included at all, and (if the table is
            included) which layout it renders as (full/compact/rollup).
        patient_config: Optional patient name/email/notes to print below
            the title (fields left blank are omitted), and the goal(s)
            used by ``report_config.include_goal_progress``.
    """
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output_path, pagesize=_PAGE_SIZES[report_config.page_size])
    elements = [
        Paragraph("Blood Pressure Report", styles["Title"]),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            f" &middot; {len(rows)} reading(s)",
            styles["Normal"],
        ),
    ]
    people = sorted({_who(row) for row in rows})
    if len(people) > 1:
        elements.append(
            Paragraph(
                f"This report includes readings from {len(people)} people "
                f"({', '.join(escape(person) for person in people)}). The chart, "
                "summary, and rollup table below are all split per person -- pass "
                "--profile &lt;name&gt; (or ?profile= via the API) to see just one "
                "person's report instead.",
                styles["Italic"],
            )
        )
    if patient_config.name:
        elements.append(Paragraph(f"Patient: {escape(patient_config.name)}", styles["Normal"]))
    if patient_config.email:
        elements.append(Paragraph(f"Email: {escape(patient_config.email)}", styles["Normal"]))
    if patient_config.notes:
        elements.append(Paragraph(f"Notes: {escape(patient_config.notes)}", styles["Normal"]))
    if report_config.include_summary:
        elements.extend(_summary_paragraphs(rows, report_config, styles))
    elements.append(Spacer(1, 0.2 * inch))

    if report_config.include_goal_progress:
        elements.extend(_build_goal_progress_elements(rows, report_config, patient_config, styles))

    if report_config.include_chart:
        elements.append(_build_chart(rows, report_config))
        elements.append(Spacer(1, 0.2 * inch))

    if report_config.include_table:
        if report_config.table_layout == "compact":
            elements.append(_build_compact_table(rows, report_config))
        elif report_config.table_layout == "rollup":
            elements.append(_build_rollup_table(rows, report_config))
        else:
            elements.append(_build_table(rows, report_config))

    doc.build(elements)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="etekcity-bp-report",
        description="Generate a PDF or CSV report from the daemon's reading database.",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "-c", "--config", help="Path to the daemon's INI config file (reads db_path from it)"
    )
    source.add_argument(
        "-d", "--db", help="Path to the SQLite database file, bypassing the config file"
    )
    parser.add_argument(
        "-F", "--format", choices=["pdf", "csv"], default="pdf",
        help="Output format (default: %(default)s)",
    )
    parser.add_argument(
        "-o", "--output", help="Output file path (default: bp-report.<format>)"
    )
    parser.add_argument(
        "-p", "--period", choices=["7d", "30d", "90d", "1y", "all"], default="all",
        help="Preset date range (default: %(default)s)",
    )
    parser.add_argument(
        "-f", "--from", dest="from_date", metavar="YYYY-MM-DD",
        help="Explicit start date, overrides --period",
    )
    parser.add_argument(
        "-t", "--to", dest="to_date", metavar="YYYY-MM-DD",
        help="Explicit end date (inclusive), defaults to now",
    )
    parser.add_argument(
        "-a", "--address", help="Restrict the report to one device's BLE address"
    )
    parser.add_argument(
        "-P",
        "--profile",
        help=(
            "Restrict to readings tagged with this profile name (requires "
            "--config); also personalizes the report (name/email/notes, "
            "unit/date-format/page-size overrides, goals) from that "
            "profile's [profile.<name>] section"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)

    if args.profile and not args.config:
        print("Error: --profile requires --config (profile details live in the config file)")
        return 1

    db_path = args.db
    report_config = DEFAULT_REPORT_CONFIG
    if args.config:
        try:
            db_path = load_config(args.config).db_path
            report_config = load_report_config(args.config)
        except ConfigError as exc:
            print(f"Error: {exc}")
            return 1

    ensure_schema(db_path)

    patient_config = DEFAULT_PATIENT_CONFIG
    if args.profile:
        try:
            patient_config = load_profile_details(args.config, args.profile)
        except ConfigError as exc:
            print(f"Error: {exc}")
            return 1

    start, end = _resolve_range(args.period, args.from_date, args.to_date)
    output = args.output or f"bp-report.{args.format}"

    rows = fetch_rows(db_path, args.address, start, end, args.profile)
    if not rows:
        print("No readings found for the given range/filters.")
        return 1

    # A profile's own unit/date_format/page_size (if set) override the
    # shared report config for its reports, so e.g. one household member
    # can see mmHg while another sees kPa.
    effective_report_config = _apply_profile_overrides(report_config, patient_config)

    if args.format == "csv":
        build_csv(rows, output, effective_report_config)
    else:
        build_pdf(rows, output, effective_report_config, patient_config)
    print(f"Wrote {len(rows)} reading(s) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
