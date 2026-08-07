"""Check for stale, hypertensive-range, or irregular-heartbeat readings and notify via Apprise."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import apprise

from ._version import __version__
from .config import AlertConfig, ConfigError, load_alert_config, load_config

# Minimum time between repeat staleness alerts for the same user slot, so a
# once-hourly check doesn't re-notify every single run while data stays old.
_STALE_ALERT_THROTTLE = timedelta(days=1)


def _load_state(state_path: str) -> dict[str, dict[str, str]]:
    """Load per-user-slot alert state, tolerating a missing or corrupt file."""
    path = Path(state_path)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state_path: str, state: dict[str, dict[str, str]]) -> None:
    """Persist per-user-slot alert state, creating the parent directory if needed."""
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _all_address_users(db_path: str) -> list[tuple[str, int]]:
    """Return every distinct (address, user) pair with at least one reading."""
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            "SELECT DISTINCT address, user FROM readings"
        ).fetchall()
    finally:
        connection.close()


def _latest_reading(db_path: str, address: str, user: int) -> tuple | None:
    """Return the most recent reading row for one (address, user) pair."""
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            "SELECT recorded_at, systolic_mmhg, diastolic_mmhg, irregular_heartbeat "
            "FROM readings WHERE address = ? AND user = ? "
            "ORDER BY recorded_at DESC LIMIT 1",
            (address, user),
        ).fetchone()
    finally:
        connection.close()


def check_alerts(
    db_path: str, alert_config: AlertConfig, now: datetime | None = None
) -> list[str]:
    """Evaluate staleness, hypertensive-range, and irregular-heartbeat conditions.

    Checked independently for every (address, user) slot with at least one
    reading. A crisis-range or irregular-heartbeat alert only fires the
    first time a given "latest reading" is seen, so it isn't repeated on
    every subsequent run until a new reading arrives. A staleness alert
    repeats at most once per ``_STALE_ALERT_THROTTLE`` while the condition
    persists.

    Args:
        db_path: Path to the SQLite database file.
        alert_config: Parsed [alerting] configuration.
        now: Current UTC time; injectable for testing. Defaults to
            ``datetime.now(timezone.utc)``.

    Returns:
        Triggered alert messages (empty if nothing was triggered). The
        caller is responsible for actually sending them.
    """
    now = now or datetime.now(timezone.utc)
    state = _load_state(alert_config.state_path)
    messages: list[str] = []

    for address, user in _all_address_users(db_path):
        key = f"{address}:{user}"
        slot_state = state.get(key, {})
        row = _latest_reading(db_path, address, user)
        if row is None:
            continue

        recorded_at, systolic, diastolic, irregular_heartbeat = row
        latest_dt = datetime.fromisoformat(recorded_at)
        label = f"{address} (user {user + 1})"

        if alert_config.stale_after_days > 0:
            if now - latest_dt > timedelta(days=alert_config.stale_after_days):
                last_alert = slot_state.get("last_stale_alert_at")
                last_alert_dt = datetime.fromisoformat(last_alert) if last_alert else None
                if last_alert_dt is None or now - last_alert_dt > _STALE_ALERT_THROTTLE:
                    messages.append(
                        f"No reading from {label} in over "
                        f"{alert_config.stale_after_days} day(s) (last: {recorded_at})"
                    )
                    slot_state["last_stale_alert_at"] = now.isoformat()
            else:
                slot_state.pop("last_stale_alert_at", None)

        already_seen = slot_state.get("last_seen_recorded_at") == recorded_at
        if not already_seen:
            crisis_systolic = alert_config.crisis_systolic_mmhg
            crisis_diastolic = alert_config.crisis_diastolic_mmhg
            if (
                (crisis_systolic > 0 and systolic is not None and systolic >= crisis_systolic)
                or (
                    crisis_diastolic > 0
                    and diastolic is not None
                    and diastolic >= crisis_diastolic
                )
            ):
                messages.append(
                    f"Hypertensive-crisis-range reading from {label}: "
                    f"{systolic}/{diastolic} mmHg -- seek medical attention"
                )

            if alert_config.alert_on_irregular_heartbeat and irregular_heartbeat:
                messages.append(f"Irregular heartbeat detected on {label}'s latest reading")

        slot_state["last_seen_recorded_at"] = recorded_at
        state[key] = slot_state

    _save_state(alert_config.state_path, state)
    return messages


def send_alerts(apprise_urls: list[str], messages: list[str]) -> None:
    """Send each message via Apprise to every configured notification URL.

    Args:
        apprise_urls: Apprise service URLs to notify.
        messages: One notification is sent per message.
    """
    notifier = apprise.Apprise()
    for url in apprise_urls:
        notifier.add(url)
    for message in messages:
        notifier.notify(title="Etekcity Blood Pressure Alert", body=message)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="etekcity-bp-alert-check",
        description=(
            "Check for stale, hypertensive-range, or irregular-heartbeat "
            "readings and notify via Apprise."
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
        Process exit code.
    """
    args = _parse_args(argv)

    try:
        db_path = load_config(args.config).db_path
        alert_config = load_alert_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 1

    if not alert_config.enabled:
        print("Alerting is disabled (alerting.enabled = no).")
        return 0

    messages = check_alerts(db_path, alert_config)
    if not messages:
        print("No alerts triggered.")
        return 0

    send_alerts(alert_config.apprise_urls, messages)
    for message in messages:
        print(f"ALERT: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
