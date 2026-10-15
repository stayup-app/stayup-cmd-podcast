#!/usr/bin/env python3
"""
Stayup — monitors podcast feeds and stores the latest episodes via stayup-api.

For each tracked repository of type 'podcast', the script fetches the most
recent episodes of the feed using feedparser. An episode is a feed entry with an
audio enclosure. On first run the latest episode is stored. On subsequent runs
all new episodes (up to config["max_entries"], default 5) are stored until the
already-known episode is reached.

Talks to stayup-api over HTTP (STAYUP_API_URL + STAYUP_API_KEY) — it never
touches a database directly. See stayup-api/docs/self-hosting-and-providers.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from html import unescape

import feedparser
import requests

PROVIDER_TYPE = "podcast"

# Display name of the provider in the apps (fallback: capitalized table name).
DISPLAY_NAME = "Podcast"

# Where this connector ranks among the others in the sidebar.
SORT_ORDER = 70

# The stayup-api instance to talk to, and the key that authenticates this
# connector for the 'podcast' provider — obtained from that instance's admin
# (see stayup-api/docs/self-hosting-and-providers.md).
API_URL = os.environ.get("STAYUP_API_URL", "http://localhost:3000").rstrip("/")
API_KEY = os.environ.get("STAYUP_API_KEY")

DEFAULT_MAX_ENTRIES = 5

_PARAGRAPH_END_RE = re.compile(r"<\s*/p\s*>", re.IGNORECASE)
_LINE_BREAK_RE = re.compile(r"<\s*br\s*/?\s*>|<\s*/(?:li|div)\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------------------
# stayup-api client
# ---------------------------------------------------------------------------


def api_request(method: str, path: str, **kwargs) -> dict | None:
    """Call one of stayup-api's /connector-api/podcast/* endpoints.

    Raises RuntimeError if STAYUP_API_KEY isn't set, or requests.HTTPError on
    a non-2xx response (via raise_for_status — 403 means this key isn't
    scoped to 'podcast', 401 means it's missing or revoked).
    """
    if not API_KEY:
        raise RuntimeError("STAYUP_API_KEY is not set.")
    url = f"{API_URL}/connector-api/{PROVIDER_TYPE}{path}"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


def register_provider() -> None:
    """Self-declaration at startup — display name and sort order."""
    api_request(
        "POST",
        "/register",
        json={
            "displayName": DISPLAY_NAME,
            "sortOrder": SORT_ORDER,
        },
    )


def add_source(url: str) -> int:
    """Track a new feed URL and return its id."""
    result = api_request("POST", "/sources", json={"url": url})
    return result["id"]


def get_sources() -> list[tuple[int, str, dict]]:
    """Return all tracked sources as (id, url, config) tuples."""
    result = api_request("GET", "/sources")
    return [(s["id"], s["url"], s.get("config") or {}) for s in result["sources"]]


def get_latest_version(repository_id: int) -> str | None:
    """Return the GUID of the most recently stored episode, or None on first run."""
    result = api_request("GET", f"/sources/{repository_id}/state")
    return result["version"]


def save_episodes(repository_id: int, episodes: list[dict], executed_at: datetime) -> None:
    """Persist a batch of episodes in a single call. No-op on an empty batch."""
    if not episodes:
        return
    items = [
        {
            "repositoryId": repository_id,
            "version": episode["version"],
            "content": json.dumps(
                {
                    "title": episode["title"],
                    "link": episode["link"],
                    "summary": episode["summary"],
                    "audio": episode["audio"],
                    "image": episode["image"],
                    "duration": episode["duration"],
                },
                ensure_ascii=False,
            ),
            "datetime": episode["published"].isoformat() if episode["published"] else None,
            "executedAt": executed_at.isoformat(),
            "success": True,
        }
        for episode in episodes
    ]
    api_request("POST", "/items", json={"items": items})


def save_error(repository_id: int | None, error: str, executed_at: datetime) -> None:
    """Persist a retrieval error."""
    api_request(
        "POST",
        "/errors",
        json={"repositoryId": repository_id, "error": error, "executedAt": executed_at.isoformat()},
    )


def save_feed_title(repository_id: int, title: str) -> None:
    """Store the podcast's <title> in repository.config so the apps can label the
    flux with it. A merge, not a replace — see mergeSourceConfig."""
    api_request("PATCH", f"/sources/{repository_id}/config", json={"config": {"title": title}})


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------


def to_text(html: str | None) -> str | None:
    """Reduce show notes (HTML) to plain text — the apps render them as text."""
    if not html:
        return None
    text = _LINE_BREAK_RE.sub("\n", _PARAGRAPH_END_RE.sub("\n\n", html))
    text = unescape(_TAG_RE.sub("", text))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text or None


def _audio_url(raw_entry) -> str | None:
    """The first audio enclosure of an entry."""
    for enclosure in raw_entry.get("enclosures") or []:
        href = enclosure.get("href")
        if href and (enclosure.get("type") or "").startswith("audio"):
            return href
    return None


def _parse_entry(raw_entry, feed_image: str | None) -> dict | None:
    """Extract an episode from a feedparser entry, or None if it has no audio."""
    audio = _audio_url(raw_entry)
    if not audio:
        return None

    published: datetime | None = None
    published_parsed = raw_entry.get("published_parsed")
    if published_parsed:
        try:
            published = datetime(*published_parsed[:6], tzinfo=UTC)
        except Exception:
            pass

    return {
        "version": raw_entry.get("id") or audio,
        "title": raw_entry.get("title"),
        "link": raw_entry.get("link"),
        "summary": to_text(raw_entry.get("summary")),
        "audio": audio,
        "image": (raw_entry.get("image") or {}).get("href") or feed_image,
        "duration": raw_entry.get("itunes_duration"),
        "published": published,
    }


def fetch_feed(feed_url: str, max_entries: int = 5) -> tuple[str | None, list[dict]]:
    """Return the podcast's title and its most recent episodes.

    Episodes are dicts (newest first) with keys: version, title, link, summary,
    audio, image, duration, published. Entries without an audio enclosure are not
    episodes and are left out. Title is ``None`` when the feed carries no
    ``<title>``. feedparser.parse() never raises — it returns an empty feed on
    network errors.
    """
    feed = feedparser.parse(feed_url, agent="stayup-podcast/1.0")
    if feed.bozo and not feed.entries:
        status = getattr(feed, "status", "no status")
        href = getattr(feed, "href", feed_url)
        raise RuntimeError(f"Feed parse error (HTTP {status}, url={href}): {feed.bozo_exception}")
    title = (feed.feed.get("title") or "").strip() or None
    feed_image = (feed.feed.get("image") or {}).get("href")
    episodes = [_parse_entry(e, feed_image) for e in feed.entries]
    return title, [e for e in episodes if e][:max_entries]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def process_repository(repository_id: int, repository_url: str, executed_at: datetime, config: dict) -> None:
    """Fetch new episodes for one podcast and persist them.

    - If no previous episode exists, only the latest one is stored.
    - Otherwise, episodes are stored from newest to oldest until the already-known
      version is reached, up to config["max_entries"] (default 5) episodes.
    - Any exception is caught, logged via the API, and printed to stderr.
    """
    max_entries = config.get("max_entries", DEFAULT_MAX_ENTRIES)
    try:
        feed_title, episodes = fetch_feed(repository_url, max_entries=max_entries)
        if not episodes:
            raise RuntimeError("No episode found.")

        # Keeps `repository.config.title` up to date for the flux label (the
        # template falls back to the URL's domain when it is absent).
        if feed_title and feed_title != config.get("title"):
            save_feed_title(repository_id, feed_title)

        last_version = get_latest_version(repository_id)

        if last_version is None:
            # First run: store only the most recent episode.
            save_episodes(repository_id, [episodes[0]], executed_at)
            return

        new_episodes = []
        for episode in episodes:
            if episode["version"] == last_version:
                break
            new_episodes.append(episode)
        save_episodes(repository_id, new_episodes, executed_at)

    except Exception as e:
        save_error(repository_id, str(e), executed_at)
        print(f"[{repository_url}] Error: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor podcast feeds and store latest episodes.")
    parser.add_argument("--add", metavar="URL", help="Add a podcast feed to track and exit.")
    args = parser.parse_args()

    register_provider()

    if args.add:
        add_source(args.add)
        print(f"Repository added: {args.add}")
        return

    executed_at = datetime.now(tz=UTC)
    sources = get_sources()

    if not sources:
        print("No repositories tracked. Use --add <url> to add one.")
        return

    for repository_id, repository_url, config in sources:
        process_repository(repository_id, repository_url, executed_at, config)


if __name__ == "__main__":
    main()
