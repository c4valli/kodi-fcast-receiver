"""Player behaviour: start position, and what senders are told at end of media."""

import unittest

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin.player import FCastPlayer
from fcast_plugin.FCastPackets import EventType, PlayBackState, PlayMessage


class FakeSession:
    """Stands in for a connected sender, subscribed to events or not."""

    def __init__(self, protocol_version=3, subscribed=()):
        self.protocol_version = protocol_version
        self.subscribed_events = set(subscribed)
        self.playback_updates = []
        self.media_events = []
        self.play_updates = []
        self.opcodes = []
        self.errors = []

    def send_playback_update(self, message):
        self.playback_updates.append(message)

    def send_play_update(self, play_data):
        self.play_updates.append(play_data)

    def sendOpCode(self, opcode):
        self.opcodes.append(opcode)

    def send_playback_error(self, message):
        self.errors.append(message)

    def send_media_event(self, event_type, item):
        if self.protocol_version < 3 or event_type not in self.subscribed_events:
            return False
        self.media_events.append((event_type, item))
        return True


def owned_player(*args, **kwargs):
    """A player that believes the current playback is ours, as main sets it."""
    player = FCastPlayer(*args, **kwargs)
    player.owns_playback = True
    return player


def subscriber():
    return FakeSession(subscribed=[EventType.MEDIA_ITEM_END])


class TestOwnership(unittest.TestCase):
    """Only playback the add-on started is ours to act on.

    Reported from the field: with the add-on enabled, a gapless FLAC album
    played a few seconds per track. Kodi fires onPlayBackEnded for each track
    while the next is already playing, the add-on answered with a stop, and
    that killed the track Kodi had just started.
    """

    def test_callbacks_do_nothing_when_playback_is_not_ours(self):
        session = subscriber()
        player = FCastPlayer([session])  # not ours

        player.onAVStarted()
        player.onPlayBackTimeChanged()
        player.onPlayBackEnded()
        player.onPlayBackStopped()
        player.onPlayBackError()

        self.assertEqual(session.playback_updates, [])
        self.assertEqual(session.media_events, [])
        self.assertEqual(session.errors, [])

    def test_a_gapless_album_is_left_alone(self):
        player = FCastPlayer([])

        for _ in range(6):
            player.onAVStarted()
            player.onPlayBackEnded()

        self.assertEqual(player.seeked_to, [])

    def test_ending_our_cast_releases_ownership(self):
        session = subscriber()
        player = owned_player([session])

        player.onPlayBackEnded()

        self.assertFalse(player.owns_playback)

    def test_after_release_later_playback_is_ignored(self):
        session = subscriber()
        player = owned_player([session])
        player.onPlayBackEnded()
        session.media_events.clear()

        player.onAVStarted()
        player.onPlayBackEnded()

        self.assertEqual(session.media_events, [])


class TestStartTime(unittest.TestCase):
    """`start_time` carries the sender's requested position into playback.

    main sets it from PlayMessage.time immediately before calling play(), and
    onAVStarted consumes it once. A class-level default must not interfere
    with that: an instance attribute set by main shadows it.
    """

    def test_seeks_to_the_position_the_sender_asked_for(self):
        player = owned_player([])
        player.start_time = 12.5  # what main assigns from PlayMessage.time

        player.onAVStarted()

        self.assertEqual(player.seeked_to, [12.5])

    def test_position_is_consumed_once_not_reapplied(self):
        player = owned_player([])
        player.start_time = 30.0

        player.onAVStarted()
        player.onAVStarted()

        self.assertEqual(player.seeked_to, [30.0])

    def test_playback_started_outside_fcast_does_not_raise(self):
        # The player receives callbacks for everything Kodi plays, not just
        # what we cast. Nothing has set start_time in that case.
        player = owned_player([])

        player.onAVStarted()

        self.assertEqual(player.seeked_to, [])

    def test_zero_start_time_does_not_seek(self):
        player = owned_player([])
        player.start_time = 0.0

        player.onAVStarted()

        self.assertEqual(player.seeked_to, [])


class TestForeignPlayback(unittest.TestCase):
    """Kodi calls our callbacks for everything it plays, not just our casts.

    Both cases here were seen in the wild on 0.2.0, from a user playing local
    FLAC music while the add-on was connected. Gapless audio makes onAVStarted
    arrive after the player has already moved past the item it announced, so
    getTime() raises instead of returning.
    """

    def test_time_change_survives_a_player_that_has_moved_on(self):
        session = FakeSession()
        player = owned_player([session])

        def gone():
            raise RuntimeError("Kodi is not playing any media file")

        player.getTime = gone

        player.onPlayBackTimeChanged()  # must not raise

        self.assertEqual(session.playback_updates, [])

    def test_av_started_survives_a_player_that_has_moved_on(self):
        session = FakeSession()
        player = owned_player([session])
        player.start_time = 30.0

        def gone(*args):
            raise RuntimeError("Kodi is not playing any media file")

        player.seekTime = gone
        player.getTime = gone

        player.onAVStarted()  # must not raise

        self.assertEqual(session.playback_updates, [])

    def test_duration_failing_is_also_survivable(self):
        session = FakeSession()
        player = owned_player([session])

        def gone():
            raise RuntimeError("Kodi is not playing any media file")

        player.getTotalTime = gone

        player.onPlayBackTimeChanged()

        self.assertEqual(session.playback_updates, [])


class TestMediaItemEnd(unittest.TestCase):
    """An item finishing on its own fires MediaItemEnd, not Idle.

    Senders run their own queue from this event - Grayjay answers it with the
    Play message for the next video. Sending PlaybackUpdate(Idle) first makes
    a sender stand its queue down, so it is reserved for senders that cannot
    receive the event.
    """

    PLAYING = PlayMessage(container="application/dash+xml", url="https://e/v.mpd",
                          metadata={"title": "Episode 1", "type": 0})

    def player_with(self, sessions):
        return owned_player(sessions, get_play_data=lambda: self.PLAYING)

    def test_item_survives_the_player_having_stopped(self):
        """The provider must not be gated on isPlaying().

        onPlayBackEnded fires once playback is already over, so a provider
        that checks isPlaying() returns None and the event ships item: null -
        which tells a sender nothing about which queue entry finished.
        """
        session = subscriber()
        player = owned_player([session], get_play_data=lambda: self.PLAYING)
        player.playing = False  # the player has already torn down

        player.onPlayBackEnded()

        _, item = session.media_events[0]
        self.assertIsNotNone(item)
        self.assertEqual(item.url, "https://e/v.mpd")

    def test_subscriber_gets_the_event_and_no_idle(self):
        session = subscriber()
        player = self.player_with([session])
        player.prev_time = 124

        player.onPlayBackEnded()

        self.assertEqual(len(session.media_events), 1)
        event_type, item = session.media_events[0]
        self.assertEqual(event_type, EventType.MEDIA_ITEM_END)
        self.assertEqual(session.playback_updates, [])

    def test_event_carries_the_item_that_finished(self):
        session = subscriber()
        player = self.player_with([session])
        player.prev_time = 124

        player.onPlayBackEnded()

        _, item = session.media_events[0]
        self.assertEqual(item.url, "https://e/v.mpd")
        self.assertEqual(item.container, "application/dash+xml")
        self.assertEqual(item.metadata["title"], "Episode 1")
        self.assertEqual(item.time, 124)

    def test_inline_manifest_is_not_echoed_in_the_event(self):
        # content can be tens of KB; the packet ceiling is 32KB.
        session = subscriber()
        player = owned_player([session], get_play_data=lambda: PlayMessage(
            container="application/dash+xml", content="<MPD>" + "x" * 40000))

        player.onPlayBackEnded()

        _, item = session.media_events[0]
        self.assertIsNone(item.content)

    def test_non_subscriber_falls_back_to_idle(self):
        session = FakeSession(subscribed=[])
        player = self.player_with([session])
        player.prev_time = 90

        player.onPlayBackEnded()

        self.assertEqual(session.media_events, [])
        self.assertEqual(len(session.playback_updates), 1)
        self.assertEqual(session.playback_updates[0].state, PlayBackState.IDLE)

    def test_v2_sender_falls_back_to_idle(self):
        session = FakeSession(protocol_version=2, subscribed=[EventType.MEDIA_ITEM_END])
        player = self.player_with([session])

        player.onPlayBackEnded()

        self.assertEqual(session.media_events, [])
        self.assertEqual(session.playback_updates[0].state, PlayBackState.IDLE)

    def test_mixed_senders_each_get_what_they_can_use(self):
        modern, legacy = subscriber(), FakeSession(protocol_version=2)
        player = self.player_with([modern, legacy])

        player.onPlayBackEnded()

        self.assertEqual(len(modern.media_events), 1)
        self.assertEqual(modern.playback_updates, [])
        self.assertEqual(legacy.media_events, [])
        self.assertEqual(len(legacy.playback_updates), 1)

    def test_no_stop_opcode_is_sent_back_to_the_sender(self):
        # Stop is defined sender-to-receiver only.
        session = subscriber()
        player = self.player_with([session])

        player.onPlayBackEnded()

        self.assertEqual(session.opcodes, [])

    def test_end_after_teardown_does_not_query_the_player(self):
        # getTime()/getTotalTime() raise once the player has gone away.
        session = subscriber()
        player = self.player_with([session])

        def boom():
            raise RuntimeError("player is gone")

        player.getTime = boom
        player.getTotalTime = boom

        player.onPlayBackEnded()

        self.assertEqual(len(session.media_events), 1)


class TestDeliberateStop(unittest.TestCase):
    """A deliberate stop reports Idle, and must not advance anyone's queue."""

    def test_stopped_reports_idle_even_to_subscribers(self):
        session = subscriber()
        player = owned_player([session])

        player.onPlayBackStopped()

        self.assertEqual(session.media_events, [])
        self.assertEqual(session.playback_updates[0].state, PlayBackState.IDLE)

    def test_error_reports_a_playback_error_then_idle(self):
        session = subscriber()
        player = owned_player([session])

        player.onPlayBackError()

        self.assertEqual(len(session.errors), 1)
        self.assertEqual(session.playback_updates[0].state, PlayBackState.IDLE)
        self.assertEqual(session.media_events, [])


class TestPlaybackUpdates(unittest.TestCase):

    def test_start_reports_position_to_every_session(self):
        sessions = [FakeSession(), FakeSession()]
        player = owned_player(sessions)
        player.start_time = 8.0
        player.total_time = 120.0

        player.onAVStarted()

        for session in sessions:
            self.assertEqual(len(session.playback_updates), 1)
            self.assertEqual(session.playback_updates[0].time, 8)
            self.assertEqual(session.playback_updates[0].duration, 120)


if __name__ == "__main__":
    unittest.main()
