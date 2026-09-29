#!/usr/bin/env bash
# Phone-reader compatibility screenshots (jimemo#s3e6).
#
#   tests/fixtures/phone-reader/shots.sh OUT_DIR
#
# Renders the chart-dashboard sample and the synthetic fixtures in this
# directory, checks each page with `jimemo check`, and screenshots every
# page at a 390px mobile viewport in four ways: scripts on, scripts
# disabled, scripts blocked by a CSP `script-src 'none'` (a copy of the
# page with that meta tag), and 200% text zoom. Writes PNGs, the rendered
# pages and OUT_DIR/manifest.txt (jimemo version, SHA-256 per page, one
# JSON line per screenshot). Fails if any page requests a non-file URL or
# is wider than the viewport. Writes nothing into the repo.
#
# Needs Node >= 22 and a local Chromium-family browser: $CHROME, else
# Google Chrome's default macOS path.
set -euo pipefail

out=${1:?usage: shots.sh OUT_DIR}
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
created=0
[ -d "$out" ] || { mkdir -p "$out"; created=1; }
out=$(cd "$out" && pwd -P)
case "$out/" in
  "$(cd "$repo" && pwd -P)/"*)
    [ "$created" = 1 ] && rmdir "$out"
    echo "OUT_DIR $out is inside the repo; pick a directory outside it" >&2
    exit 2 ;;
esac
chrome=${CHROME:-"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}
[ -x "$chrome" ] || { echo "no browser at $chrome (set CHROME)" >&2; exit 1; }
manifest="$out/manifest.txt"

# The interpreter install.sh would bind (>= 3.13.6); override with
# JIMEMO_PYTHON. A stock macOS python3 is too old for jimemo.
py=${JIMEMO_PYTHON:-python3}
jimemo() { "$py" "$repo/jimemo" "$@"; }

{
  echo "jimemo: $(jimemo --version)"
  echo "commit: $(git -C "$repo" rev-parse HEAD)"
} > "$manifest"

jimemo render chart-dashboard "$repo/templates/chart-dashboard/sample/content.yaml" \
  -o "$out/chart-dashboard.html" >/dev/null
jimemo render briefing "$here/briefing-ja.md" -o "$out/briefing-ja.html" >/dev/null

fail=0
for page in chart-dashboard briefing-ja; do
  html="$out/$page.html"
  jimemo check "$html" >/dev/null
  echo "sha256 $page.html $(shasum -a 256 "$html" | cut -d' ' -f1)" >> "$manifest"
  python3 - "$html" "$out/$page-csp.html" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
html = open(src, encoding="utf-8").read()
meta = '<meta http-equiv="Content-Security-Policy" content="script-src \'none\'">'
if html.count("<head>") != 1:
    sys.exit(f"{src}: expected exactly one <head> to carry the CSP meta")
open(dst, "w", encoding="utf-8").write(html.replace("<head>", "<head>\n" + meta, 1))
PY
  for mode in on off zoom csp; do
    file=$html; run=$mode
    if [ "$mode" = csp ]; then file="$out/$page-csp.html"; run=on; fi
    line=$(node "$here/shot.mjs" "$chrome" "$file" "$out/$page-$mode.png" "$run" 390)
    echo "shot $page $mode $line" >> "$manifest"
    python3 -c 'import json,sys; r=json.loads(sys.argv[1]); sys.exit(1 if r["requests"] or r["width"]!=390 or r["scrollWidth"]>390 else 0)' "$line" \
      || { echo "FAIL $page $mode: $line" >&2; fail=1; }
  done
done
echo "wrote $out"
exit $fail
