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


def _newest_released_heading(text):
    # The first "## " heading that is not "Unreleased", e.g. "## 0.0.4 - 2026-09-30".
    import re

    for line in text.splitlines():
        m = re.match(r"^## \[?([^\]\s]+)\]?", line)
        if m and m.group(1).lower() != "unreleased":
            return m.group(1)
    return None


def test_changelog_newest_release_matches_version():
    # A commit that changes __version__ adds its CHANGELOG.md section in the
    # same commit (AGENTS.md, "Releasing"); this keeps the two from drifting.
    changelog = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    assert changelog.is_file(), "CHANGELOG.md is missing at the repo root"
    assert _newest_released_heading(changelog.read_text(encoding="utf-8")) == jimemo.__version__


def test_newest_released_heading_skips_unreleased():
    text = "# Changelog\n\n## Unreleased\n\n- x\n\n## 0.0.9 - 2026-01-01\n\n## 0.0.8\n"
    assert _newest_released_heading(text) == "0.0.9"
