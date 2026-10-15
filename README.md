# Stayup

[![CI](https://github.com/stayup-app/stayup-cmd-podcast/actions/workflows/ci.yml/badge.svg)](https://github.com/stayup-app/stayup-cmd-podcast/actions/workflows/ci.yml)
[![Daily Podcast fetch](https://github.com/stayup-app/stayup-cmd-podcast/actions/workflows/daily.yml/badge.svg)](https://github.com/stayup-app/stayup-cmd-podcast/actions/workflows/daily.yml)

**Website:** https://stayup-ui.vercel.app

Monitors podcast feeds and stores their latest episodes. For each tracked feed the script fetches the most recent entries with feedparser and keeps those that carry an audio enclosure (an episode). A new episode is only stored when its GUID has changed since the last run, up to `max_entries` (default 5) per run; on the first run only the latest episode is stored. The script never touches a database directly, it only calls [stayup-api](https://github.com/stayup-app/stayup-api)'s `/connector-api/podcast/*` endpoints. Old content is pruned by `stayup-api` itself — retention is an instance-wide setting an admin manages, not this connector's job.

## Requirements

- Python 3.13, or [Docker](https://www.docker.com/)
- A `stayup-api` instance (see [stayup-api](https://github.com/stayup-app/stayup-api))
- An API key for the `podcast` provider, created from that instance's admin panel (Connector keys → New key, provider `podcast`). The key is shown once — copy it right away.

## Setup

```bash
git clone https://github.com/stayup-app/stayup-cmd-podcast.git
cd stayup-cmd-podcast
cp .env.example .env
```

Open `.env` and set `STAYUP_API_URL` (your `stayup-api` instance) and `STAYUP_API_KEY` (the key you created for `podcast`).

> **Note:** the provider registers itself automatically on every run — nothing to create by hand beyond the key. The apps render it with the template bundled in them (`src/lib/providers.ts`), so use a version of the apps that knows `podcast`.

## Usage

**Track a podcast** (its RSS feed URL):
```bash
docker compose run --rm fetch_podcast --add https://feeds.example.com/my-show.xml
```

**Run the script manually:**
```bash
docker compose run --rm fetch_podcast
```

Without Docker:
```bash
pip install -r requirements.txt
STAYUP_API_URL=... STAYUP_API_KEY=... python fetch_podcast.py
```

## Automation

The script runs automatically every night at midnight UTC via GitHub Actions.

To enable it on your fork, add `STAYUP_API_URL` and `STAYUP_API_KEY` secrets in:
**Settings → Secrets and variables → Actions → New repository secret**

You can also trigger the workflow manually from the **Actions → Daily Podcast fetch → Run workflow** tab.

## Development

**Install the pre-commit hook** (runs linter + tests before every commit):
```bash
cp scripts/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

**Run tests** (no external dependencies — `stayup-api` and network calls are mocked):
```bash
docker compose run --rm test
```

**Check linting:**
```bash
docker compose run --rm --entrypoint="" test sh -c "ruff check . && black --check ."
```

**Auto-format code:**
```bash
docker run --rm --entrypoint="" -v $(pwd):/app -w /app stayup-test black .
```

## What gets stored

Each stored episode is a JSON `content` blob `{"title", "link", "summary", "audio", "image", "duration"}` — `summary` is the show notes reduced to plain text (the apps render it as text), `audio` the enclosure's URL (the web and desktop apps play it, the mobile app opens it), `image` the episode cover or, failing that, the show's, `duration` whatever the feed declares (`itunes:duration`). It is keyed by GUID (`version`, falls back to the audio URL). The show's `<title>` is kept in the tracked feed's config so the apps can label it — see `stayup-api`'s `connector-api` docs for the full contract.

## Project structure

```
stayup-cmd-podcast/
├── fetch_podcast.py   # Main script
├── tests/
│   └── test_unit.py   # Tests — stayup-api and network calls are mocked
├── .env.example       # Configuration template
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml     # Ruff + Black configuration
```
