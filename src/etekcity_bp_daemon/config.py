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
    include_goal_progress: bool
    unit: str  # "mmhg" or "kpa"
    date_format: str  # "us" or "world"
    page_size: str  # "letter" or "a4"


DEFAULT_REPORT_CONFIG = ReportConfig(
    include_address=True,
    include_profile=False,
    include_summary=True,
    include_categories=True,
    include_goal_progress=False,
    unit="mmhg",
    date_format="world",
    page_size="letter",
)

_UNITS = ("mmhg", "kpa")
_DATE_FORMATS = ("us", "world")
_PAGE_SIZES = ("letter", "a4")


@dataclass
class PatientConfig:
    """A profile's identifying info, report preferences, and alert overrides.

    Loaded from a ``[profile.<name>]`` section (see ``load_profile_details``)
    or left at these blanks/unset when no profile is selected. Report
    fields (``unit``, ``date_format``, ``page_size``, goals) are consumed by
    ``report.py``; alert fields (``apprise_urls``, ``stale_after_days``,
    ``alert_on_irregular_heartbeat``) are consumed by ``alerting.py``. Every
    field is optional -- a profile with no section at all still tags and
    reports normally, just without personalization.
    """

    name: str
    email: str
    notes: str
    unit: str  # "" (unset, use report.unit), "mmhg", or "kpa"
    date_format: str  # "" (unset, use report.date_format), "us", or "world"
    page_size: str  # "" (unset, use report.page_size), "letter", or "a4"
    goal_systolic_mmhg: int | None  # None means unset
    goal_diastolic_mmhg: int | None  # None means unset
    goal_pulse_bpm: int | None  # None means unset
    apprise_urls: list[str]  # empty means "use [alerting] apprise_urls"
    stale_after_days: int | None  # None means "use [alerting] stale_after_days"
    alert_on_irregular_heartbeat: bool | None  # None means "use [alerting]'s value"


DEFAULT_PATIENT_CONFIG = PatientConfig(
    name="",
    email="",
    notes="",
    unit="",
    date_format="",
    page_size="",
    goal_systolic_mmhg=None,
    goal_diastolic_mmhg=None,
    goal_pulse_bpm=None,
    apprise_urls=[],
    stale_after_days=None,
    alert_on_irregular_heartbeat=None,
)


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
    """Parsed [profiles] section: who-was-this tagging for a shared device.

    The device's own user slot (0 or 1) is too coarse to use as identity --
    it can't tell a third or fourth person apart from whoever normally uses
    that slot, and only ever reports one of two values no matter how many
    people actually share the device. So tagging asks a human instead, the
    same way etekcity-scale-daemon does. When the HTTP API is enabled, a new
    reading is announced via an ntfy notification with one HTTP action
    button per profile, each calling back into the API to tag the reading.
    When the API is disabled, there's nothing for ntfy's action buttons to
    call back to, so a local dunstify prompt is used instead, which resolves
    synchronously in-process.
    """

    enabled: bool
    names: list[str]
    ntfy_url: str
    ntfy_token: str
    api_base_url: str
    dunstify_timeout_seconds: int
    assign_window_seconds: int  # 0 disables the staleness check


DEFAULT_PROFILES_CONFIG = ProfilesConfig(
    enabled=False,
    names=[],
    ntfy_url="",
    ntfy_token="",
    api_base_url="http://127.0.0.1:8080",
    dunstify_timeout_seconds=30,
    assign_window_seconds=0,
)


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


def _parse_optional_bool(value: str, key: str) -> bool | None:
    """Parse a yes/no-style config value, or None if left blank (inherit).

    Args:
        value: Raw string from the config file.
        key: Dotted key name, used in the error message.

    Returns:
        The parsed boolean, or None if ``value`` is blank.

    Raises:
        ConfigError: If ``value`` is non-blank and not a recognized yes/no
            spelling.
    """
    if not value.strip():
        return None
    return _parse_bool(value, key)


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
        include_goal_progress=_parse_bool(
            report.get("include_goal_progress", "no"), "report.include_goal_progress"
        ),
        unit=unit,
        date_format=date_format,
        page_size=page_size,
    )


def _parse_positive_int(value: str, key: str) -> int | None:
    """Parse an optional positive integer, or None if left blank.

    Args:
        value: Raw string from the config file.
        key: Dotted key name, used in the error message.

    Returns:
        The parsed integer, or None if ``value`` is blank.

    Raises:
        ConfigError: If ``value`` is non-blank and not a positive integer.
    """
    if not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if parsed <= 0:
        raise ConfigError(f"{key} must be a positive number")
    return parsed


def load_profile_details(config_path: str, profile: str) -> PatientConfig:
    """Load one ``[profile.<name>]`` section: identity, report prefs, alert overrides.

    Each profile is self-contained -- a missing section just falls back to
    blanks/unset, since none of these fields are required for the daemon to
    function; they only personalize reports/alerts if provided.

    Args:
        config_path: Path to the INI configuration file.
        profile: The profile name, expected to match one of the names in
            ``[profiles] names``.

    Returns:
        A ``PatientConfig`` for this profile (``name`` defaults to the
        profile name itself if left blank). All fields are "unset" defaults
        if the section doesn't exist at all.

    Raises:
        ConfigError: If the file is missing or a value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser()
    parser.read(path)

    section_name = f"profile.{profile}"
    if not parser.has_section(section_name):
        return PatientConfig(
            name=profile,
            email="",
            notes="",
            unit="",
            date_format="",
            page_size="",
            goal_systolic_mmhg=None,
            goal_diastolic_mmhg=None,
            goal_pulse_bpm=None,
            apprise_urls=[],
            stale_after_days=None,
            alert_on_irregular_heartbeat=None,
        )

    section = parser[section_name]

    unit = section.get("unit", "").strip().lower()
    if unit and unit not in _UNITS:
        raise ConfigError(f"{section_name}.unit must be one of {_UNITS}, got {unit!r}")

    date_format = section.get("date_format", "").strip().lower()
    if date_format and date_format not in _DATE_FORMATS:
        raise ConfigError(
            f"{section_name}.date_format must be one of {_DATE_FORMATS}, got {date_format!r}"
        )

    page_size = section.get("page_size", "").strip().lower()
    if page_size and page_size not in _PAGE_SIZES:
        raise ConfigError(
            f"{section_name}.page_size must be one of {_PAGE_SIZES}, got {page_size!r}"
        )

    goal_systolic_mmhg = _parse_positive_int(
        section.get("goal_systolic_mmhg", ""), f"{section_name}.goal_systolic_mmhg"
    )
    goal_diastolic_mmhg = _parse_positive_int(
        section.get("goal_diastolic_mmhg", ""), f"{section_name}.goal_diastolic_mmhg"
    )
    goal_pulse_bpm = _parse_positive_int(
        section.get("goal_pulse_bpm", ""), f"{section_name}.goal_pulse_bpm"
    )

    urls_raw = section.get("apprise_urls", "").strip()
    apprise_urls = [url.strip() for url in urls_raw.split(",") if url.strip()]

    stale_after_days = None
    stale_after_days_str = section.get("stale_after_days", "").strip()
    if stale_after_days_str:
        try:
            stale_after_days = int(stale_after_days_str)
        except ValueError as exc:
            raise ConfigError(f"{section_name}.stale_after_days must be an integer") from exc
        if stale_after_days < 0:
            raise ConfigError(f"{section_name}.stale_after_days must be zero or positive")

    alert_on_irregular_heartbeat = _parse_optional_bool(
        section.get("alert_on_irregular_heartbeat", ""),
        f"{section_name}.alert_on_irregular_heartbeat",
    )

    return PatientConfig(
        name=section.get("name", "").strip() or profile,
        email=section.get("email", "").strip(),
        notes=section.get("notes", "").strip(),
        unit=unit,
        date_format=date_format,
        page_size=page_size,
        goal_systolic_mmhg=goal_systolic_mmhg,
        goal_diastolic_mmhg=goal_diastolic_mmhg,
        goal_pulse_bpm=goal_pulse_bpm,
        apprise_urls=apprise_urls,
        stale_after_days=stale_after_days,
        alert_on_irregular_heartbeat=alert_on_irregular_heartbeat,
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

    Note that whether the ntfy or dunstify path is actually usable also
    depends on ``[api] enabled`` -- that cross-check happens where both
    configs are loaded together (the daemon's startup), not here, since
    this loader only sees its own section.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed profiles configuration, or ``DEFAULT_PROFILES_CONFIG``
        (disabled) if the file has no ``[profiles]`` section.

    Raises:
        ConfigError: If the file is missing, enabled without any names, or
            a numeric value is invalid.
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

    try:
        dunstify_timeout_seconds = int(
            profiles.get(
                "dunstify_timeout_seconds",
                str(DEFAULT_PROFILES_CONFIG.dunstify_timeout_seconds),
            )
        )
    except ValueError as exc:
        raise ConfigError("profiles.dunstify_timeout_seconds must be an integer") from exc

    try:
        assign_window_seconds = int(
            profiles.get(
                "assign_window_seconds",
                str(DEFAULT_PROFILES_CONFIG.assign_window_seconds),
            )
        )
    except ValueError as exc:
        raise ConfigError("profiles.assign_window_seconds must be an integer") from exc
    if assign_window_seconds < 0:
        raise ConfigError("profiles.assign_window_seconds must be zero or positive")

    return ProfilesConfig(
        enabled=enabled,
        names=names,
        ntfy_url=profiles.get("ntfy_url", "").strip(),
        ntfy_token=profiles.get("ntfy_token", "").strip(),
        api_base_url=(
            profiles.get("api_base_url", DEFAULT_PROFILES_CONFIG.api_base_url).strip()
            or DEFAULT_PROFILES_CONFIG.api_base_url
        ),
        dunstify_timeout_seconds=dunstify_timeout_seconds,
        assign_window_seconds=assign_window_seconds,
    )


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
