import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import jimemo


def test_version_is_semver_string():
    parts = jimemo.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_version_carries_the_script_free_chart_fallback():
    # 0.0.4 is the first version whose chart pages carry the script-free
    # fallback table (jimemo#s3e6); readers detect it by
    # <details class="jm-chart-data"> and may require at least this.
    assert tuple(int(p) for p in jimemo.__version__.split(".")) >= (0, 0, 4)
