from pathlib import Path

import pytest

from etekcity_bp_daemon.config import (
    ConfigError,
    load_alert_config,
    load_api_config,
    load_config,
    load_mqtt_config,
    load_profiles_config,
    load_report_config,
    persist_discovered_address,
)

_BASE_CONFIG = """
[monitor]
address = AA:BB:CC:DD:EE:FF
adapter =
cooldown_seconds = 5

[storage]
db_path = /tmp/readings.db

[daemon]
log_level = INFO
"""


def _write(tmp_path: Path, contents: str) -> str:
    path = tmp_path / "config.ini"
    path.write_text(contents)
    return str(path)


def test_load_config_basic(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG)
    config = load_config(config_path)
    assert config.address == "AA:BB:CC:DD:EE:FF"
    assert config.cooldown_seconds == 5
    assert config.db_path == "/tmp/readings.db"
    assert config.log_level == "INFO"


def test_load_config_missing_file():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/config.ini")


def test_load_config_requires_db_path(tmp_path):
    config_path = _write(tmp_path, "[storage]\ndb_path =\n")
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_load_report_config_defaults(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG)
    report = load_report_config(config_path)
    assert report.unit == "mmhg"
    assert report.include_categories is True


def test_load_report_config_invalid_unit(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG + "\n[report]\nunit = pounds\n")
    with pytest.raises(ConfigError):
        load_report_config(config_path)


def test_load_mqtt_config_requires_host_when_enabled(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG + "\n[mqtt]\nenabled = yes\n")
    with pytest.raises(ConfigError):
        load_mqtt_config(config_path)


def test_load_mqtt_config_disabled_default(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG)
    mqtt = load_mqtt_config(config_path)
    assert mqtt.enabled is False


def test_load_alert_config_requires_urls_when_enabled(tmp_path):
    config_path = _write(
        tmp_path, _BASE_CONFIG + "\n[alerting]\nenabled = yes\nstale_after_days = 2\n"
    )
    with pytest.raises(ConfigError):
        load_alert_config(config_path)


def test_load_alert_config_requires_something_to_check(tmp_path):
    config_path = _write(
        tmp_path,
        _BASE_CONFIG + "\n[alerting]\nenabled = yes\napprise_urls = tgram://x/y\n",
    )
    with pytest.raises(ConfigError):
        load_alert_config(config_path)


def test_load_alert_config_valid(tmp_path):
    config_path = _write(
        tmp_path,
        _BASE_CONFIG
        + "\n[alerting]\nenabled = yes\napprise_urls = tgram://x/y\n"
        "crisis_systolic_mmhg = 180\n",
    )
    alert = load_alert_config(config_path)
    assert alert.enabled is True
    assert alert.crisis_systolic_mmhg == 180
    assert alert.apprise_urls == ["tgram://x/y"]


def test_load_api_config_defaults(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG)
    api = load_api_config(config_path)
    assert api.enabled is False
    assert api.host == "127.0.0.1"
    assert api.port == 8080


def test_load_profiles_config_more_than_two_names_allowed(tmp_path):
    config_path = _write(
        tmp_path, _BASE_CONFIG + "\n[profiles]\nenabled = yes\nnames = A, B, C, D\n"
    )
    profiles = load_profiles_config(config_path)
    assert profiles.names == ["A", "B", "C", "D"]


def test_load_profiles_config_valid(tmp_path):
    config_path = _write(
        tmp_path,
        _BASE_CONFIG
        + "\n[profiles]\nenabled = yes\nnames = Alice, Bob\n"
        "ntfy_url = https://ntfy.sh/topic\nassign_window_seconds = 120\n",
    )
    profiles = load_profiles_config(config_path)
    assert profiles.enabled is True
    assert profiles.names == ["Alice", "Bob"]
    assert profiles.ntfy_url == "https://ntfy.sh/topic"
    assert profiles.assign_window_seconds == 120


def test_load_profiles_config_requires_names_when_enabled(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG + "\n[profiles]\nenabled = yes\n")
    with pytest.raises(ConfigError):
        load_profiles_config(config_path)


def test_load_profiles_config_invalid_assign_window(tmp_path):
    config_path = _write(
        tmp_path,
        _BASE_CONFIG + "\n[profiles]\nenabled = yes\nnames = Alice\n"
        "assign_window_seconds = -5\n",
    )
    with pytest.raises(ConfigError):
        load_profiles_config(config_path)


def test_persist_discovered_address(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[monitor]\naddress =\nadapter =\n\n[storage]\ndb_path = /tmp/x.db\n"
    )
    persist_discovered_address(config_path, "11:22:33:44:55:66")
    updated = config_path.read_text()
    assert "address = 11:22:33:44:55:66" in updated

    config = load_config(str(config_path))
    assert config.address == "11:22:33:44:55:66"
