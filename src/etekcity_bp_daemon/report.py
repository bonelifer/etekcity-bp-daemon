"""Generate a PDF or CSV report of blood pressure readings from the SQLite database."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ._version import __version__
from .categories import CRISIS, ELEVATED, NORMAL, STAGE_1, STAGE_2, classify
from .config import (
    DEFAULT_REPORT_CONFIG,
    ConfigError,
    ReportConfig,
    load_config,
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
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f5d8a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]
    style_commands.extend(("ALIGN", (idx, 1), (idx, -1), "RIGHT") for idx in numeric_cols)
    for row_index, category in enumerate(categories, start=1):
        color = _CATEGORY_COLORS.get(category, colors.white)
        style_commands.append(("BACKGROUND", (0, row_index), (-1, row_index), color))
        if category == CRISIS:
            style_commands.append(("TEXTCOLOR", (0, row_index), (-1, row_index), colors.white))

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle(style_commands))
    return table


def _build_chart(rows: list[ReportRow], report_config: ReportConfig) -> Drawing:
    """Build a line chart of systolic and diastolic pressure over time.

    Args:
        rows: Reading rows to include, oldest first.
        report_config: Supplies the pressure unit and date/time format.

    Returns:
        A reportlab Drawing containing the chart, or just a "not enough
        data" note if fewer than two readings have pressure values.
    """
    points = [
        (row.recorded_at, *_pressure_values(row, report_config.unit)[:2])
        for row in rows
        if row.systolic_mmhg is not None and row.diastolic_mmhg is not None
    ]
    _, _, unit_label = _pressure_values(rows[0], report_config.unit) if rows else (None, None, "")

    drawing = Drawing(480, 260)
    if len(points) < 2:
        drawing.add(String(10, 130, "Not enough pressure data to plot a chart."))
        return drawing

    systolic_values = [point[1] for point in points]
    diastolic_values = [point[2] for point in points]
    date_pattern = "%m/%d" if report_config.date_format == "us" else "%d/%m"
    date_labels = [point[0].astimezone().strftime(date_pattern) for point in points]

    step = max(1, len(date_labels) // _CHART_MAX_LABELS)
    thinned_labels = [label if i % step == 0 else "" for i, label in enumerate(date_labels)]

    chart = HorizontalLineChart()
    chart.x = 50
    chart.y = 40
    chart.width = 400
    chart.height = 180
    chart.data = [systolic_values, diastolic_values]
    chart.categoryAxis.categoryNames = thinned_labels
    chart.categoryAxis.labels.angle = 30
    chart.categoryAxis.labels.dx = -8
    chart.categoryAxis.labels.dy = -10
    chart.categoryAxis.labels.fontSize = 7
    all_values = systolic_values + diastolic_values
    chart.valueAxis.valueMin = min(all_values) - 5
    chart.valueAxis.valueMax = max(all_values) + 5
    chart.lines[0].strokeColor = colors.HexColor("#cc0000")
    chart.lines[0].strokeWidth = 1.5
    chart.lines[1].strokeColor = colors.HexColor("#2f5d8a")
    chart.lines[1].strokeWidth = 1.5

    drawing.add(chart)
    drawing.add(
        String(
            chart.x,
            chart.y + chart.height + 25,
            f"Systolic (red) / Diastolic (blue), {unit_label}, over time",
            fontName="Helvetica-Bold",
            fontSize=10,
        )
    )
    return drawing


def _summary_paragraphs(rows: list[ReportRow], report_config: ReportConfig, styles) -> list:
    """Build min/max/average summary lines for systolic, diastolic, and pulse.

    Args:
        rows: Reading rows to include, oldest first.
        report_config: Supplies the pressure unit.
        styles: A reportlab stylesheet, as returned by getSampleStyleSheet().

    Returns:
        Paragraph elements, empty if no row has pressure data.
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

    return [Paragraph(line, styles["Normal"]) for line in lines]


def build_pdf(rows: list[ReportRow], output_path: str, report_config: ReportConfig) -> None:
    """Render reading rows as a chart, summary, and table in a PDF file.

    Args:
        rows: Reading rows to include, oldest first.
        output_path: Filesystem path to write the PDF to.
        report_config: Controls which columns are shown, the pressure unit,
            the date/time format, the page size, and whether a summary is
            printed.
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
    if report_config.include_summary:
        elements.extend(_summary_paragraphs(rows, report_config, styles))
    elements.append(Spacer(1, 0.2 * inch))

    elements.append(_build_chart(rows, report_config))
    elements.append(Spacer(1, 0.2 * inch))
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
        "-P", "--profile", help="Restrict to readings tagged with this profile name"
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

    start, end = _resolve_range(args.period, args.from_date, args.to_date)
    output = args.output or f"bp-report.{args.format}"

    rows = fetch_rows(db_path, args.address, start, end, args.profile)
    if not rows:
        print("No readings found for the given range/filters.")
        return 1

    if args.format == "csv":
        build_csv(rows, output, report_config)
    else:
        build_pdf(rows, output, report_config)
    print(f"Wrote {len(rows)} reading(s) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
