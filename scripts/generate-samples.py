#!/usr/bin/env python3
"""Regenerate samples/*.pdf from a fixed, two-profile fixture dataset.

Run this (with the package installed, e.g. `pip install -e .` from a
checkout) after any change to report.py's rendering, so the checked-in
samples don't go stale relative to what the code actually produces:

    ./scripts/generate-samples.py

See samples/README.md for what each file demonstrates.
"""

from __future__ import annotations

import configparser
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from etekcity_bp_daemon.report import main as report_main
from etekcity_bp_daemon.storage import ReadingStore

_ADDRESS = "AA:BB:CC:DD:EE:FF"
_MMHG_TO_KPA = 0.13332

# (recorded_at, user, profile, systolic, diastolic, pulse, irregular, motion)
# Alice's numbers trend downward across six weeks toward her configured
# goal (130/80, pulse 70), to demonstrate the "trending toward goal" line.
# Bob's include one hypertensive-crisis-range reading, to demonstrate
# category shading and the rollup layout's "worst category" column.
_READINGS = [
    ("2026-01-05T08:00:00+00:00", 0, "Alice", 145, 92, 78, False, False),
    ("2026-01-07T08:00:00+00:00", 0, "Alice", 142, 90, 76, False, False),
    ("2026-01-12T08:00:00+00:00", 0, "Alice", 138, 88, 74, True, False),
    ("2026-01-14T08:00:00+00:00", 0, "Alice", 135, 85, 75, False, False),
    ("2026-01-19T08:00:00+00:00", 0, "Alice", 132, 84, 73, False, False),
    ("2026-01-21T08:00:00+00:00", 0, "Alice", 128, 82, 72, False, False),
    ("2026-01-26T08:00:00+00:00", 0, "Alice", 125, 80, 70, False, False),
    ("2026-01-28T08:00:00+00:00", 0, "Alice", 122, 78, 71, False, True),
    ("2026-02-02T08:00:00+00:00", 0, "Alice", 120, 78, 69, False, False),
    ("2026-02-04T08:00:00+00:00", 0, "Alice", 118, 76, 68, False, False),
    ("2026-02-09T08:00:00+00:00", 0, "Alice", 116, 75, 68, False, False),
    ("2026-02-11T08:00:00+00:00", 0, "Alice", 115, 74, 67, False, False),
    ("2026-01-06T08:00:00+00:00", 1, "Bob", 118, 76, 65, False, False),
    ("2026-01-13T08:00:00+00:00", 1, "Bob", 190, 122, 95, False, False),
    ("2026-01-20T08:00:00+00:00", 1, "Bob", 122, 80, 68, False, False),
    ("2026-01-27T08:00:00+00:00", 1, "Bob", 119, 77, 66, False, False),
    ("2026-02-03T08:00:00+00:00", 1, "Bob", 121, 79, 67, True, False),
    ("2026-02-10T08:00:00+00:00", 1, "Bob", 117, 75, 65, False, False),
]

_BASE_REPORT = {
    "include_address": "yes",
    "include_profile": "yes",
    "include_summary": "yes",
    "include_categories": "yes",
    "include_goal_progress": "no",
    "include_chart": "yes",
    "include_table": "yes",
    "table_layout": "full",
    "rollup_period": "week",
    "unit": "mmhg",
    "date_format": "world",
    "page_size": "letter",
}

# Each sample: (output filename, [report] overrides, extra CLI args).
_SAMPLES: list[tuple[str, dict[str, str], list[str]]] = [
    # Main grid: table_layout x unit x date_format, for the two per-reading
    # table layouts.
    ("full-mmhg-world.pdf", {"table_layout": "full", "unit": "mmhg", "date_format": "world"}, []),
    ("full-mmhg-us.pdf", {"table_layout": "full", "unit": "mmhg", "date_format": "us"}, []),
    ("full-kpa-world.pdf", {"table_layout": "full", "unit": "kpa", "date_format": "world"}, []),
    ("full-kpa-us.pdf", {"table_layout": "full", "unit": "kpa", "date_format": "us"}, []),
    (
        "compact-mmhg-world.pdf",
        {"table_layout": "compact", "unit": "mmhg", "date_format": "world"},
        [],
    ),
    (
        "compact-mmhg-us.pdf",
        {"table_layout": "compact", "unit": "mmhg", "date_format": "us"},
        [],
    ),
    (
        "compact-kpa-world.pdf",
        {"table_layout": "compact", "unit": "kpa", "date_format": "world"},
        [],
    ),
    ("compact-kpa-us.pdf", {"table_layout": "compact", "unit": "kpa", "date_format": "us"}, []),
    # Rollup layout: period matters more than unit/date-format here.
    ("rollup-week-mmhg.pdf", {"table_layout": "rollup", "rollup_period": "week"}, []),
    ("rollup-month-mmhg.pdf", {"table_layout": "rollup", "rollup_period": "month"}, []),
    # Toggle demos.
    (
        "full-minimal.pdf",
        {"include_address": "no", "include_profile": "no", "include_categories": "no",
         "include_summary": "no"},
        [],
    ),
    ("chart-only.pdf", {"include_table": "no"}, []),
    ("table-only.pdf", {"include_chart": "no"}, []),
    # Personalization: Alice's own report, with her goal progress section.
    (
        "full-with-goal-progress.pdf",
        {"include_goal_progress": "yes"},
        ["--profile", "Alice"],
    ),
]


def _build_fixture_db(db_path: Path) -> None:
    store = ReadingStore(str(db_path))
    for recorded_at, user, profile, systolic, diastolic, pulse, irregular, motion in _READINGS:
        store.record(
            recorded_at=recorded_at,
            address=_ADDRESS,
            user=user,
            profile=profile,
            systolic_mmhg=systolic,
            diastolic_mmhg=diastolic,
            systolic_kpa=round(systolic * _MMHG_TO_KPA, 1),
            diastolic_kpa=round(diastolic * _MMHG_TO_KPA, 1),
            pulse_bpm=pulse,
            irregular_heartbeat=irregular,
            motion_detected=motion,
            display_unit="MMHG",
            error_code="OK",
        )
    store.close()


def _write_config(path: Path, db_path: Path, report_overrides: dict[str, str]) -> None:
    parser = configparser.ConfigParser()
    parser["monitor"] = {"address": _ADDRESS}
    parser["storage"] = {"db_path": str(db_path)}
    parser["daemon"] = {"log_level": "INFO"}
    parser["report"] = {**_BASE_REPORT, **report_overrides}
    parser["profile.Alice"] = {
        "name": "Alice Smith",
        "email": "alice@example.com",
        "notes": "On lisinopril 10mg",
        "goal_systolic_mmhg": "130",
        "goal_diastolic_mmhg": "80",
        "goal_pulse_bpm": "70",
    }
    with open(path, "w") as config_file:
        parser.write(config_file)


def main() -> int:
    repo_dir = Path(__file__).resolve().parent.parent
    samples_dir = repo_dir / "samples"
    samples_dir.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as workdir:
        workdir_path = Path(workdir)
        db_path = workdir_path / "readings.db"
        _build_fixture_db(db_path)

        for filename, report_overrides, extra_args in _SAMPLES:
            config_path = workdir_path / f"{filename}.ini"
            _write_config(config_path, db_path, report_overrides)
            output_path = samples_dir / filename
            argv = ["--config", str(config_path), "--output", str(output_path), *extra_args]
            print(f"==> {filename}")
            with redirect_stdout(io.StringIO()) as captured:
                exit_code = report_main(argv)
            if exit_code != 0:
                print(captured.getvalue(), file=sys.stderr)
                print(f"Failed to generate {filename}", file=sys.stderr)
                return 1

    print(f"Wrote {len(_SAMPLES)} sample(s) to {samples_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
