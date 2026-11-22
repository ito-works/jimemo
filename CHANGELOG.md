# Changelog

What changed for someone who runs `git pull` on a jimemo clone: what you
will see, and anything you have to do. Newest first. `jimemo --version`
prints the version you have; each released version is tagged `vX.Y.Z`.

## Unreleased

## 0.0.5 - 2026-11-22

- Tooltips: a `title` on an inline element or a markdown image in your
  content now shows as a themed box on hover and on tap. Pages without a
  `title` are unchanged.
- Japanese text: a soft line break between two Japanese characters no
  longer turns into a space in the rendered page.
- Markdown tables scroll sideways on a phone instead of widening the page,
  and print whole.
- A chart page whose Chart.js cannot run shows only its data table, never
  an empty canvas.
- `~/.jimemo/config.toml` is read with the standard library's `tomllib`;
  the vendored `tomli` is gone. Nothing to do.
- Publishing with the `cloudflare` backend: `jimemo publish setup` now
  creates the `<project>-tombstones` KV namespace and binds it as
  `TOMBSTONES` itself, so the dashboard steps are no longer needed.
  `JIMEMO_WRANGLER=/path/to/wrangler` picks the Wrangler to run. A deploy
  that fails after the upload keeps the page instead of reporting it lost.
- `jimemo render --lang TAG` sets the page's language, for example
  `--lang ja`. Without it, pages stay `en`.
- Chart axis labels, legend text, grid lines and radar tick labels follow
  the page's light or dark mode. Re-render an older page to get it.
- A theme from `import-design` stays light when the reader's system is in
  dark mode, and its positive and negative badge colours meet WCAG AA
  contrast against the page background. Re-run `import-design` to update a
  theme you imported before.
- `jimemo doctor` warns when the `cloudflare` backend's installed
  middleware or `_headers` differs from this version's copy; run
  `jimemo publish setup --assets-only` to refresh them.
- `install.sh` links the Codex skill only when Codex is installed, and no
  longer creates `~/.codex` on a Mac without it.

## 0.0.4 - 2026-09-30

- You need Python 3.13.6 or newer, a final release. Install one and re-run
  `./install.sh` (or `./install.sh --python /path/to/python3`). The
  installed `jimemo` is now bound to that interpreter, and `jimemo doctor`
  shows which one.
- Every chart page carries a plain data table under the chart, so a reader
  with scripts off still sees the labels and values. Re-render an older
  page to get it.
- Charts follow the page's light or dark mode.
- Diagrams: `jimemo render --figure NAME=file.svg` fills a
  `[[DIAGRAM:NAME]]` paragraph, and a markdown image of a local `.svg` is
  inlined. Both pass through the SVG sanitizer, which names on stderr what
  it removed.
- `import-design --embed-fonts` embeds only the font faces the theme uses.
- `jimemo check` is stricter: some hand-edited pages that passed before
  now fail, for markup a browser would load from outside the page.

## 0.0.3 - 2026-09-15

- The row of big-number stat tiles is gone from every template. A content
  file that still has a `stats:` key now fails with "unknown slot"; move
  those numbers into prose or a table.
- New template: `research-bible`, for long deep-research output.
- An `<a id="...">` in markdown content keeps its `id`, so links to it
  from elsewhere in the page work.
- Publishing with the `cloudflare` backend needs curl 8.3 or newer and the
  API token in the environment for setup and every deploy, and refuses
  to deploy while the Pages project is fail-open. If you publish from more
  than one machine, make the backend's state directory a git repo with an
  `origin` remote; without one, jimemo skips the sync, and a deploy from
  the second machine removes the pages the first one published. Steps:
  [docs/publish-setup.md](docs/publish-setup.md#the-wipe-by-deploy-hazard-and-the-git-fix-read-this).

## 0.0.2 - 2026-07-15

- `jimemo render --pdf`, `-o out.pdf` and `jimemo pdf` write a PDF through
  a locally installed Chrome-family browser; `jimemo check` verifies a page
  is still self-contained after you edit it.
- `jimemo scaffold <template>` writes a fill-in content file.
- New template: `ops-board`, for operational status boards.
- `docs/diagrams.md` explains how to draw an inline-SVG diagram that
  follows the theme.
- `jimemo publish setup` for Cloudflare can be run again safely.

## 0.0.1 - 2026-07-06

First version: `render`, `suggest` and `render auto` turn a template and a
content file into one self-contained HTML page; `publish` posts it to an
unlisted link; `import-design` turns a Claude Design export into a theme;
`install.sh` registers the CLI and the agent skill with each harness it
finds.
