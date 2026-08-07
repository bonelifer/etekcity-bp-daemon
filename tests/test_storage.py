from etekcity_bp_daemon.storage import ReadingStore, get_distinct_profiles


def test_record_and_read_back(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    try:
        row_id = store.record(
            recorded_at="2026-01-01T00:00:00+00:00",
            address="AA:BB:CC:DD:EE:FF",
            user=0,
            profile="Alice",
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
        assert row_id == 1
    finally:
        store.close()


def test_get_distinct_profiles(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    try:
        store.record(
            recorded_at="2026-01-01T00:00:00+00:00",
            address="AA:BB:CC:DD:EE:FF",
            user=0,
            profile="Alice",
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
        store.record(
            recorded_at="2026-01-01T00:05:00+00:00",
            address="AA:BB:CC:DD:EE:FF",
            user=1,
            profile=None,
            systolic_mmhg=118,
            diastolic_mmhg=76,
            systolic_kpa=15.7,
            diastolic_kpa=10.1,
            pulse_bpm=68,
            irregular_heartbeat=False,
            motion_detected=False,
            display_unit="MMHG",
            error_code="OK",
        )
    finally:
        store.close()

    assert get_distinct_profiles(db_path) == {"Alice"}


def test_get_distinct_profiles_no_table(tmp_path):
    db_path = str(tmp_path / "empty.db")
    assert get_distinct_profiles(db_path) == set()
