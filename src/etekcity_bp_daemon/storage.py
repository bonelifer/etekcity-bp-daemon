"""SQLite storage backend for blood pressure readings."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
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
);
"""


def ensure_schema(db_path: str) -> None:
    """Create the readings table if it doesn't already exist.

    Safe to call from any entry point (daemon, API server, etc.) regardless
    of whether the database file already exists or which one touches it
    first.

    Args:
        db_path: Filesystem path to the SQLite database file. Parent
            directories are created automatically if missing.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(_SCHEMA)
        connection.commit()
    finally:
        connection.close()


def get_distinct_profiles(db_path: str) -> set[str]:
    """Return the distinct non-null profile tags actually used in the database.

    Args:
        db_path: Filesystem path to the SQLite database file.

    Returns:
        The set of distinct profile names, empty if none are tagged yet or
        the ``readings`` table doesn't exist.
    """
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT DISTINCT profile FROM readings WHERE profile IS NOT NULL"
        ).fetchall()
        return {row[0] for row in rows}
    except sqlite3.OperationalError:
        return set()
    finally:
        connection.close()


def get_reading_recorded_at(db_path: str, row_id: int) -> str | None:
    """Look up a reading's recorded_at timestamp, without modifying it.

    Args:
        db_path: Filesystem path to the SQLite database file.
        row_id: The reading's primary key, as returned by ``record()``.

    Returns:
        The stored ISO-8601 ``recorded_at`` string, or None if no row
        matches ``row_id``.
    """
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT recorded_at FROM readings WHERE id = ?", (row_id,)
        ).fetchone()
        return row[0] if row is not None else None
    finally:
        connection.close()


def set_reading_profile(db_path: str, row_id: int, profile: str) -> bool:
    """Tag a previously recorded reading with a profile name.

    Args:
        db_path: Filesystem path to the SQLite database file.
        row_id: The reading's primary key, as returned by ``record()``.
        profile: The profile name to assign.

    Returns:
        True if a row was updated, False if no row matched ``row_id``.
    """
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(
            "UPDATE readings SET profile = ? WHERE id = ?", (profile, row_id)
        )
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


class ReadingStore:
    """Persists blood pressure readings to a local SQLite database.

    Args:
        db_path: Filesystem path to the SQLite database file. Parent
            directories are created automatically if missing.
    """

    def __init__(self, db_path: str) -> None:
        ensure_schema(db_path)
        self._connection = sqlite3.connect(db_path)

    def record(
        self,
        recorded_at: str,
        address: str,
        user: int,
        profile: str | None,
        systolic_mmhg: int | None,
        diastolic_mmhg: int | None,
        systolic_kpa: float | None,
        diastolic_kpa: float | None,
        pulse_bpm: int | None,
        irregular_heartbeat: bool,
        motion_detected: bool,
        display_unit: str | None,
        error_code: str | None,
    ) -> int:
        """Insert one reading row.

        Args:
            recorded_at: ISO-8601 UTC timestamp of the reading.
            address: BLE address of the device that produced it.
            user: Device user slot (0 = User 1, 1 = User 2).
            profile: Profile name, if already known at insert time.
                Normally None -- profiles are tagged after the fact via
                ``set_reading_profile()`` once ntfy/dunstify gets an answer,
                since the device's user slot alone can't identify who took
                the reading.
            systolic_mmhg: Systolic pressure in mmHg, if reported.
            diastolic_mmhg: Diastolic pressure in mmHg, if reported.
            systolic_kpa: Systolic pressure in kPa, if reported.
            diastolic_kpa: Diastolic pressure in kPa, if reported.
            pulse_bpm: Pulse rate in beats per minute, if reported.
            irregular_heartbeat: Whether an irregular heartbeat was detected.
            motion_detected: Whether arm motion was detected.
            display_unit: Name of the device's current display unit.
            error_code: Last error code reported by the device ("OK" if none).

        Returns:
            The inserted row's primary key.
        """
        cursor = self._connection.execute(
            """
            INSERT INTO readings (
                recorded_at, address, user, profile, systolic_mmhg,
                diastolic_mmhg, systolic_kpa, diastolic_kpa, pulse_bpm,
                irregular_heartbeat, motion_detected, display_unit, error_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recorded_at,
                address,
                user,
                profile,
                systolic_mmhg,
                diastolic_mmhg,
                systolic_kpa,
                diastolic_kpa,
                pulse_bpm,
                int(irregular_heartbeat),
                int(motion_detected),
                display_unit,
                error_code,
            ),
        )
        self._connection.commit()
        return cursor.lastrowid

    def close(self) -> None:
        """Close the underlying database connection."""
        self._connection.close()
