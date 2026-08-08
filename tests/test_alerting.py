from dataclasses import replace
from datetime import datetime, timezone

from etekcity_bp_daemon.alerting import check_alerts
from etekcity_bp_daemon.config import DEFAULT_ALERT_CONFIG, DEFAULT_PATIENT_CONFIG
from etekcity_bp_daemon.storage import ReadingStore

_ADDRESS = "AA:BB:CC:DD:EE:FF"


def _record(store, recorded_at, user=0, profile=None, systolic=120, diastolic=80, irregular=False):
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
    alerts = check_alerts(db_path, config)
    assert alerts == []


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
    alerts = check_alerts(db_path, config, now=now)
    assert len(alerts) == 1
    assert "No reading" in alerts[0].message
    assert alerts[0].urls == ["json://localhost"]


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
    assert "crisis" in first[0].message.lower()
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
    alerts = check_alerts(db_path, config, now=now)
    assert len(alerts) == 1
    assert "Irregular heartbeat" in alerts[0].message


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
    alerts = check_alerts(db_path, config, now=now)
    assert len(alerts) == 1
    assert "user 1" in alerts[0].message


def test_profile_apprise_urls_override_routes_to_profile_only(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", profile="Alice", systolic=190, diastolic=125)
    store.close()

    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://shared"],
        crisis_systolic_mmhg=180,
        crisis_diastolic_mmhg=120,
        state_path=str(tmp_path / "state.json"),
    )
    alice = replace(DEFAULT_PATIENT_CONFIG, apprise_urls=["json://alice-phone"])
    now = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
    alerts = check_alerts(db_path, config, profile_configs={"Alice": alice}, now=now)
    assert len(alerts) == 1
    assert alerts[0].urls == ["json://alice-phone"]
    assert "Alice" in alerts[0].message


def test_profile_without_override_falls_back_to_global_urls(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", profile="Bob", systolic=190, diastolic=125)
    store.close()

    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://shared"],
        crisis_systolic_mmhg=180,
        crisis_diastolic_mmhg=120,
        state_path=str(tmp_path / "state.json"),
    )
    bob = replace(DEFAULT_PATIENT_CONFIG)  # no apprise_urls override
    now = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
    alerts = check_alerts(db_path, config, profile_configs={"Bob": bob}, now=now)
    assert len(alerts) == 1
    assert alerts[0].urls == ["json://shared"]


def test_profile_stale_after_days_override(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", profile="Alice")
    store.close()

    # Global staleness check disabled; Alice overrides it to 1 day.
    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://shared"],
        stale_after_days=0,
        state_path=str(tmp_path / "state.json"),
    )
    alice = replace(DEFAULT_PATIENT_CONFIG, stale_after_days=1)
    now = datetime(2026, 1, 5, tzinfo=timezone.utc)
    alerts = check_alerts(db_path, config, profile_configs={"Alice": alice}, now=now)
    assert len(alerts) == 1
    assert "No reading" in alerts[0].message


def test_profile_alert_on_irregular_heartbeat_override(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", profile="Alice", irregular=True)
    store.close()

    # Global irregular-heartbeat alerting off; Alice opts in.
    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://shared"],
        alert_on_irregular_heartbeat=False,
        state_path=str(tmp_path / "state.json"),
    )
    alice = replace(DEFAULT_PATIENT_CONFIG, alert_on_irregular_heartbeat=True)
    now = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
    alerts = check_alerts(db_path, config, profile_configs={"Alice": alice}, now=now)
    assert len(alerts) == 1
    assert "Irregular heartbeat" in alerts[0].message
