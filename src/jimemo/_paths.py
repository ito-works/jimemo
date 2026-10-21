"""Single source of truth for repo-root-relative paths."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Chart.js is browser JS, never imported by Python -- it lives under its own
# vendor dir with its own SHA256SUMS, checked by doctor but never added to
# sys.path (see _vendor.py, which is the Python-importable vendor gate).
CHARTS_VENDOR_DIR = REPO_ROOT / "charts" / "vendor"
CHARTJS_BUNDLE = CHARTS_VENDOR_DIR / "chartjs" / "chart.umd.min.js"

# The renderer-built tooltip (tooltip.py): jimemo's own fixed script and
# stylesheet, emitted only when content carries a title.
TOOLTIP_JS = REPO_ROOT / "toolkit" / "tooltip.js"
TOOLTIP_CSS = REPO_ROOT / "toolkit" / "tooltip.css"
