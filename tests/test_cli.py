from etekcity_bp_daemon.cli import _check_config

_BASE_CONFIG = """
[monitor]
address = AA:BB:CC:DD:EE:FF
adapter =
cooldown_seconds = 5

[storage]
db_path = {db_path}

[daemon]
log_level = INFO
"""


def _write(tmp_path, contents):
    path = tmp_path / "config.ini"
    path.write_text(contents)
    return str(path)


def test_check_config_valid_minimal(tmp_path, capsys):
    config_path = _write(tmp_path, _BASE_CONFIG.format(db_path=str(tmp_path / "readings.db")))
    assert _check_config(config_path) == 0
    assert "OK" in capsys.readouterr().out


def test_check_config_reports_invalid_profile_section(tmp_path, capsys):
    contents = (
        _BASE_CONFIG.format(db_path=str(tmp_path / "readings.db"))
        + "\n[profiles]\nenabled = yes\nnames = Alice\n"
        "\n[profile.Alice]\ngoal_systolic_mmhg = -5\n"
    )
    config_path = _write(tmp_path, contents)
    assert _check_config(config_path) == 1
    out = capsys.readouterr().out
    assert "INVALID" in out
    assert "goal_systolic_mmhg" in out


def test_check_config_reports_valid_profile_details(tmp_path, capsys):
    contents = (
        _BASE_CONFIG.format(db_path=str(tmp_path / "readings.db"))
        + "\n[profiles]\nenabled = yes\nnames = Alice, Bob\n"
        "\n[profile.Alice]\nname = Alice Smith\n"
    )
    config_path = _write(tmp_path, contents)
    assert _check_config(config_path) == 0
    out = capsys.readouterr().out
    assert "details_valid=2/2" in out
