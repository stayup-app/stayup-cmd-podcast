"""Unit tests — no external dependencies. stayup-api itself is mocked
(unittest.mock.patch on `requests.request`); its actual behavior is covered
by stayup-api's own test suite. Feeds are parsed for real by feedparser, from
inline XML, so no network is needed."""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fetch_podcast import (
    add_source,
    fetch_feed,
    get_latest_version,
    get_sources,
    main,
    process_repository,
    register_provider,
    save_episodes,
    save_error,
    save_feed_title,
    to_text,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def mock_response(json_body=None, status=200):
    response = MagicMock()
    response.status_code = status
    response.content = b"{}" if json_body is not None else b""
    response.json.return_value = json_body
    response.raise_for_status.return_value = None
    return response


def episode(version="ep-1", **over):
    base = {
        "version": version,
        "title": f"Episode {version}",
        "link": f"https://show.example/{version}",
        "summary": "Notes",
        "audio": f"https://cdn.example/{version}.mp3",
        "image": "https://cdn.example/cover.jpg",
        "duration": "42:10",
        "published": datetime(2024, 6, 15, tzinfo=UTC),
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# stayup-api client
# ---------------------------------------------------------------------------


@patch("fetch_podcast.API_KEY", "test-key")
class TestRegisterProvider:
    @patch("fetch_podcast.requests.request")
    def test_posts_display_name_and_sort_order(self, mock_request):
        mock_request.return_value = mock_response()
        register_provider()
        method, url = mock_request.call_args[0]
        assert method == "POST"
        assert url.endswith("/connector-api/podcast/register")
        body = mock_request.call_args.kwargs["json"]
        assert body["displayName"] == "Podcast"
        assert body["sortOrder"] == 70

    @patch("fetch_podcast.requests.request")
    def test_sends_the_bearer_token(self, mock_request):
        mock_request.return_value = mock_response()
        register_provider()
        assert mock_request.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"


class TestApiRequestWithoutKey:
    @patch("fetch_podcast.API_KEY", None)
    def test_raises_when_no_api_key_is_configured(self):
        with pytest.raises(RuntimeError, match="STAYUP_API_KEY"):
            register_provider()


@patch("fetch_podcast.API_KEY", "test-key")
class TestSources:
    @patch("fetch_podcast.requests.request")
    def test_add_source_posts_the_url_and_returns_the_id(self, mock_request):
        mock_request.return_value = mock_response({"id": 7})
        assert add_source("https://show.example/feed.xml") == 7
        assert mock_request.call_args[0][1].endswith("/connector-api/podcast/sources")
        assert mock_request.call_args.kwargs["json"] == {"url": "https://show.example/feed.xml"}

    @patch("fetch_podcast.requests.request")
    def test_get_sources_returns_id_url_config_tuples(self, mock_request):
        mock_request.return_value = mock_response(
            {"sources": [{"id": 1, "url": "https://a.dev/feed", "config": {"max_entries": 3}}, {"id": 2, "url": "u"}]}
        )
        assert get_sources() == [(1, "https://a.dev/feed", {"max_entries": 3}), (2, "u", {})]


@patch("fetch_podcast.API_KEY", "test-key")
class TestGetLatestVersion:
    @patch("fetch_podcast.requests.request")
    def test_returns_none_on_first_run(self, mock_request):
        mock_request.return_value = mock_response({"version": None})
        assert get_latest_version(1) is None

    @patch("fetch_podcast.requests.request")
    def test_returns_the_version(self, mock_request):
        mock_request.return_value = mock_response({"version": "ep-9"})
        assert get_latest_version(1) == "ep-9"
        assert mock_request.call_args[0][1].endswith("/connector-api/podcast/sources/1/state")


@patch("fetch_podcast.API_KEY", "test-key")
class TestSaveEpisodes:
    @patch("fetch_podcast.requests.request")
    def test_batches_episodes_into_one_call(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        save_episodes(1, [episode("a"), episode("b")], NOW)
        assert mock_request.call_count == 1
        items = mock_request.call_args.kwargs["json"]["items"]
        assert [i["version"] for i in items] == ["a", "b"]
        assert items[0]["repositoryId"] == 1
        assert items[0]["success"] is True
        assert items[0]["datetime"] == "2024-06-15T00:00:00+00:00"
        assert json.loads(items[0]["content"]) == {
            "title": "Episode a",
            "link": "https://show.example/a",
            "summary": "Notes",
            "audio": "https://cdn.example/a.mp3",
            "image": "https://cdn.example/cover.jpg",
            "duration": "42:10",
        }

    @patch("fetch_podcast.requests.request")
    def test_does_nothing_on_an_empty_batch(self, mock_request):
        save_episodes(1, [], NOW)
        mock_request.assert_not_called()

    @patch("fetch_podcast.requests.request")
    def test_stores_a_null_date_when_the_feed_gave_none(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        save_episodes(1, [episode(published=None)], NOW)
        assert mock_request.call_args.kwargs["json"]["items"][0]["datetime"] is None


@patch("fetch_podcast.API_KEY", "test-key")
class TestSaveErrorAndTitle:
    @patch("fetch_podcast.requests.request")
    def test_save_error_posts_the_error(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        save_error(5, "boom", NOW)
        assert mock_request.call_args.kwargs["json"] == {
            "repositoryId": 5,
            "error": "boom",
            "executedAt": NOW.isoformat(),
        }

    @patch("fetch_podcast.requests.request")
    def test_save_error_accepts_a_missing_repository(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        save_error(None, "boom", NOW)
        assert mock_request.call_args.kwargs["json"]["repositoryId"] is None

    @patch("fetch_podcast.requests.request")
    def test_save_feed_title_patches_the_config(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        save_feed_title(7, "The Show")
        method, url = mock_request.call_args[0]
        assert method == "PATCH"
        assert url.endswith("/connector-api/podcast/sources/7/config")
        assert mock_request.call_args.kwargs["json"] == {"config": {"title": "The Show"}}


# ---------------------------------------------------------------------------
# to_text
# ---------------------------------------------------------------------------


class TestToText:
    def test_strips_tags_and_keeps_paragraph_breaks(self):
        assert to_text("<p>First &amp; best</p><p>Second<br/>line</p>") == "First & best\n\nSecond\nline"

    def test_collapses_runs_of_blank_lines_and_spaces(self):
        assert to_text("<p>a</p>\n\n\n<p>b   c</p>") == "a\n\nb c"

    def test_is_none_for_nothing_worth_showing(self):
        assert to_text(None) is None
        assert to_text("") is None
        assert to_text("<p>  </p>") is None


# ---------------------------------------------------------------------------
# fetch_feed — real feedparser, inline XML
# ---------------------------------------------------------------------------

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>  The Show  </title>
    <itunes:image href="https://cdn.example/show.jpg"/>
    <item>
      <title>Episode 3</title>
      <link>https://show.example/3</link>
      <guid>ep-3</guid>
      <pubDate>Sat, 15 Jun 2024 10:00:00 GMT</pubDate>
      <description><![CDATA[<p>Notes &amp; links</p>]]></description>
      <enclosure url="https://cdn.example/3.mp3" type="audio/mpeg" length="1"/>
      <itunes:image href="https://cdn.example/3.jpg"/>
      <itunes:duration>01:02:03</itunes:duration>
    </item>
    <item>
      <title>Blog post, no audio</title>
      <link>https://show.example/blog</link>
      <guid>blog-1</guid>
    </item>
    <item>
      <title>Episode 2</title>
      <link>https://show.example/2</link>
      <enclosure url="https://cdn.example/2.mp3" type="audio/mpeg" length="1"/>
    </item>
    <item>
      <title>Episode 1</title>
      <guid>ep-1</guid>
      <enclosure url="https://cdn.example/1.mp3" type="audio/mpeg" length="1"/>
    </item>
  </channel>
</rss>"""


class TestFetchFeed:
    def test_returns_the_trimmed_title_and_the_episodes(self):
        title, episodes = fetch_feed(FEED)
        assert title == "The Show"
        first = episodes[0]
        assert first["version"] == "ep-3"
        assert first["title"] == "Episode 3"
        assert first["link"] == "https://show.example/3"
        assert first["summary"] == "Notes & links"
        assert first["audio"] == "https://cdn.example/3.mp3"
        assert first["image"] == "https://cdn.example/3.jpg"
        assert first["duration"] == "01:02:03"
        assert first["published"] == datetime(2024, 6, 15, 10, 0, tzinfo=UTC)

    def test_leaves_out_entries_without_audio(self):
        _, episodes = fetch_feed(FEED)
        assert [e["title"] for e in episodes] == ["Episode 3", "Episode 2", "Episode 1"]

    def test_falls_back_to_the_audio_url_as_version_and_the_show_cover_as_image(self):
        _, episodes = fetch_feed(FEED)
        second = episodes[1]
        assert second["version"] == "https://cdn.example/2.mp3"
        assert second["image"] == "https://cdn.example/show.jpg"
        assert second["published"] is None
        assert second["summary"] is None
        assert second["duration"] is None

    def test_limits_to_max_entries(self):
        _, episodes = fetch_feed(FEED, max_entries=2)
        assert len(episodes) == 2

    def test_ignores_non_audio_enclosures(self):
        xml = """<rss version="2.0"><channel><title>x</title><item><title>t</title>
        <enclosure url="https://cdn.example/pic.jpg" type="image/jpeg" length="1"/></item></channel></rss>"""
        _, episodes = fetch_feed(xml)
        assert episodes == []

    def test_title_is_none_when_the_feed_has_no_title(self):
        xml = """<rss version="2.0"><channel><item><title>t</title>
        <enclosure url="https://cdn.example/a.mp3" type="audio/mpeg" length="1"/></item></channel></rss>"""
        title, episodes = fetch_feed(xml)
        assert title is None
        assert len(episodes) == 1

    def test_returns_no_episode_for_an_empty_feed(self):
        _, episodes = fetch_feed('<rss version="2.0"><channel><title>x</title></channel></rss>')
        assert episodes == []

    def test_a_malformed_publication_date_is_not_fatal(self):
        with patch("fetch_podcast.feedparser.parse") as mock_parse:
            entry = MagicMock()
            entry.get.side_effect = lambda k, d=None: {
                "enclosures": [{"href": "https://cdn.example/a.mp3", "type": "audio/mpeg"}],
                "published_parsed": (99999, 1, 1, 0, 0, 0),
            }.get(k, d)
            feed = MagicMock()
            feed.get.side_effect = lambda k, d=None: d
            mock_parse.return_value = MagicMock(bozo=False, entries=[entry], feed=feed)
            _, episodes = fetch_feed("https://show.example/feed.xml")
        assert episodes[0]["published"] is None

    @patch("fetch_podcast.feedparser.parse")
    def test_raises_on_bozo_with_no_entries(self, mock_parse):
        mock_parse.return_value = MagicMock(bozo=True, entries=[], bozo_exception=Exception("bad xml"))
        with pytest.raises(RuntimeError, match="Feed parse error"):
            fetch_feed("https://show.example/feed.xml")


# ---------------------------------------------------------------------------
# process_repository
# ---------------------------------------------------------------------------

URL = "https://show.example/feed.xml"


@patch("fetch_podcast.API_KEY", "test-key")
class TestProcessRepository:
    @patch("fetch_podcast.save_error")
    @patch("fetch_podcast.save_episodes")
    @patch("fetch_podcast.get_latest_version", return_value=None)
    @patch("fetch_podcast.fetch_feed")
    def test_first_run_stores_only_the_latest_episode(self, mock_fetch, _latest, mock_save, mock_error):
        mock_fetch.return_value = (None, [episode("ep-2"), episode("ep-1")])
        process_repository(1, URL, NOW, {})
        _, episodes, _ = mock_save.call_args[0]
        assert [e["version"] for e in episodes] == ["ep-2"]
        mock_error.assert_not_called()

    @patch("fetch_podcast.save_episodes")
    @patch("fetch_podcast.get_latest_version", return_value="ep-1")
    @patch("fetch_podcast.fetch_feed")
    def test_stores_the_new_episodes_until_the_known_one(self, mock_fetch, _latest, mock_save):
        mock_fetch.return_value = (None, [episode("ep-3"), episode("ep-2"), episode("ep-1")])
        process_repository(1, URL, NOW, {})
        _, episodes, _ = mock_save.call_args[0]
        assert [e["version"] for e in episodes] == ["ep-3", "ep-2"]

    @patch("fetch_podcast.save_episodes")
    @patch("fetch_podcast.get_latest_version", return_value="ep-1")
    @patch("fetch_podcast.fetch_feed")
    def test_stores_nothing_when_the_known_episode_is_the_latest(self, mock_fetch, _latest, mock_save):
        mock_fetch.return_value = (None, [episode("ep-1")])
        process_repository(1, URL, NOW, {})
        assert mock_save.call_args[0][1] == []

    @patch("fetch_podcast.save_episodes")
    @patch("fetch_podcast.get_latest_version", return_value="gone")
    @patch("fetch_podcast.fetch_feed")
    def test_stores_everything_when_the_known_episode_left_the_feed(self, mock_fetch, _latest, mock_save):
        mock_fetch.return_value = (None, [episode(f"ep-{i}") for i in range(4)])
        process_repository(1, URL, NOW, {})
        assert len(mock_save.call_args[0][1]) == 4

    @patch("fetch_podcast.save_episodes")
    @patch("fetch_podcast.get_latest_version", return_value=None)
    @patch("fetch_podcast.fetch_feed")
    def test_passes_max_entries_to_the_parser(self, mock_fetch, _latest, _save):
        mock_fetch.return_value = (None, [episode()])
        process_repository(1, URL, NOW, {"max_entries": 2})
        assert mock_fetch.call_args.kwargs["max_entries"] == 2

    @patch("fetch_podcast.save_error")
    @patch("fetch_podcast.fetch_feed")
    def test_reports_a_failure_through_the_api(self, mock_fetch, mock_error, capsys):
        mock_fetch.side_effect = Exception("network error")
        process_repository(1, URL, NOW, {})
        mock_error.assert_called_once_with(1, "network error", NOW)
        assert "network error" in capsys.readouterr().err

    @patch("fetch_podcast.save_error")
    @patch("fetch_podcast.fetch_feed", return_value=(None, []))
    def test_reports_a_feed_without_episodes(self, _fetch, mock_error):
        process_repository(1, URL, NOW, {})
        assert mock_error.call_args[0][1] == "No episode found."

    @patch("fetch_podcast.save_episodes")
    @patch("fetch_podcast.get_latest_version", return_value=None)
    @patch("fetch_podcast.save_feed_title")
    @patch("fetch_podcast.fetch_feed")
    def test_keeps_the_show_title_up_to_date(self, mock_fetch, mock_title, _latest, _save):
        mock_fetch.return_value = ("The Show", [episode()])
        process_repository(7, URL, NOW, {})
        mock_title.assert_called_once_with(7, "The Show")

    @patch("fetch_podcast.save_episodes")
    @patch("fetch_podcast.get_latest_version", return_value=None)
    @patch("fetch_podcast.save_feed_title")
    @patch("fetch_podcast.fetch_feed")
    def test_does_not_patch_the_config_when_the_title_is_unchanged(self, mock_fetch, mock_title, _latest, _save):
        mock_fetch.return_value = ("The Show", [episode()])
        process_repository(7, URL, NOW, {"title": "The Show"})
        mock_title.assert_not_called()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


class TestMain:
    @patch("fetch_podcast.process_repository")
    @patch("fetch_podcast.get_sources", return_value=[(1, URL, {}), (2, "https://b.dev/feed", {"max_entries": 1})])
    @patch("fetch_podcast.register_provider")
    def test_registers_then_processes_every_source(self, mock_register, _sources, mock_process):
        with patch("sys.argv", ["fetch_podcast.py"]):
            main()
        mock_register.assert_called_once()
        assert mock_process.call_count == 2
        assert mock_process.call_args_list[1][0][:2] == (2, "https://b.dev/feed")

    @patch("fetch_podcast.process_repository")
    @patch("fetch_podcast.add_source")
    @patch("fetch_podcast.register_provider")
    def test_add_registers_the_source_and_exits(self, _register, mock_add, mock_process, capsys):
        with patch("sys.argv", ["fetch_podcast.py", "--add", URL]):
            main()
        mock_add.assert_called_once_with(URL)
        mock_process.assert_not_called()
        assert "Repository added" in capsys.readouterr().out

    @patch("fetch_podcast.process_repository")
    @patch("fetch_podcast.get_sources", return_value=[])
    @patch("fetch_podcast.register_provider")
    def test_says_so_when_nothing_is_tracked(self, _register, _sources, mock_process, capsys):
        with patch("sys.argv", ["fetch_podcast.py"]):
            main()
        mock_process.assert_not_called()
        assert "No repositories tracked" in capsys.readouterr().out
