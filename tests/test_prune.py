from datetime import datetime, timedelta, timezone

from etekcity_bp_daemon.prune import count_old_rows, delete_old_rows
from etekcity_bp_daemon.storage import ReadingStore

_ADDRESS = "AA:BB:CC:DD:EE:FF"


def _record(store, recorded_at):
    store.record(
        recorded_at=recorded_at,
        address=_ADDRESS,
        user=0,
        profile=None,
        systolic_mmhg=120,
        diastolic_mmhg=80,
        systolic_kpa=16.0,
        diastolic_kpa=10.7,
        pulse_bpm=70,
        irregular_heartbeat=False,
        motion_detected=False,
        display_unit="MMHG",
        error_code="OK",
    )


def test_count_and_delete_old_rows(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    now = datetime.now(timezone.utc)
    _record(store, (now - timedelta(days=400)).isoformat())
    _record(store, (now - timedelta(days=1)).isoformat())
    store.close()

    cutoff = now - timedelta(days=365)
    assert count_old_rows(db_path, cutoff, None) == 1

    deleted = delete_old_rows(db_path, cutoff, None)
    assert deleted == 1
    assert count_old_rows(db_path, cutoff, None) == 0
