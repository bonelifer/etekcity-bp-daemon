#!/usr/bin/env python3
"""Create a tiny fixture SQLite database for smoke/CI testing.

The schema here is duplicated from storage.py's _SCHEMA rather than
imported, so this script has no dependency on the package being installed
(it's run standalone by scripts/smoke-test.sh, not through the package).
Keep the two in sync if the readings table's columns change.
"""

import sqlite3
import sys
from datetime import datetime, timezone


def main() -> None:
    db_path = sys.argv[1]
    con = sqlite3.connect(db_path)
    con.execute(
        """
        CREATE TABLE readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            address TEXT NOT NULL,
            user INTEGER NOT NULL,
            profile TEXT,
            systolic_mmhg INTEGER,
            diastolic_mmhg INTEGER,
            systolic_kpa REAL,
            diastolic_kpa REAL,
            pulse_bpm INTEGER,
            irregular_heartbeat INTEGER,
            motion_detected INTEGER,
            display_unit TEXT,
            error_code TEXT
        )
        """
    )
    con.execute(
        "INSERT INTO readings "
        "(recorded_at, address, user, profile, systolic_mmhg, diastolic_mmhg, "
        "systolic_kpa, diastolic_kpa, pulse_bpm, irregular_heartbeat, "
        "motion_detected, display_unit, error_code) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now(timezone.utc).isoformat(),
            "AA:BB:CC:DD:EE:FF",
            0,
            None,
            120,
            80,
            16.0,
            10.7,
            70,
            0,
            0,
            "MMHG",
            "OK",
        ),
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
