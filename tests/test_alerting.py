from dataclasses import replace
from datetime import datetime, timezone

from etekcity_bp_daemon.alerting import check_alerts
from etekcity_bp_daemon.config import DEFAULT_ALERT_CONFIG
from etekcity_bp_daemon.storage import ReadingStore

_ADDRESS = "AA:BB:CC:DD:EE:FF"


def _record(store, recorded_at, user=0, systolic=120, diastolic=80, irregular=False):
    store.record(
        recorded_at=recorded_at,
        address=_ADDRESS,
        user=user,
        profile=None,
        systolic_mmhg=systolic,
        diastolic_mmhg=diastolic,
        systolic_kpa=systolic * 0.13332,
        diastolic_kpa=diastolic * 0.13332,
        pulse_bpm=70,
        irregular_heartbeat=irregular,
        motion_detected=False,
        display_unit="MMHG",
        error_code="OK",
    )


def test_no_alerts_when_disabled_checks(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00")
    store.close()

    config = replace(DEFAULT_ALERT_CONFIG, state_path=str(tmp_path / "state.json"))
    messages = check_alerts(db_path, config)
    assert messages == []


def test_staleness_alert(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00")
    store.close()

    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://localhost"],
        stale_after_days=2,
        state_path=str(tmp_path / "state.json"),
    )
    now = datetime(2026, 1, 5, tzinfo=timezone.utc)
    messages = check_alerts(db_path, config, now=now)
    assert len(messages) == 1
    assert "No reading" in messages[0]


def test_staleness_alert_throttled_on_repeat_check(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00")
    store.close()

    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://localhost"],
        stale_after_days=2,
        state_path=str(tmp_path / "state.json"),
    )
    now = datetime(2026, 1, 5, tzinfo=timezone.utc)
    first = check_alerts(db_path, config, now=now)
    second = check_alerts(db_path, config, now=now)
    assert len(first) == 1
    assert len(second) == 0


def test_crisis_range_alert_fires_once(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", systolic=190, diastolic=125)
    store.close()

    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://localhost"],
        crisis_systolic_mmhg=180,
        crisis_diastolic_mmhg=120,
        state_path=str(tmp_path / "state.json"),
    )
    now = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
    first = check_alerts(db_path, config, now=now)
    second = check_alerts(db_path, config, now=now)
    assert len(first) == 1
    assert "crisis" in first[0].lower()
    assert len(second) == 0


def test_irregular_heartbeat_alert(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", irregular=True)
    store.close()

    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://localhost"],
        alert_on_irregular_heartbeat=True,
        state_path=str(tmp_path / "state.json"),
    )
    now = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
    messages = check_alerts(db_path, config, now=now)
    assert len(messages) == 1
    assert "Irregular heartbeat" in messages[0]


def test_separate_user_slots_tracked_independently(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", user=0, systolic=190, diastolic=125)
    _record(store, "2026-01-01T00:00:00+00:00", user=1, systolic=115, diastolic=75)
    store.close()

    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://localhost"],
        crisis_systolic_mmhg=180,
        crisis_diastolic_mmhg=120,
        state_path=str(tmp_path / "state.json"),
    )
    now = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
    messages = check_alerts(db_path, config, now=now)
    assert len(messages) == 1
    assert "user 1" in messages[0]
