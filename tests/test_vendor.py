import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo._vendor import VENDOR_DIR, add_vendor_to_path


def test_vendor_dir_exists():
    assert VENDOR_DIR.is_dir()
    assert (VENDOR_DIR / "SHA256SUMS").is_file()


def test_vendored_jinja2_is_used():
    add_vendor_to_path()
    import jinja2
    assert Path(jinja2.__file__).resolve().is_relative_to(VENDOR_DIR)


def test_config_uses_stdlib_tomllib_not_tomli(tmp_path):
    # jimemo#bgaw: config.toml is parsed with the stdlib tomllib (the
    # Python floor, 3.13.6, always ships it) and the vendored tomli is
    # gone. config.py's `import tomllib` is function-local inside
    # load_config(), so there is no jimemo.config.tomllib module
    # attribute to inspect -- instead load a real config file and prove
    # the stdlib module did the parsing while tomli never loads.
    assert "tomli" not in sys.modules
    sys.modules.pop("tomllib", None)

    import jimemo.config

    cfg = tmp_path / "config.toml"
    cfg.write_text('[publish]\nbackend = "command"\ncommand = "notes-publish"\n')
    assert jimemo.config.load_config(cfg).publish.command == "notes-publish"

    toml_mod = sys.modules["tomllib"]
    assert toml_mod.__name__ == "tomllib"
    assert not Path(toml_mod.__file__).resolve().is_relative_to(VENDOR_DIR)
    assert "tomli" not in sys.modules


def test_add_vendor_is_idempotent():
    add_vendor_to_path()
    add_vendor_to_path()
    assert sys.path.count(str(VENDOR_DIR)) == 1
