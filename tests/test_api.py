import asyncio
from dataclasses import replace

from aiohttp.test_utils import TestClient, TestServer

from etekcity_bp_daemon.api import build_app
from etekcity_bp_daemon.config import (
    DEFAULT_API_CONFIG,
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


def _run(coro):
    return asyncio.run(coro)


def test_assign_profile_tags_reading(tmp_path):
    db_path, row_id = _make_db(tmp_path)
    profiles_config = replace(DEFAULT_PROFILES_CONFIG, enabled=True, names=["Alice", "Bob"])
    app = build_app(db_path, DEFAULT_API_CONFIG, DEFAULT_REPORT_CONFIG, profiles_config)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"/assign-profile?id={row_id}&profile=Alice")
            assert resp.status == 200
            body = await resp.json()
            assert body == {"status": "ok", "id": row_id, "profile": "Alice"}

    _run(scenario())
    assert get_distinct_profiles(db_path) == {"Alice"}


def test_assign_profile_rejects_unknown_profile(tmp_path):
    db_path, row_id = _make_db(tmp_path)
    profiles_config = replace(DEFAULT_PROFILES_CONFIG, enabled=True, names=["Alice"])
    app = build_app(db_path, DEFAULT_API_CONFIG, DEFAULT_REPORT_CONFIG, profiles_config)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"/assign-profile?id={row_id}&profile=Mallory")
            assert resp.status == 400

    _run(scenario())


def test_assign_profile_unknown_id(tmp_path):
    db_path, _ = _make_db(tmp_path)
    profiles_config = replace(DEFAULT_PROFILES_CONFIG, enabled=True, names=["Alice"])
    app = build_app(db_path, DEFAULT_API_CONFIG, DEFAULT_REPORT_CONFIG, profiles_config)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/assign-profile?id=9999&profile=Alice")
            assert resp.status == 404

    _run(scenario())


def test_assign_profile_rejects_stale_reading_without_confirm(tmp_path):
    db_path, row_id = _make_db(tmp_path, recorded_at="2020-01-01T00:00:00+00:00")
    profiles_config = replace(
        DEFAULT_PROFILES_CONFIG, enabled=True, names=["Alice"], assign_window_seconds=60
    )
    app = build_app(db_path, DEFAULT_API_CONFIG, DEFAULT_REPORT_CONFIG, profiles_config)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"/assign-profile?id={row_id}&profile=Alice")
            assert resp.status == 409

            confirmed = await client.get(
                f"/assign-profile?id={row_id}&profile=Alice&confirm=1"
            )
            assert confirmed.status == 200

    _run(scenario())
    assert get_distinct_profiles(db_path) == {"Alice"}


def test_latest_endpoint_requires_token_when_set(tmp_path):
    db_path, _ = _make_db(tmp_path)
    api_config = replace(DEFAULT_API_CONFIG, token="secret")
    app = build_app(db_path, api_config, DEFAULT_REPORT_CONFIG, DEFAULT_PROFILES_CONFIG)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            unauthorized = await client.get("/latest")
            assert unauthorized.status == 401

            authorized = await client.get(
                "/latest", headers={"Authorization": "Bearer secret"}
            )
            assert authorized.status == 200

    _run(scenario())
