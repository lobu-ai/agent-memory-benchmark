# Website — Agent Memory Benchmark leaderboard

A static, ClickBench-style leaderboard. Plain HTML + CSS + vanilla JS, no
framework, no build step, no server at runtime. It reads a single committed
`public/data.json` file and renders one sortable leaderboard per dataset.

## Layout

```
website/
  build-data.mjs        # reads ../results/*.json, writes public/data.json
  public/
    index.html          # the page
    style.css           # styles (no CDN, fully offline)
    app.js              # loads data.json, renders the table
    data.json           # generated artifact (committed)
  README.md             # this file
```

## Regenerate the data

The site renders from `public/data.json`, which is built from the report files
in the repo's `results/` directory. After a new benchmark run, regenerate it:

```bash
# from the repo root
bun website/build-data.mjs
# or
node website/build-data.mjs
```

This scans `results/*.json`, extracts the leaderboard summary for each system,
groups runs by `suiteId` (marking the latest run per suite), and writes
`website/public/data.json`. It is defensive: malformed or partial reports are
skipped with a warning, and missing metric fields render as `—`.

> Note: `results/*.json` is gitignored, so `public/data.json` is the committed
> source of truth the site ships. Regenerate and commit it when results change.

## Serve / preview locally

No server is required to deploy, but to preview locally you need a static file
server (browsers block `fetch` of `data.json` over `file://`):

```bash
cd website/public
python3 -m http.server 8000
# then open http://localhost:8000
```

## Deploy

Publish the contents of `website/public/` to any static host:

- **GitHub Pages** — point Pages at `website/public/` (or a workflow that runs
  `node website/build-data.mjs` then deploys the folder).
- **Cloudflare Pages** — build command `node website/build-data.mjs`, output
  directory `website/public`.

Because everything is static and dependency-free, the folder also works when
served from any CDN or copied verbatim.

## What it shows

- Dataset tabs (one per `suiteId`).
- A sortable leaderboard: System, Answer %, Retrieval %, Citation %, Avg
  latency, Avg context tokens. Click any column header to sort; the best cell
  per column is highlighted. Default sort is Answer % descending.
- A methodology blurb, a reproduce command, and a footer with the generated-at
  timestamp and answerer model.
- A graceful "no results yet" state when `results/` is empty.

The `GitHub` link in the header is a placeholder (`https://github.com/`) — point
`#gh-link` in `index.html` at the real repo when publishing.
