import pytest

from undercutter.config import ConfigError, dollars_to_cents, load_settings, parse_floors


@pytest.mark.parametrize("text,cents", [("4.50", 450), ("$4.5", 450), ("0.01", 1), ("10", 1000)])
def test_dollars(text, cents):
    assert dollars_to_cents(text) == cents


@pytest.mark.parametrize("text", ["0", "-1", "abc", "1.005", "NaN", "Infinity", ""])
def test_bad_dollars(text):
    with pytest.raises(ConfigError):
        dollars_to_cents(text)


def test_parse_floors():
    text = "# comment\nharvester = 4.50\nCorrupt = skip  # mine\nnik's scythe=$2, other = off\n"
    assert parse_floors(text) == {"harvester": 450, "corrupt": None, "nik's scythe": 200, "other": None}


@pytest.mark.parametrize("text", ["harvester", "harvester =", "= 4", "harvester = free"])
def test_bad_floors(text):
    with pytest.raises(ConfigError):
        parse_floors(text)


def test_defaults(tmp_path):
    s = load_settings({}, tmp_path)
    assert s.dry_run is False
    assert s.rules == {}                # nothing is touched until you set a floor
    assert s.global_floor is None
    assert s.always_online is False


def test_invalid_values_stop_startup(tmp_path):
    for env in ({"DRY_RUN": "maybe"}, {"POLL_SECONDS": "5"}, {"UNDERCUT_STEP_CENTS": "0"},
                {"GLOBAL_FLOOR_USD": "0"}, {"GLOBAL_MAX_DROP_PCT": "100"}, {"FLOORS": "x=abc"}):
        with pytest.raises(ConfigError):
            load_settings(env, tmp_path)


def test_limits(tmp_path):
    (tmp_path / "floors.txt").write_text("harvester = 4.50\nluger = 1\n")
    s = load_settings({"FLOORS": "luger = 2, corrupt = skip", "GLOBAL_FLOOR_USD": "1.25"}, tmp_path)
    assert s.limit_for("harvester", "Harvester").floor == 450
    assert s.limit_for("harvester", "Harvester").is_global is False
    assert s.limit_for("luger-x", "Luger").floor == 200                       # env wins, matches by name
    assert s.limit_for("corrupt", "Corrupt").excluded is True
    other = s.limit_for("gemstone", "Gemstone")
    assert (other.floor, other.is_global) == (125, True)


def test_no_floor_without_global(tmp_path):
    limit = load_settings({}, tmp_path).limit_for("harvester", "Harvester")
    assert limit.floor is None and not limit.excluded


def test_dotenv_and_quotes(tmp_path):
    (tmp_path / ".env").write_text("BHOPP_TOKEN=\"abc.def\"\nDRY_RUN=false\n# x=1\n")
    from undercutter.config import load_dotenv
    s = load_settings(load_dotenv(tmp_path / ".env"), tmp_path)
    assert s.token == "abc.def" and s.dry_run is False


def test_state_file_location(tmp_path):
    from pathlib import Path
    assert load_settings({}, tmp_path).state_file == str(tmp_path / "state.json")
    volume = {"RAILWAY_VOLUME_MOUNT_PATH": "/data"}
    assert load_settings(volume, tmp_path).state_file == str(Path("/data") / "state.json")
    assert load_settings({"STATE_FILE": "/x/y.json", **volume}, tmp_path).state_file == "/x/y.json"
