"""Lightweight local HTTP API: latest readings, on-demand reports, and profile tagging.

Reads from (and, for /api/v1/assign-profile, writes to) the same SQLite
database as everything else in this package -- it's a standalone view onto
that data, not part of the daemon's BLE connection lifecycle, so it works
whether or not the daemon is currently running.

All routes, including the unauthenticated ``/health`` and ``/capabilities``
checks, live under the ``/api/v1/`` prefix.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

from aiohttp import web

from ._version import __version__
from .config import (
    DEFAULT_MQTT_CONFIG,
    DEFAULT_PATIENT_CONFIG,
    ApiConfig,
    ConfigError,
    MqttConfig,
    load_api_config,
    load_config,
    load_mqtt_config,
    load_profile_details,
    load_profiles_config,
    load_report_config,
)
from .report import _apply_profile_overrides, _resolve_range, build_csv, build_pdf, fetch_rows
from .storage import ensure_schema, get_reading_recorded_at, set_reading_profile

_VALID_FORMATS = ("pdf", "csv")
_VALID_PERIODS = ("7d", "30d", "90d", "1y", "all")

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def is_insecurely_exposed(api_config: ApiConfig) -> bool:
    """Return whether the API is bound to a non-loopback address with no auth token.

    Not a hard failure -- there are legitimate reasons to do this (a reverse
    proxy in front that handles auth) -- but it's the shape of a mistake
    (forgetting to set a token before exposing the API on the LAN) worth
    surfacing rather than silently allowing.
    """
    return api_config.host not in _LOOPBACK_HOSTS and not api_config.token


def _latest_readings(
    db_path: str, address: str | None, profile: str | None = None
) -> list[dict[str, object]]:
    """Return the most recent reading for each (address, user) slot.

    Args:
        db_path: Path to the SQLite database file.
        address: Restrict to a single device's BLE address, if given.
        profile: Restrict to readings tagged with this profile name, if given.

    Returns:
        One dict per (address, user) slot, each with the same fields stored
        in the database.
    """
    query = (
        "SELECT id, recorded_at, address, user, profile, systolic_mmhg, "
        "diastolic_mmhg, systolic_kpa, diastolic_kpa, pulse_bpm, "
        "irregular_heartbeat, motion_detected, display_unit, error_code "
        "FROM readings r1 WHERE recorded_at = ("
        "    SELECT MAX(recorded_at) FROM readings r2 "
        "    WHERE r2.address = r1.address AND r2.user = r1.user"
        ")"
    )
    params: list[str] = []
    if address:
        query += " AND address = ?"
        params.append(address)
    if profile:
        query += " AND profile = ?"
        params.append(profile)

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(query, params).fetchall()
    finally:
        connection.close()

    return [
        {
            "id": row[0],
            "recorded_at": row[1],
            "address": row[2],
            "user": row[3],
            "profile": row[4],
            "systolic_mmhg": row[5],
            "diastolic_mmhg": row[6],
            "systolic_kpa": row[7],
            "diastolic_kpa": row[8],
            "pulse_bpm": row[9],
            "irregular_heartbeat": bool(row[10]),
            "motion_detected": bool(row[11]),
            "display_unit": row[12],
            "error_code": row[13],
        }
        for row in rows
    ]


def _require_auth(request: web.Request) -> web.Response | None:
    """Return a 401 response if a token is configured and missing/wrong.

    Args:
        request: The incoming request. Reads the configured token from
            ``request.app["api_token"]``.

    Returns:
        A 401 JSON response if unauthorized, or None if the request may
        proceed.
    """
    token = request.app["api_token"]
    if not token:
        return None
    if request.headers.get("Authorization", "") != f"Bearer {token}":
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


async def handle_health(request: web.Request) -> web.Response:
    """GET /api/v1/health -- unauthenticated liveness check."""
    return web.json_response({"status": "ok", "version": __version__})


async def handle_capabilities(request: web.Request) -> web.Response:
    """GET /api/v1/capabilities -- unauthenticated description of what this daemon reports.

    Static/config-derived facts about this daemon's data model, meant for a
    Health Hub aggregator to introspect without hardcoding assumptions:
    what it measures, how profiles work, why there's no measured-at
    timestamp, and whether MQTT publishing is on.
    """
    mqtt_config: MqttConfig = request.app["mqtt_config"]
    mqtt_capability: dict[str, object] = {"enabled": mqtt_config.enabled}
    if mqtt_config.enabled:
        mqtt_capability["topic_pattern"] = f"{mqtt_config.topic_prefix}/<address>/state"

    return web.json_response(
        {
            "daemon": "etekcity-bp",
            "api_version": "v1",
            "measurement_types": ["systolic", "diastolic", "pulse"],
            "measurement_modes": ["spot"],
            "profile_model": (
                "assignable -- readings are stored with a nullable profile and "
                "tagged after the fact via /api/v1/assign-profile; the raw "
                "device 'user' field (0/1) is only which of the device's two "
                "internal memory slots the reading was stored under, not a "
                "profile identity"
            ),
            "timestamp_fields": {
                "recorded_at": (
                    "arrival time at this daemon; the BLE protocol has no "
                    "device-side clock/timestamp at all, so there is no "
                    "measured_at distinct from received_at for this device"
                )
            },
            "mqtt": mqtt_capability,
        }
    )


async def handle_latest(request: web.Request) -> web.Response:
    """GET /api/v1/latest[?address=...&profile=...] -- most recent reading per user slot, JSON."""
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    readings = _latest_readings(
        request.app["db_path"],
        request.query.get("address"),
        request.query.get("profile"),
    )
    if not readings:
        return web.json_response({"error": "no readings found"}, status=404)
    return web.json_response(readings)


async def handle_assign_profile(request: web.Request) -> web.Response:
    """POST /api/v1/assign-profile?id=...&profile=...[&confirm=1] -- tag a reading.

    Accepts GET too, since notification action buttons (ntfy's http action
    in particular) are simplest to configure as a bare URL hit rather than
    a POST with a body.

    If ``profiles.assign_window_seconds`` is set, tagging a reading older
    than that window is rejected unless ``confirm=1`` is also passed. This
    guards against a delayed ntfy notification -- tapped long after it was
    sent, once connectivity returns -- silently tagging a now-stale reading
    that's no longer what the person meant to answer for. Deliberate manual
    corrections (see the README) just add ``&confirm=1``.
    """
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    profiles_config = request.app["profiles_config"]
    profile = request.query.get("profile", "")
    if profile not in profiles_config.names:
        return web.json_response(
            {"error": f"profile must be one of {profiles_config.names}"}, status=400
        )

    row_id_raw = request.query.get("id", "")
    try:
        row_id = int(row_id_raw)
    except ValueError:
        return web.json_response({"error": "id must be an integer"}, status=400)

    db_path = request.app["db_path"]
    recorded_at_raw = get_reading_recorded_at(db_path, row_id)
    if recorded_at_raw is None:
        return web.json_response({"error": f"no reading with id {row_id}"}, status=404)

    window = profiles_config.assign_window_seconds
    confirmed = request.query.get("confirm") == "1"
    if window and not confirmed:
        age_seconds = (
            datetime.now(timezone.utc) - datetime.fromisoformat(recorded_at_raw)
        ).total_seconds()
        if age_seconds > window:
            return web.json_response(
                {
                    "error": (
                        f"reading {row_id} is {age_seconds:.0f}s old, older than "
                        f"profiles.assign_window_seconds ({window}s) -- likely a "
                        "delayed notification tap rather than the intended "
                        "reading; pass &confirm=1 to tag it anyway"
                    )
                },
                status=409,
            )

    updated = set_reading_profile(db_path, row_id, profile)
    if not updated:
        return web.json_response({"error": f"no reading with id {row_id}"}, status=404)
    return web.json_response({"status": "ok", "id": row_id, "profile": profile})


async def handle_report(request: web.Request) -> web.Response:
    """GET /api/v1/report[?format=pdf|csv&period=...&from=...&to=...&address=...&profile=...].

    Generates a report on demand using the same config-driven settings as
    ``etekcity-bp-report`` and returns it as a file download. Report
    personalization (name/email/notes, unit/date-format/page-size
    overrides, goals) only ever comes from ``profile``'s own
    ``[profile.<name>]`` section -- there's no shared fallback, since
    defaulting to someone else's goal would be a correctness bug, not a
    convenience.
    """
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    fmt = request.query.get("format", "pdf")
    if fmt not in _VALID_FORMATS:
        return web.json_response({"error": f"format must be one of {_VALID_FORMATS}"}, status=400)

    period = request.query.get("period", "all")
    if period not in _VALID_PERIODS:
        return web.json_response({"error": f"period must be one of {_VALID_PERIODS}"}, status=400)

    try:
        start, end = _resolve_range(period, request.query.get("from"), request.query.get("to"))
    except ValueError as exc:
        return web.json_response({"error": f"invalid date: {exc}"}, status=400)

    profile = request.query.get("profile")
    patient_config = DEFAULT_PATIENT_CONFIG
    if profile:
        try:
            patient_config = load_profile_details(request.app["config_path"], profile)
        except ConfigError as exc:
            return web.json_response({"error": str(exc)}, status=400)

    rows = fetch_rows(
        request.app["db_path"],
        request.query.get("address"),
        start,
        end,
        profile,
    )
    if not rows:
        return web.json_response(
            {"error": "no readings found for the given range/filters"}, status=404
        )

    # A profile's own unit/date_format/page_size (if set) override the
    # shared report config, same reasoning as etekcity-bp-report --profile.
    report_config = request.app["report_config"]
    effective_report_config = _apply_profile_overrides(report_config, patient_config)

    fd, temp_path = tempfile.mkstemp(suffix=f".{fmt}")
    os.close(fd)
    try:
        if fmt == "csv":
            build_csv(rows, temp_path, effective_report_config)
            content_type = "text/csv"
        else:
            build_pdf(rows, temp_path, effective_report_config, patient_config)
            content_type = "application/pdf"
        with open(temp_path, "rb") as report_file:
            body = report_file.read()
    finally:
        os.remove(temp_path)

    return web.Response(
        body=body,
        content_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="bp-report.{fmt}"'},
    )


def build_app(
    config_path: str,
    db_path: str,
    api_config: ApiConfig,
    report_config,
    profiles_config,
    mqtt_config: MqttConfig | None = None,
) -> web.Application:
    """Build the aiohttp application with routes and shared state attached.

    Args:
        config_path: Path to the INI configuration file, used to load a
            specific profile's [profile.<name>] personalization on demand.
        db_path: Path to the SQLite database file.
        api_config: Supplies the auth token.
        report_config: Used for on-demand report generation.
        profiles_config: Supplies the valid profile names for
            /api/v1/assign-profile.
        mqtt_config: Supplies the MQTT enabled flag/topic prefix reported by
            /api/v1/capabilities. Defaults to the disabled config if omitted
            (e.g. in tests that don't care about MQTT).

    Returns:
        A configured, unstarted aiohttp Application.
    """
    app = web.Application()
    app["config_path"] = config_path
    app["db_path"] = db_path
    app["api_token"] = api_config.token
    app["report_config"] = report_config
    app["profiles_config"] = profiles_config
    app["mqtt_config"] = mqtt_config if mqtt_config is not None else DEFAULT_MQTT_CONFIG
    app.router.add_get("/api/v1/health", handle_health)
    app.router.add_get("/api/v1/capabilities", handle_capabilities)
    app.router.add_get("/api/v1/latest", handle_latest)
    app.router.add_get("/api/v1/report", handle_report)
    app.router.add_get("/api/v1/assign-profile", handle_assign_profile)
    app.router.add_post("/api/v1/assign-profile", handle_assign_profile)
    return app


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="etekcity-bp-api",
        description=(
            "Lightweight local HTTP API: latest readings, on-demand "
            "reports, and profile tagging."
        ),
    )
    parser.add_argument(
        "-c", "--config", required=True, help="Path to the daemon's INI config file"
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code. Only returns while disabled or on a config
        error -- otherwise blocks forever serving requests.
    """
    args = _parse_args(argv)

    try:
        db_path = load_config(args.config).db_path
        api_config = load_api_config(args.config)
        report_config = load_report_config(args.config)
        profiles_config = load_profiles_config(args.config)
        mqtt_config = load_mqtt_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 1

    if not api_config.enabled:
        print("API is disabled (api.enabled = no).")
        return 0

    if is_insecurely_exposed(api_config):
        print(
            f"WARNING: api.host is {api_config.host!r} (not loopback) but api.token "
            "is unset -- anyone who can reach this address can read readings and "
            "generate reports. Set api.token, or bind to 127.0.0.1 and put a "
            "reverse proxy with its own auth in front if you need remote access."
        )

    ensure_schema(db_path)
    app = build_app(args.config, db_path, api_config, report_config, profiles_config, mqtt_config)
    print(f"Listening on http://{api_config.host}:{api_config.port}")
    web.run_app(app, host=api_config.host, port=api_config.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
