"""Playlists a sender hands over for the receiver to walk."""

import json
import unittest

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin import main
from fcast_plugin.playlist import Playlist, is_playlist, parse_playlist
from fcast_plugin.FCastPackets import (
    PlayBackState,
    PlayMessage,
    PlaylistContent,
    SetPlaylistItemMessage,
)

import xbmc
import xbmcgui


def playlist_message(items, offset=None, volume=None, speed=None, url=None):
    content = {"contentType": 0, "items": items}
    if offset is not None:
        content["offset"] = offset
    if volume is not None:
        content["volume"] = volume
    if speed is not None:
        content["speed"] = speed
    return PlayMessage(container="application/json",
                       content=None if url else json.dumps(content), url=url)


def video(name, **extra):
    item = {"container": "video/mp4", "url": "https://e/%s.mp4" % name}
    item.update(extra)
    return item


class FakePlayer:
    """Records what it was asked to play."""

    start_time = 0.0

    def __init__(self):
        self.played = []
        self.playing = False

    def isPlaying(self):
        return self.playing

    def play(self, item=None, listitem=None):
        self.played.append(item)


class FakeSession:
    def __init__(self):
        self.playback_updates = []
        self.play_updates = []

    def send_playback_update(self, message):
        self.playback_updates.append(message)

    def send_play_update(self, play_data):
        self.play_updates.append(play_data)


class TestDetectionAndParsing(unittest.TestCase):

    def test_only_application_json_is_a_playlist(self):
        self.assertTrue(is_playlist(PlayMessage(container="application/json")))
        self.assertTrue(is_playlist(PlayMessage(container="APPLICATION/JSON; charset=utf-8")))
        self.assertFalse(is_playlist(PlayMessage(container="video/mp4")))
        self.assertFalse(is_playlist(PlayMessage(container="application/dash+xml")))

    def test_items_become_media_items(self):
        content = parse_playlist(playlist_message([video("a"), video("b")]))

        self.assertEqual(len(content.items), 2)
        self.assertEqual(content.items[0].url, "https://e/a.mp4")
        self.assertEqual(content.items[1].container, "video/mp4")

    def test_unknown_item_fields_are_dropped(self):
        content = parse_playlist(playlist_message(
            [video("a", somethingNew=True, showDuration=8)]))

        self.assertEqual(content.items[0].showDuration, 8)
        self.assertFalse(hasattr(content.items[0], "somethingNew"))

    def test_playlist_wide_settings_are_read(self):
        content = parse_playlist(playlist_message(
            [video("a")], offset=1, volume=0.4, speed=1.5))

        self.assertEqual(content.offset, 1)
        self.assertEqual(content.volume, 0.4)
        self.assertEqual(content.speed, 1.5)


class TestPlaylistNavigation(unittest.TestCase):

    def build(self, count=3, **kwargs):
        content = parse_playlist(playlist_message(
            [video("item%d" % i) for i in range(count)], **kwargs))
        return Playlist(content)

    def test_starts_at_the_beginning_by_default(self):
        self.assertEqual(self.build().index, 0)

    def test_offset_chooses_the_first_item(self):
        self.assertEqual(self.build(offset=2).current.url, "https://e/item2.mp4")

    def test_an_offset_past_the_end_is_clamped(self):
        self.assertEqual(self.build(count=3, offset=99).index, 2)

    def test_advancing_walks_the_queue_then_runs_out(self):
        playlist = self.build(count=2)

        self.assertEqual(playlist.advance().url, "https://e/item1.mp4")
        self.assertIsNone(playlist.advance())
        self.assertTrue(playlist.exhausted)

    def test_selecting_jumps_to_an_item(self):
        playlist = self.build()

        self.assertEqual(playlist.select(2).url, "https://e/item2.mp4")
        self.assertEqual(playlist.index, 2)

    def test_selecting_a_missing_item_changes_nothing(self):
        playlist = self.build()

        self.assertIsNone(playlist.select(99))
        self.assertIsNone(playlist.select(-1))
        self.assertEqual(playlist.index, 0)

    def test_playlist_volume_and_speed_apply_to_items_without_their_own(self):
        content = parse_playlist(playlist_message(
            [video("a"), video("b", volume=0.9)], volume=0.2, speed=1.25))
        playlist = Playlist(content)

        first = playlist.play_message()
        self.assertEqual(first.volume, 0.2)
        self.assertEqual(first.speed, 1.25)

        playlist.advance()
        self.assertEqual(playlist.play_message().volume, 0.9)


class PlaybackTestCase(unittest.TestCase):
    def setUp(self):
        xbmc.builtins_called.clear()
        xbmcgui.current_window_id = 10000
        xbmcgui.current_dialog_id = 9999
        main.sessions.clear()
        self.session = FakeSession()
        main.sessions.append(self.session)
        self.player = FakePlayer()
        self.previous_player, main.player = main.player, self.player
        main.playlist = None

    def tearDown(self):
        main.sessions.clear()
        main.player = self.previous_player
        main.playlist = None

    def play(self, message):
        main.handle_play(None, message)
        for thread in list(getattr(main, "_test_threads", [])):
            thread.join(timeout=5)


class TestPlaylistPlayback(PlaybackTestCase):

    def test_casting_a_playlist_starts_its_first_item(self):
        self.play(playlist_message([video("a"), video("b")]))

        self.assertIsNotNone(main.playlist)
        self.assertEqual(len(main.playlist), 2)
        self.assertEqual(main.playlist.index, 0)

    def test_offset_is_honoured(self):
        self.play(playlist_message([video("a"), video("b"), video("c")], offset=2))

        self.assertEqual(main.playlist.index, 2)

    def test_finishing_an_item_advances_the_queue(self):
        self.play(playlist_message([video("a"), video("b")]))

        self.assertTrue(main.advance_playlist())
        self.assertEqual(main.playlist.index, 1)

    def test_the_queue_ends_after_the_last_item(self):
        self.play(playlist_message([video("a")]))

        self.assertFalse(main.advance_playlist())
        self.assertIsNone(main.playlist)

    def test_advancing_without_a_playlist_reports_nothing_follows(self):
        # This is what tells the player that playback is genuinely over.
        self.assertFalse(main.advance_playlist())

    def test_set_playlist_item_jumps(self):
        self.play(playlist_message([video("a"), video("b"), video("c")]))

        main.handle_set_playlist_item(None, SetPlaylistItemMessage(itemIndex=2))

        self.assertEqual(main.playlist.index, 2)

    def test_set_playlist_item_out_of_range_is_ignored(self):
        self.play(playlist_message([video("a"), video("b")]))

        main.handle_set_playlist_item(None, SetPlaylistItemMessage(itemIndex=7))

        self.assertEqual(main.playlist.index, 0)

    def test_a_single_item_play_abandons_the_queue(self):
        self.play(playlist_message([video("a"), video("b")]))

        self.play(PlayMessage(container="video/mp4", url="https://e/other.mp4"))

        self.assertIsNone(main.playlist)

    def test_stop_abandons_the_queue(self):
        self.play(playlist_message([video("a"), video("b")]))

        main.handle_stop(None)

        self.assertIsNone(main.playlist)

    def test_item_index_is_reported_while_a_queue_is_playing(self):
        self.play(playlist_message([video("a"), video("b")], offset=1))

        self.assertEqual(main.current_item_index(), 1)

    def test_no_item_index_for_a_single_item(self):
        self.play(PlayMessage(container="video/mp4", url="https://e/v.mp4"))

        self.assertIsNone(main.current_item_index())

    def test_an_empty_playlist_is_rejected_rather_than_played(self):
        self.play(playlist_message([]))

        self.assertIsNone(main.playlist)

    def test_malformed_playlist_content_does_not_raise(self):
        self.play(PlayMessage(container="application/json", content="{not json"))

        self.assertIsNone(main.playlist)


if __name__ == "__main__":
    unittest.main()
