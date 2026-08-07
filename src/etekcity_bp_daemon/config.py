"""Configuration loading and persistence for the daemon."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class DaemonConfig:
    """Parsed daemon configuration."""

    config_path: Path
    address: str
    adapter: str
    cooldown_seconds: int
    db_path: str
    log_level: str


@dataclass
class ReportConfig:
    """Parsed [report] section controlling PDF/CSV report rendering."""

    include_address: bool
    include_profile: bool
    include_summary: bool
    include_categories: bool
    unit: str  # "mmhg" or "kpa"
    date_format: str  # "us" or "world"
    page_size: str  # "letter" or "a4"


DEFAULT_REPORT_CONFIG = ReportConfig(
    include_address=True,
    include_profile=False,
    include_summary=True,
    include_categories=True,
    unit="mmhg",
    date_format="world",
    page_size="letter",
)

_UNITS = ("mmhg", "kpa")
_DATE_FORMATS = ("us", "world")
_PAGE_SIZES = ("letter", "a4")


@dataclass
class MqttConfig:
    """Parsed [mqtt] section: optional MQTT publishing of live readings."""

    enabled: bool
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    topic_prefix: str
    qos: int
    retain: bool


DEFAULT_MQTT_CONFIG = MqttConfig(
    enabled=False,
    host="",
    port=1883,
    username="",
    password="",
    use_tls=False,
    topic_prefix="etekcity_bp_daemon",
    qos=0,
    retain=True,
)

_QOS_LEVELS = (0, 1, 2)


@dataclass
class AlertConfig:
    """Parsed [alerting] section: optional Apprise-based notifications."""

    enabled: bool
    apprise_urls: list[str]
    stale_after_days: int  # 0 disables the staleness check
    crisis_systolic_mmhg: int  # 0 disables the hypertensive-crisis check
    crisis_diastolic_mmhg: int  # 0 disables the hypertensive-crisis check
    alert_on_irregular_heartbeat: bool
    state_path: str


DEFAULT_ALERT_CONFIG = AlertConfig(
    enabled=False,
    apprise_urls=[],
    stale_after_days=0,
    crisis_systolic_mmhg=0,
    crisis_diastolic_mmhg=0,
    alert_on_irregular_heartbeat=False,
    state_path="/var/lib/etekcity-bp-daemon/alert-state.json",
)


@dataclass
class ApiConfig:
    """Parsed [api] section: optional local HTTP API for reading data on demand."""

    enabled: bool
    host: str
    port: int
    token: str  # "" means no authentication required


DEFAULT_API_CONFIG = ApiConfig(enabled=False, host="127.0.0.1", port=8080, token="")


@dataclass
class ProfilesConfig:
    """Parsed [profiles] section: names for the device's two hardware user slots.

    Unlike a shared scale, this device already tags every reading with a
    user slot (0 or 1) at the protocol level, so there's no "who was this?"
    guessing to do -- position 0 in ``names`` is User 1, position 1 is User 2.
    """

    enabled: bool
    names: list[str]


DEFAULT_PROFILES_CONFIG = ProfilesConfig(enabled=False, names=[])


def _parse_bool(value: str, key: str) -> bool:
    """Parse a yes/no-style config value.

    Args:
        value: Raw string from the config file.
        key: Dotted key name, used in the error message.

    Returns:
        The parsed boolean.

    Raises:
        ConfigError: If ``value`` isn't a recognized yes/no spelling.
    """
    normalized = value.strip().lower()
    if normalized in ("yes", "true", "1", "on"):
        return True
    if normalized in ("no", "false", "0", "off"):
        return False
    raise ConfigError(f"{key} must be yes/no, got {value!r}")


def load_config(config_path: str) -> DaemonConfig:
    """Load and validate the daemon configuration file.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed configuration.

    Raises:
        ConfigError: If the file is missing or a required value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(
            f"Config file not found: {path}. Copy "
            "config/etekcity-bp-daemon.ini.example to this path and edit it."
        )

    parser = configparser.ConfigParser()
    parser.read(path)

    monitor = parser["monitor"] if parser.has_section("monitor") else {}
    storage = parser["storage"] if parser.has_section("storage") else {}
    daemon = parser["daemon"] if parser.has_section("daemon") else {}

    try:
        cooldown_seconds = int(monitor.get("cooldown_seconds", "5"))
    except ValueError as exc:
        raise ConfigError("monitor.cooldown_seconds must be an integer") from exc

    db_path = storage.get("db_path", "").strip()
    if not db_path:
        raise ConfigError("storage.db_path must be set")

    return DaemonConfig(
        config_path=path,
        address=monitor.get("address", "").strip(),
        adapter=monitor.get("adapter", "").strip(),
        cooldown_seconds=cooldown_seconds,
        db_path=db_path,
        log_level=daemon.get("log_level", "INFO").strip().upper(),
    )


def load_report_config(config_path: str) -> ReportConfig:
    """Load the ``[report]`` section of the daemon config file, if present.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed report configuration, or ``DEFAULT_REPORT_CONFIG`` if the
        file has no ``[report]`` section.

    Raises:
        ConfigError: If the file is missing or a ``[report]`` value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser()
    parser.read(path)

    if not parser.has_section("report"):
        return DEFAULT_REPORT_CONFIG

    report = parser["report"]

    unit = report.get("unit", DEFAULT_REPORT_CONFIG.unit).strip().lower()
    if unit not in _UNITS:
        raise ConfigError(f"report.unit must be one of {_UNITS}, got {unit!r}")

    date_format = report.get("date_format", DEFAULT_REPORT_CONFIG.date_format).strip().lower()
    if date_format not in _DATE_FORMATS:
        raise ConfigError(
            f"report.date_format must be one of {_DATE_FORMATS}, got {date_format!r}"
        )

    page_size = report.get("page_size", DEFAULT_REPORT_CONFIG.page_size).strip().lower()
    if page_size not in _PAGE_SIZES:
        raise ConfigError(f"report.page_size must be one of {_PAGE_SIZES}, got {page_size!r}")

    return ReportConfig(
        include_address=_parse_bool(
            report.get("include_address", "yes"), "report.include_address"
        ),
        include_profile=_parse_bool(
            report.get("include_profile", "no"), "report.include_profile"
        ),
        include_summary=_parse_bool(
            report.get("include_summary", "yes"), "report.include_summary"
        ),
        include_categories=_parse_bool(
            report.get("include_categories", "yes"), "report.include_categories"
        ),
        unit=unit,
        date_format=date_format,
        page_size=page_size,
    )


def load_mqtt_config(config_path: str) -> MqttConfig:
    """Load the ``[mqtt]`` section of the daemon config file, if present.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed MQTT configuration, or ``DEFAULT_MQTT_CONFIG`` (disabled)
        if the file has no ``[mqtt]`` section.

    Raises:
        ConfigError: If the file is missing or a ``[mqtt]`` value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser()
    parser.read(path)

    if not parser.has_section("mqtt"):
        return DEFAULT_MQTT_CONFIG

    mqtt = parser["mqtt"]
    enabled = _parse_bool(mqtt.get("enabled", "no"), "mqtt.enabled")

    host = mqtt.get("host", "").strip()
    if enabled and not host:
        raise ConfigError("mqtt.host must be set when mqtt.enabled = yes")

    try:
        port = int(mqtt.get("port", str(DEFAULT_MQTT_CONFIG.port)))
    except ValueError as exc:
        raise ConfigError("mqtt.port must be an integer") from exc

    try:
        qos = int(mqtt.get("qos", str(DEFAULT_MQTT_CONFIG.qos)))
    except ValueError as exc:
        raise ConfigError("mqtt.qos must be an integer") from exc
    if qos not in _QOS_LEVELS:
        raise ConfigError(f"mqtt.qos must be one of {_QOS_LEVELS}, got {qos!r}")

    return MqttConfig(
        enabled=enabled,
        host=host,
        port=port,
        username=mqtt.get("username", "").strip(),
        password=mqtt.get("password", "").strip(),
        use_tls=_parse_bool(mqtt.get("use_tls", "no"), "mqtt.use_tls"),
        topic_prefix=mqtt.get("topic_prefix", DEFAULT_MQTT_CONFIG.topic_prefix).strip(),
        qos=qos,
        retain=_parse_bool(mqtt.get("retain", "yes"), "mqtt.retain"),
    )


def load_alert_config(config_path: str) -> AlertConfig:
    """Load the ``[alerting]`` section of the daemon config file, if present.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed alert configuration, or ``DEFAULT_ALERT_CONFIG``
        (disabled) if the file has no ``[alerting]`` section.

    Raises:
        ConfigError: If the file is missing or an ``[alerting]`` value is
            invalid, including enabling it with nothing to check or without
            any notification URLs.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser()
    parser.read(path)

    if not parser.has_section("alerting"):
        return DEFAULT_ALERT_CONFIG

    alerting = parser["alerting"]
    enabled = _parse_bool(alerting.get("enabled", "no"), "alerting.enabled")

    urls_raw = alerting.get("apprise_urls", "").strip()
    apprise_urls = [url.strip() for url in urls_raw.split(",") if url.strip()]
    if enabled and not apprise_urls:
        raise ConfigError("alerting.apprise_urls must be set when alerting.enabled = yes")

    try:
        stale_after_days = int(
            alerting.get("stale_after_days", str(DEFAULT_ALERT_CONFIG.stale_after_days))
        )
    except ValueError as exc:
        raise ConfigError("alerting.stale_after_days must be an integer") from exc
    if stale_after_days < 0:
        raise ConfigError("alerting.stale_after_days must be zero or positive")

    try:
        crisis_systolic_mmhg = int(
            alerting.get(
                "crisis_systolic_mmhg", str(DEFAULT_ALERT_CONFIG.crisis_systolic_mmhg)
            )
        )
    except ValueError as exc:
        raise ConfigError("alerting.crisis_systolic_mmhg must be an integer") from exc
    if crisis_systolic_mmhg < 0:
        raise ConfigError("alerting.crisis_systolic_mmhg must be zero or positive")

    try:
        crisis_diastolic_mmhg = int(
            alerting.get(
                "crisis_diastolic_mmhg", str(DEFAULT_ALERT_CONFIG.crisis_diastolic_mmhg)
            )
        )
    except ValueError as exc:
        raise ConfigError("alerting.crisis_diastolic_mmhg must be an integer") from exc
    if crisis_diastolic_mmhg < 0:
        raise ConfigError("alerting.crisis_diastolic_mmhg must be zero or positive")

    alert_on_irregular_heartbeat = _parse_bool(
        alerting.get("alert_on_irregular_heartbeat", "no"),
        "alerting.alert_on_irregular_heartbeat",
    )

    if (
        enabled
        and stale_after_days == 0
        and crisis_systolic_mmhg == 0
        and crisis_diastolic_mmhg == 0
        and not alert_on_irregular_heartbeat
    ):
        raise ConfigError(
            "alerting.enabled = yes but nothing is configured to check -- set "
            "stale_after_days, crisis_systolic_mmhg/crisis_diastolic_mmhg, or "
            "alert_on_irregular_heartbeat"
        )

    return AlertConfig(
        enabled=enabled,
        apprise_urls=apprise_urls,
        stale_after_days=stale_after_days,
        crisis_systolic_mmhg=crisis_systolic_mmhg,
        crisis_diastolic_mmhg=crisis_diastolic_mmhg,
        alert_on_irregular_heartbeat=alert_on_irregular_heartbeat,
        state_path=alerting.get("state_path", DEFAULT_ALERT_CONFIG.state_path).strip(),
    )


def load_api_config(config_path: str) -> ApiConfig:
    """Load the ``[api]`` section of the daemon config file, if present.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed API configuration, or ``DEFAULT_API_CONFIG`` (disabled,
        bound to loopback) if the file has no ``[api]`` section.

    Raises:
        ConfigError: If the file is missing or an ``[api]`` value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser()
    parser.read(path)

    if not parser.has_section("api"):
        return DEFAULT_API_CONFIG

    api = parser["api"]

    try:
        port = int(api.get("port", str(DEFAULT_API_CONFIG.port)))
    except ValueError as exc:
        raise ConfigError("api.port must be an integer") from exc

    return ApiConfig(
        enabled=_parse_bool(api.get("enabled", "no"), "api.enabled"),
        host=api.get("host", DEFAULT_API_CONFIG.host).strip() or DEFAULT_API_CONFIG.host,
        port=port,
        token=api.get("token", "").strip(),
    )


def load_profiles_config(config_path: str) -> ProfilesConfig:
    """Load the ``[profiles]`` section of the daemon config file, if present.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed profiles configuration, or ``DEFAULT_PROFILES_CONFIG``
        (disabled) if the file has no ``[profiles]`` section.

    Raises:
        ConfigError: If the file is missing, or enabled without any names.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser()
    parser.read(path)

    if not parser.has_section("profiles"):
        return DEFAULT_PROFILES_CONFIG

    profiles = parser["profiles"]
    enabled = _parse_bool(profiles.get("enabled", "no"), "profiles.enabled")

    names_raw = profiles.get("names", "").strip()
    names = [name.strip() for name in names_raw.split(",") if name.strip()]
    if enabled and not names:
        raise ConfigError("profiles.names must be set when profiles.enabled = yes")
    if len(names) > 2:
        raise ConfigError(
            "profiles.names supports at most 2 names (the device has two user "
            f"slots), got {len(names)}"
        )

    return ProfilesConfig(enabled=enabled, names=names)


def persist_discovered_address(config_path: Path, address: str) -> None:
    """Write a newly discovered device's address back to the config file.

    Rewrites only the ``address =`` line within the ``[monitor]`` section in
    place, so comments and formatting elsewhere in the file are preserved.

    Args:
        config_path: Path to the INI configuration file to update.
        address: BLE address of the discovered device.
    """
    lines = config_path.read_text().splitlines(keepends=True)
    in_monitor_section = False
    address_written = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_monitor_section = stripped == "[monitor]"
            continue
        if not in_monitor_section:
            continue
        if stripped.startswith("address") and "=" in stripped and not address_written:
            lines[i] = f"address = {address}\n"
            address_written = True

    config_path.write_text("".join(lines))
