import asyncio
from dataclasses import replace

from aiohttp.test_utils import TestClient, TestServer

from etekcity_bp_daemon.api import build_app, is_insecurely_exposed
from etekcity_bp_daemon.config import (
    DEFAULT_API_CONFIG,
    DEFAULT_MQTT_CONFIG,
    DEFAULT_PROFILES_CONFIG,
    DEFAULT_REPORT_CONFIG,
)
from etekcity_bp_daemon.storage import ReadingStore, get_distinct_profiles

_ADDRESS = "AA:BB:CC:DD:EE:FF"


def _make_db(tmp_path, recorded_at="2026-01-01T00:00:00+00:00"):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    row_id = store.record(
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
    store.close()
    return db_path, row_id


def _build(tmp_path, db_path, api_config=DEFAULT_API_CONFIG,
           report_config=DEFAULT_REPORT_CONFIG, profiles_config=DEFAULT_PROFILES_CONFIG,
           config_path=None, mqtt_config=DEFAULT_MQTT_CONFIG):
    return build_app(
        config_path or str(tmp_path / "config.ini"),
        db_path,
        api_config,
        report_config,
        profiles_config,
        mqtt_config,
    )


def _run(coro):
    return asyncio.run(coro)


def test_capabilities_endpoint_unauthenticated_without_mqtt(tmp_path):
    db_path, _ = _make_db(tmp_path)
    api_config = replace(DEFAULT_API_CONFIG, token="secret")
    app = _build(tmp_path, db_path, api_config=api_config)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/capabilities")
            assert resp.status == 200
            body = await resp.json()
            assert body["daemon"] == "etekcity-bp"
            assert body["api_version"] == "v1"
            assert body["measurement_types"] == ["systolic", "diastolic", "pulse"]
            assert body["measurement_modes"] == ["spot"]
            assert "recorded_at" in body["timestamp_fields"]
            assert body["mqtt"] == {"enabled": False}

    _run(scenario())


def test_capabilities_endpoint_reports_mqtt_when_enabled(tmp_path):
    db_path, _ = _make_db(tmp_path)
    mqtt_config = replace(
        DEFAULT_MQTT_CONFIG, enabled=True, host="broker.local", topic_prefix="bp"
    )
    app = _build(tmp_path, db_path, mqtt_config=mqtt_config)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/capabilities")
            assert resp.status == 200
            body = await resp.json()
            assert body["mqtt"] == {
                "enabled": True,
                "topic_pattern": "bp/<address>/state",
            }

    _run(scenario())


def test_assign_profile_tags_reading(tmp_path):
    db_path, row_id = _make_db(tmp_path)
    profiles_config = replace(DEFAULT_PROFILES_CONFIG, enabled=True, names=["Alice", "Bob"])
    app = _build(tmp_path, db_path, profiles_config=profiles_config)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"/api/v1/assign-profile?id={row_id}&profile=Alice")
            assert resp.status == 200
            body = await resp.json()
            assert body == {"status": "ok", "id": row_id, "profile": "Alice"}

    _run(scenario())
    assert get_distinct_profiles(db_path) == {"Alice"}


def test_assign_profile_rejects_unknown_profile(tmp_path):
    db_path, row_id = _make_db(tmp_path)
    profiles_config = replace(DEFAULT_PROFILES_CONFIG, enabled=True, names=["Alice"])
    app = _build(tmp_path, db_path, profiles_config=profiles_config)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"/api/v1/assign-profile?id={row_id}&profile=Mallory")
            assert resp.status == 400

    _run(scenario())


def test_assign_profile_unknown_id(tmp_path):
    db_path, _ = _make_db(tmp_path)
    profiles_config = replace(DEFAULT_PROFILES_CONFIG, enabled=True, names=["Alice"])
    app = _build(tmp_path, db_path, profiles_config=profiles_config)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/assign-profile?id=9999&profile=Alice")
            assert resp.status == 404

    _run(scenario())


def test_assign_profile_rejects_stale_reading_without_confirm(tmp_path):
    db_path, row_id = _make_db(tmp_path, recorded_at="2020-01-01T00:00:00+00:00")
    profiles_config = replace(
        DEFAULT_PROFILES_CONFIG, enabled=True, names=["Alice"], assign_window_seconds=60
    )
    app = _build(tmp_path, db_path, profiles_config=profiles_config)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"/api/v1/assign-profile?id={row_id}&profile=Alice")
            assert resp.status == 409

            confirmed = await client.get(
                f"/api/v1/assign-profile?id={row_id}&profile=Alice&confirm=1"
            )
            assert confirmed.status == 200

    _run(scenario())
    assert get_distinct_profiles(db_path) == {"Alice"}


def test_latest_endpoint_requires_token_when_set(tmp_path):
    db_path, _ = _make_db(tmp_path)
    api_config = replace(DEFAULT_API_CONFIG, token="secret")
    app = _build(tmp_path, db_path, api_config=api_config)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            unauthorized = await client.get("/api/v1/latest")
            assert unauthorized.status == 401

            authorized = await client.get(
                "/api/v1/latest", headers={"Authorization": "Bearer secret"}
            )
            assert authorized.status == 200

    _run(scenario())


def test_report_endpoint_applies_profile_goal_and_unit(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    store.record(
        recorded_at="2026-01-01T00:00:00+00:00",
        address=_ADDRESS,
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
    store.close()
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[profile.Alice]\n"
        "name = Alice Smith\n"
        "email = alice@example.com\n"
        "unit = kpa\n"
        "goal_systolic_mmhg = 130\n"
        "goal_diastolic_mmhg = 80\n"
    )
    report_config = replace(DEFAULT_REPORT_CONFIG, include_goal_progress=True)
    app = _build(tmp_path, db_path, report_config=report_config, config_path=str(config_path))

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/report?profile=Alice")
            assert resp.status == 200
            assert resp.content_type == "application/pdf"
            body = await resp.read()
            assert body.startswith(b"%PDF")

    _run(scenario())


def test_report_endpoint_rejects_unknown_format(tmp_path):
    db_path, _ = _make_db(tmp_path)
    app = _build(tmp_path, db_path)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/report?format=xml")
            assert resp.status == 400

    _run(scenario())


def test_is_insecurely_exposed_loopback_without_token_is_fine():
    config = replace(DEFAULT_API_CONFIG, host="127.0.0.1", token="")
    assert is_insecurely_exposed(config) is False


def test_is_insecurely_exposed_non_loopback_without_token():
    config = replace(DEFAULT_API_CONFIG, host="0.0.0.0", token="")
    assert is_insecurely_exposed(config) is True


def test_is_insecurely_exposed_non_loopback_with_token_is_fine():
    config = replace(DEFAULT_API_CONFIG, host="0.0.0.0", token="secret")
    assert is_insecurely_exposed(config) is False


def test_is_insecurely_exposed_localhost_alias_is_loopback():
    config = replace(DEFAULT_API_CONFIG, host="localhost", token="")
    assert is_insecurely_exposed(config) is False
