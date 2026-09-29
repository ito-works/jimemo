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
chrome=${CHROME:-"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}
[ -x "$chrome" ] || { echo "no browser at $chrome (set CHROME)" >&2; exit 1; }
mkdir -p "$out"
out=$(cd "$out" && pwd)
manifest="$out/manifest.txt"

jimemo() { python3 "$repo/jimemo" "$@"; }

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
