# Phone-reader fixtures

Synthetic pages for checking jimemo output at phone width, with scripts
running and without. Nothing here describes real people or data.

- `briefing-ja.md` + `flow.svg` — briefing template: Japanese and
  English prose, a markdown table wider than a phone, an inline SVG.
- the chart-dashboard sample (`templates/chart-dashboard/sample/`) —
  two charts and their script-free data tables.

```
tests/fixtures/phone-reader/shots.sh /path/to/out
```

renders both pages, runs `jimemo check` on each, and takes a full-page
screenshot of each at a 390px mobile viewport (DevTools device
emulation; headless Chrome cannot make a window narrower than 500px) in
four modes:

| mode | how |
| --- | --- |
| `on` | scripts run |
| `off` | script execution disabled |
| `csp` | a copy of the page with `<meta http-equiv="Content-Security-Policy" content="script-src 'none'">` |
| `zoom` | scripts run, root font size set to 200% from DevTools (an approximation of a phone's text-size setting) |

`out/manifest.txt` records the jimemo version, the commit, the SHA-256
of each rendered page and one JSON line per screenshot. The script fails
when a page requests anything other than its own file, or when the page
is wider than 390px (a phone would zoom it out). It needs Node 22 or
later and a local Chromium-family browser (`$CHROME`, else Google
Chrome's macOS path), and it starts the browser with a throwaway profile.

Observed on 2026-09-30 (jimemo 0.0.4):

- Fixed: a wide markdown table widened the whole page to 722px, so the
  phone zoomed out and all text shrank. Prose tables now scroll inside
  their own box below 40rem.
- Not fixed, out of scope: a line break inside a Japanese paragraph in
  the markdown source renders as a space between two Japanese
  characters. Write each Japanese paragraph on one source line until
  the renderer handles this.
