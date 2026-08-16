"""Volume: reading it from Kodi, setting it, and telling senders when it moves."""

import unittest

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin import main
from fcast_plugin.FCastPackets import SetVolumeMessage

import xbmc


class FakeSession:
    def __init__(self):
        self.volume_updates = []

    def send_volume_update(self, message):
        self.volume_updates.append(message)


class VolumeTestCase(unittest.TestCase):
    def setUp(self):
        xbmc.reset_jsonrpc()
        main.sessions.clear()
        main.last_volume = None

    def tearDown(self):
        main.sessions.clear()
        main.last_volume = None

    def set_kodi_volume(self, volume, muted=False):
        xbmc.jsonrpc_responses["Application.GetProperties"] = {
            "volume": volume, "muted": muted,
        }

    def calls_to(self, method):
        return [c for c in xbmc.jsonrpc_calls if c["method"] == method]


class TestReadingVolume(VolumeTestCase):

    def test_kodi_percentage_becomes_the_zero_to_one_float_fcast_uses(self):
        self.set_kodi_volume(50)
        self.assertEqual(main.get_kodi_volume(), 0.5)

        self.set_kodi_volume(100)
        self.assertEqual(main.get_kodi_volume(), 1.0)

    def test_muted_reads_as_zero(self):
        # Senders have no mute concept, so anything else would show a level
        # while nothing is audible.
        self.set_kodi_volume(80, muted=True)
        self.assertEqual(main.get_kodi_volume(), 0.0)

    def test_failed_call_reports_unknown_rather_than_a_wrong_number(self):
        xbmc.jsonrpc_responses["Application.GetProperties"] = RuntimeError("no such method")
        self.assertIsNone(main.get_kodi_volume())


class TestSettingVolume(VolumeTestCase):

    def test_sets_kodi_volume_as_a_percentage(self):
        main.handle_volume(None, SetVolumeMessage(volume=0.42))

        params = self.calls_to("Application.SetVolume")[0]["params"]
        self.assertEqual(params["volume"], 42)

    def test_out_of_range_requests_are_clamped(self):
        main.handle_volume(None, SetVolumeMessage(volume=2.5))
        main.handle_volume(None, SetVolumeMessage(volume=-1.0))

        levels = [c["params"]["volume"] for c in self.calls_to("Application.SetVolume")]
        self.assertEqual(levels, [100, 0])

    def test_our_own_change_is_not_echoed_back_to_senders(self):
        # Otherwise the poll would report it as if another device had done it.
        session = FakeSession()
        main.sessions.append(session)

        main.handle_volume(None, SetVolumeMessage(volume=0.3))
        self.set_kodi_volume(30)
        main.check_volume()

        self.assertEqual(session.volume_updates, [])


class TestPublishingChanges(VolumeTestCase):

    def test_a_change_reaches_every_sender(self):
        sessions = [FakeSession(), FakeSession()]
        main.sessions.extend(sessions)

        self.set_kodi_volume(25)
        main.check_volume()

        for session in sessions:
            self.assertEqual(len(session.volume_updates), 1)
            self.assertEqual(session.volume_updates[0].volume, 0.25)

    def test_an_unchanged_volume_says_nothing(self):
        session = FakeSession()
        main.sessions.append(session)

        self.set_kodi_volume(25)
        main.check_volume()
        main.check_volume()
        main.check_volume()

        self.assertEqual(len(session.volume_updates), 1)

    def test_each_further_change_is_published(self):
        session = FakeSession()
        main.sessions.append(session)

        for level in (10, 20, 20, 35):
            self.set_kodi_volume(level)
            main.check_volume()

        self.assertEqual([u.volume for u in session.volume_updates], [0.1, 0.2, 0.35])

    def test_unavailable_volume_is_not_published(self):
        session = FakeSession()
        main.sessions.append(session)

        xbmc.jsonrpc_responses["Application.GetProperties"] = RuntimeError("boom")
        main.check_volume()

        self.assertEqual(session.volume_updates, [])


if __name__ == "__main__":
    unittest.main()
