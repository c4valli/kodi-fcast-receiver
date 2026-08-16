"""Still pictures: classification, the viewer's lifecycle, and how long one stays up."""

import http.server
import os
import shutil
import tempfile
import threading
import time
import unittest

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin import image_cache, main, settings
from fcast_plugin.image_viewer import ImageViewer, WINDOW_SLIDESHOW
from fcast_plugin.FCastPackets import EventType, PlayBackState, PlayMessage
from test_playlist import playlist_message, video

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
       b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
       b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')


JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 32
# What a CDN that wants a login sends back, with a 200 and a picture's URL.
NOT_A_PICTURE = b'<!doctype html><html><body>Sign in</body></html>'


class ImageServer(http.server.BaseHTTPRequestHandler):
    """Serves pictures, and the ways a picture URL can fail to be one."""

    received_headers = {}
    delay = 0.0

    def do_GET(self):
        type(self).received_headers = dict(self.headers)
        if self.path.startswith("/missing"):
            self.send_error(404)
            return

        if self.path.startswith("/slow") and self.delay:
            time.sleep(self.delay)

        if self.path.startswith("/notapicture"):
            body, content_type = NOT_A_PICTURE, "image/png"
        elif self.path.startswith("/photo"):
            body, content_type = JPEG, "image/jpeg"
        else:
            body, content_type = PNG, "image/png"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class FakePlayer:
    """Enough of FCastPlayer for handle_play to reach its video branch."""

    start_time = 0.0
    owns_playback = False

    def __init__(self):
        self.played = []
        self.paused = []

    def isPlaying(self):
        return False

    def play(self, item=None, listitem=None):
        self.played.append(item)

    def doPause(self):
        self.paused.append(True)

    def doResume(self):
        self.paused.append(False)


def wait_for(predicate, timeout=5.0):
    """Give a thread the play message started a moment to get there."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class FakeSession:
    def __init__(self):
        self.playback_updates = []
        self.play_updates = []
        self.media_events = []

    def send_playback_update(self, message):
        self.playback_updates.append(message)

    def send_play_update(self, play_data):
        self.play_updates.append(play_data)

    def send_media_event(self, event_type, item):
        self.media_events.append((event_type, item))
        return True

    def states(self):
        return [update.state for update in self.playback_updates]


def image(name, **extra):
    item = {"container": "image/jpeg", "url": "https://e/%s.jpg" % name}
    item.update(extra)
    return item


class TestClassification(unittest.TestCase):

    def test_declared_image_containers(self):
        for container in ("image/jpeg", "image/png", "image/webp", "image/avif",
                          "IMAGE/JPEG", "image/jpeg; charset=binary"):
            with self.subTest(container=container):
                self.assertTrue(main.is_image(container, "https://e/p"))

    def test_extension_is_used_when_no_container_is_declared(self):
        self.assertTrue(main.is_image(None, "https://e/holiday.JPG"))
        self.assertTrue(main.is_image("", "https://e/p.png?size=large"))

    def test_streams_and_video_are_not_images(self):
        for container, url in (
            ("video/mp4", "https://e/v.mp4"),
            ("application/dash+xml", "https://e/m.mpd"),
            ("application/vnd.apple.mpegurl", "https://e/m.m3u8"),
            (None, "https://e/v.mp4"),
        ):
            with self.subTest(container=container):
                self.assertFalse(main.is_image(container, url))

    def test_a_declared_container_beats_a_misleading_extension(self):
        # A video whose URL happens to end .png is still a video.
        self.assertFalse(main.is_image("video/mp4", "https://e/thumb.png"))


class ViewerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.HTTPServer(("127.0.0.1", 0), ImageServer)
        cls.origin = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        xbmc.builtins_called.clear()
        xbmcgui.current_window_id = 10000
        xbmcgui.current_dialog_id = 9999
        ImageServer.received_headers = {}
        self.closed = []
        self.expired = []
        self.cache = tempfile.mkdtemp()
        self.viewer = ImageViewer(on_closed=lambda: self.closed.append(True),
                                  on_expired=lambda: self.expired.append(True))

    def tearDown(self):
        shutil.rmtree(self.cache, ignore_errors=True)

    def show_now(self, url, headers=None, duration=0.0):
        self.viewer.show(url, duration=duration)

    def shown(self):
        return [c for c in xbmc.builtins_called if c.startswith("ShowPicture(")]

    def cached_files(self):
        return os.listdir(self.cache)

    def on_screen(self):
        # Kodi reports the picture viewer as the current dialog, not window.
        xbmcgui.current_dialog_id = WINDOW_SLIDESHOW

    def off_screen(self):
        xbmcgui.current_dialog_id = 9999


class TestViewerLifecycle(ViewerTestCase):

    def test_dismissal_from_the_ui_is_noticed(self):
        self.show_now(self.origin + "/p.png")
        self.on_screen()
        self.viewer.poll()
        self.assertTrue(self.viewer.is_showing)

        self.off_screen()
        self.viewer.poll()

        self.assertFalse(self.viewer.is_showing)
        self.assertEqual(self.closed, [True])

    def test_slow_opening_is_not_mistaken_for_dismissal(self):
        # ShowPicture is asynchronous, so the window is not up immediately.
        self.show_now(self.origin + "/p.png")

        for _ in range(5):
            self.viewer.poll()

        self.assertTrue(self.viewer.is_showing)
        self.assertEqual(self.closed, [])

    def test_a_viewer_that_never_opens_gives_up(self):
        self.show_now(self.origin + "/p.png")
        self.viewer._opened_at -= 99  # past the open timeout
        xbmc.messages.clear()

        self.viewer.poll()

        self.assertFalse(self.viewer.is_showing)
        self.assertEqual(self.closed, [True])
        # Loud enough to survive Kodi's default log level: a picture that
        # never appears is the fault most worth being able to see.
        levels = [level for level, message in xbmc.messages
                  if "did not open" in message]
        self.assertTrue(all(level >= xbmc.LOGWARNING for level in levels))
        self.assertTrue(levels)

    def test_close_dismisses_a_visible_viewer(self):
        # ACTION_STOP, which is what Kodi binds to X - the key that actually
        # exits the picture viewer. Back navigates instead.
        self.show_now(self.origin + "/p.png")
        self.on_screen()
        xbmc.builtins_called.clear()

        self.assertTrue(self.viewer.close())

        self.assertIn("Action(Stop)", xbmc.builtins_called)
        self.assertFalse(self.viewer.is_showing)

    def test_close_still_works_when_tracking_has_gone_stale(self):
        # A sender's Stop must dismiss a picture that is genuinely on screen,
        # even if we stopped believing one was.
        self.on_screen()
        self.viewer._showing = False

        self.assertTrue(self.viewer.close())

        self.assertIn("Action(Stop)", xbmc.builtins_called)

    def test_close_is_a_no_op_with_nothing_showing(self):
        self.off_screen()

        self.assertFalse(self.viewer.close())

        self.assertEqual(xbmc.builtins_called, [])

    def test_showing_a_second_image_swaps_in_place(self):
        # Closing and reopening drops to the UI behind for a frame, which
        # reads as a dark flash between images.
        self.show_now(self.origin + "/one.png")
        self.on_screen()
        self.viewer.poll()
        xbmc.builtins_called.clear()

        self.show_now(self.origin + "/two.png")

        self.assertNotIn("Action(Stop)", xbmc.builtins_called)
        self.assertEqual(len(self.shown()), 1)
        self.assertTrue(self.viewer.is_showing)

    def test_swapping_in_place_keeps_the_viewer_confirmed_on_screen(self):
        # Resetting the open-timeout state on a swap would make the next poll
        # think the viewer was still opening.
        self.show_now(self.origin + "/one.png")
        self.on_screen()
        self.viewer.poll()

        self.show_now(self.origin + "/two.png")
        self.off_screen()
        self.viewer.poll()

        self.assertFalse(self.viewer.is_showing)
        self.assertEqual(self.closed, [True])

    def test_detection_accepts_either_the_window_or_the_dialog_id(self):
        """The viewer is a CGUIDialog, so it reports through the dialog id.

        Checking only getCurrentWindowId() meant this was always False, which
        made the poll declare the viewer dead five seconds in and left a
        sender's Stop with nothing to act on.
        """
        self.off_screen()
        self.assertFalse(self.viewer.is_on_screen)

        xbmcgui.current_dialog_id = WINDOW_SLIDESHOW
        self.assertTrue(self.viewer.is_on_screen)

        xbmcgui.current_dialog_id = 9999
        xbmcgui.current_window_id = WINDOW_SLIDESHOW
        self.assertTrue(self.viewer.is_on_screen)
        xbmcgui.current_window_id = 10000

    def test_a_visible_viewer_is_not_declared_dead_by_the_poll(self):
        self.show_now("https://e/p.jpg")
        self.on_screen()
        self.viewer._opened_at -= 99  # well past the open timeout

        for _ in range(5):
            self.viewer.poll()

        self.assertTrue(self.viewer.is_showing)
        self.assertEqual(self.closed, [])

    def test_poll_does_nothing_when_no_image_was_shown(self):
        self.viewer.poll()
        self.assertEqual(self.closed, [])


class TestCountdown(ViewerTestCase):
    """How long a picture stays up, at the viewer's own level."""

    def test_a_picture_with_no_duration_never_expires(self):
        self.show_now(self.origin + "/p.png")
        self.on_screen()

        for _ in range(5):
            self.viewer.poll()

        self.assertEqual(self.viewer.seconds_left, 0.0)
        self.assertEqual(self.expired, [])
        self.assertTrue(self.viewer.is_showing)

    def test_the_countdown_starts_when_the_picture_is_up_not_when_asked_for(self):
        # ShowPicture returns long before Kodi has fetched anything, so
        # counting from there would cut a slow picture short.
        self.show_now(self.origin + "/p.png", duration=30)
        self.assertEqual(self.viewer.seconds_left, 0.0)

        self.on_screen()
        self.viewer.poll()

        self.assertAlmostEqual(self.viewer.seconds_left, 30, delta=0.5)

    def test_time_running_out_hands_over_once(self):
        self.show_now(self.origin + "/p.png", duration=0.05)
        self.on_screen()
        self.viewer.poll()

        time.sleep(0.06)
        self.viewer.poll()
        self.viewer.poll()

        self.assertEqual(self.expired, [True])
        # Still showing: it is the callback's business what comes next.
        self.assertTrue(self.viewer.is_showing)

    def test_a_picture_swapped_in_place_gets_its_own_time(self):
        # The window is already up, so there is no opening to wait for.
        self.show_now(self.origin + "/one.png", duration=10)
        self.on_screen()
        self.viewer.poll()

        self.show_now(self.origin + "/two.png", duration=30)

        self.assertAlmostEqual(self.viewer.seconds_left, 30, delta=0.5)

    def test_closing_forgets_the_countdown(self):
        self.show_now(self.origin + "/p.png", duration=30)
        self.on_screen()
        self.viewer.poll()

        self.viewer.close()

        self.assertEqual(self.viewer.seconds_left, 0.0)

    def test_pause_holds_the_countdown_where_it_is(self):
        self.show_now(self.origin + "/p.png", duration=0.05)
        self.on_screen()
        self.viewer.poll()

        self.assertTrue(self.viewer.pause())
        time.sleep(0.06)
        self.viewer.poll()

        self.assertEqual(self.expired, [])
        self.assertGreater(self.viewer.seconds_left, 0.0)

    def test_resume_carries_on_from_where_it_paused(self):
        self.show_now(self.origin + "/p.png", duration=30)
        self.on_screen()
        self.viewer.poll()
        self.viewer.pause()
        left = self.viewer.seconds_left

        self.viewer.resume()

        self.assertAlmostEqual(self.viewer.seconds_left, left, delta=0.5)

    def test_resuming_a_picture_with_no_duration_does_not_start_a_clock(self):
        self.show_now(self.origin + "/p.png")
        self.on_screen()
        self.viewer.poll()
        self.viewer.pause()

        self.viewer.resume()

        self.assertEqual(self.viewer.seconds_left, 0.0)

    def test_pausing_before_the_picture_appears_holds_the_whole_duration(self):
        # A sender can pause inside the moment between ShowPicture and Kodi
        # having the picture up, before the countdown has begun at all.
        self.show_now(self.origin + "/p.png", duration=30)
        self.viewer.pause()

        self.on_screen()
        self.viewer.poll()
        self.assertEqual(self.viewer.seconds_left, 30)

        self.viewer.resume()

        self.assertAlmostEqual(self.viewer.seconds_left, 30, delta=0.5)
        self.assertEqual(self.expired, [])

    def test_pause_and_resume_say_whether_there_was_a_picture(self):
        # False is what tells main to give the request to the player instead.
        self.assertFalse(self.viewer.pause())
        self.assertFalse(self.viewer.resume())

        self.show_now(self.origin + "/p.png")

        self.assertTrue(self.viewer.pause())
        self.assertTrue(self.viewer.resume())


class TestPlaybackReporting(unittest.TestCase):
    def setUp(self):
        xbmc.builtins_called.clear()
        xbmcaddon.reset_settings()
        # These are about what the viewer and senders do, not about how the
        # picture arrives, so show it the way the sender sent it - straight
        # to Kodi, with no download in between to wait for.
        xbmcaddon.settings[settings.PRELOAD_IMAGES] = False
        xbmcgui.current_window_id = 10000
        xbmcgui.current_dialog_id = 9999
        main.sessions.clear()
        self.session = FakeSession()
        main.sessions.append(self.session)
        self.previous_player, main.player = main.player, FakePlayer()

    def tearDown(self):
        main.sessions.clear()
        main.image_viewer.close()
        main.player = self.previous_player
        xbmcaddon.reset_settings()

    def show(self, message):
        main.handle_play(None, message)

    def test_showing_an_image_reports_playing(self):
        main.handle_image(PlayMessage(container="image/jpeg", url="https://e/p.jpg"))

        self.assertEqual(self.session.playback_updates[0].state, PlayBackState.PLAYING)

    def test_senders_are_told_which_picture_is_on_screen(self):
        # A picture never reaches the player, so asking the player what is
        # playing reports nothing: every PlayUpdate sent while a photo was up
        # carried an empty playData, and a sender connecting to a receiver
        # with a photo on screen was told it was idle.
        message = PlayMessage(container="image/jpeg", url="https://e/p.jpg")

        main.handle_image(message)

        self.assertIs(main.get_current_play_data(), message)
        self.assertIs(self.session.play_updates[-1], message)

    def test_nothing_is_reported_once_the_picture_has_gone(self):
        main.handle_image(PlayMessage(container="image/jpeg", url="https://e/p.jpg"))
        main.image_viewer.close()

        self.assertIsNone(main.get_current_play_data())

    def test_closing_reports_idle(self):
        main.on_image_closed()

        self.assertEqual(self.session.playback_updates[-1].state, PlayBackState.IDLE)

    def test_sender_stop_dismisses_the_picture_and_reports_idle(self):
        self.show(PlayMessage(container="image/jpeg", url="https://e/p.jpg"))
        xbmcgui.current_dialog_id = WINDOW_SLIDESHOW
        xbmc.builtins_called.clear()
        self.session.playback_updates.clear()

        main.handle_stop(None)

        self.assertIn("Action(Stop)", xbmc.builtins_called)
        self.assertEqual(self.session.playback_updates[-1].state, PlayBackState.IDLE)
        self.assertFalse(main.image_viewer.is_showing)

    def test_an_image_play_never_reaches_the_video_player(self):
        # This is the whole point: the video player renders a picture for a
        # few milliseconds and then closes.
        self.show(PlayMessage(container="image/jpeg", url="https://e/p.jpg"))

        self.assertTrue(any(c.startswith("ShowPicture(") for c in xbmc.builtins_called))
        self.assertTrue(main.image_viewer.is_showing)

    def test_starting_a_video_dismisses_a_picture(self):
        # The picture viewer sits above the video window, so a picture left up
        # hides the video completely.
        self.show(PlayMessage(container="image/jpeg", url="https://e/p.jpg"))
        xbmcgui.current_dialog_id = WINDOW_SLIDESHOW
        xbmc.builtins_called.clear()

        main.handle_play(None, PlayMessage(container="video/mp4", url="https://e/v.mp4"))

        self.assertIn("Action(Stop)", xbmc.builtins_called)
        self.assertFalse(main.image_viewer.is_showing)


class CacheTestCase(ViewerTestCase):
    def setUp(self):
        super().setUp()
        xbmcvfs.temp_dir = self.cache
        image_cache.clear()

    def cached_files(self):
        return sorted(os.listdir(image_cache.directory()))


class TestImageCache(CacheTestCase):

    def test_a_picture_is_saved_with_the_extension_its_bytes_call_for(self):
        # Kodi picks the image decoder from the extension, so a cached file
        # without the right one draws nothing at all - a black screen.
        png = image_cache.fetch(self.origin + "/p")
        jpeg = image_cache.fetch(self.origin + "/photo")

        self.assertTrue(png.endswith(".png"), png)
        self.assertTrue(jpeg.endswith(".jpg"), jpeg)
        with open(png, "rb") as saved:
            self.assertEqual(saved.read(), PNG)

    def test_the_extension_comes_from_the_bytes_not_the_url(self):
        # Senders hand out URLs with no extension, or the wrong one.
        path = image_cache.fetch(self.origin + "/holiday.gif?size=large")

        self.assertTrue(path.endswith(".png"), path)

    def test_a_picture_already_downloaded_is_not_downloaded_again(self):
        first = image_cache.fetch(self.origin + "/p.png")
        ImageServer.received_headers = {}

        second = image_cache.fetch(self.origin + "/p.png")

        self.assertEqual(second, first)
        self.assertEqual(ImageServer.received_headers, {}, "went back to the server")

    def test_two_pictures_that_share_a_filename_are_kept_apart(self):
        # image.jpg is what half the photos on the internet are called.
        first = image_cache.fetch(self.origin + "/a/image.png")
        second = image_cache.fetch(self.origin + "/b/photo")

        self.assertNotEqual(first, second)
        self.assertEqual(len(self.cached_files()), 2)

    def test_request_headers_are_sent(self):
        image_cache.fetch(self.origin + "/p.png",
                          {"Referer": "https://sender/", "User-Agent": "FCast"})

        self.assertEqual(ImageServer.received_headers.get("Referer"), "https://sender/")
        self.assertEqual(ImageServer.received_headers.get("User-Agent"), "FCast")

    def test_a_download_that_fails_caches_nothing(self):
        self.assertIsNone(image_cache.fetch(self.origin + "/missing.png"))
        self.assertEqual(self.cached_files(), [])

    def test_something_that_is_not_a_picture_is_refused(self):
        # A 200 with an image content type and a sign-in page in the body is
        # how a CDN answers a request it does not like. Cached and shown, it
        # is indistinguishable from the add-on being broken.
        self.assertIsNone(image_cache.fetch(self.origin + "/notapicture.png"))
        self.assertEqual(self.cached_files(), [])

    def test_the_oldest_pictures_go_when_the_cache_is_full(self):
        original = image_cache.MAX_FILES
        image_cache.MAX_FILES = 3
        try:
            paths = [image_cache.fetch(self.origin + "/p%d.png" % i) for i in range(5)]
        finally:
            image_cache.MAX_FILES = original

        self.assertEqual(len(self.cached_files()), 3)
        self.assertTrue(os.path.exists(paths[-1]))
        self.assertFalse(os.path.exists(paths[0]))

    def test_a_picture_shown_again_survives_the_cull(self):
        original = image_cache.MAX_FILES
        image_cache.MAX_FILES = 2
        try:
            oldest = image_cache.fetch(self.origin + "/one.png")
            image_cache.fetch(self.origin + "/two.png")
            # Casting it again makes it the youngest, not the next to go.
            image_cache.fetch(self.origin + "/one.png")
            image_cache.fetch(self.origin + "/three.png")
        finally:
            image_cache.MAX_FILES = original

        self.assertTrue(os.path.exists(oldest))

    def test_a_failed_download_is_logged_where_it_can_be_seen(self):
        # Everything else this add-on logs is LOGDEBUG, which Kodi drops
        # unless debug logging is on. A fallback nobody can see is one nobody
        # can tell apart from working.
        xbmc.messages.clear()

        image_cache.fetch(self.origin + "/missing.png")

        levels = [level for level, message in xbmc.messages
                  if "Could not cache" in message]
        self.assertTrue(levels, "the failure was not logged at all")
        self.assertTrue(all(level >= xbmc.LOGWARNING for level in levels))

    def test_clearing_everything_leaves_nothing_behind(self):
        image_cache.fetch(self.origin + "/one.png")

        image_cache.clear()

        self.assertEqual(self.cached_files(), [])

    def test_bytes_that_identify_no_picture_are_not_guessed_at(self):
        self.assertEqual(image_cache.extension_for(PNG), ".png")
        self.assertEqual(image_cache.extension_for(JPEG), ".jpg")
        self.assertEqual(image_cache.extension_for(b"RIFF\x00\x00\x00\x00WEBPVP8 "), ".webp")
        self.assertEqual(image_cache.extension_for(b"GIF89a\x00\x00"), ".gif")
        self.assertIsNone(image_cache.extension_for(NOT_A_PICTURE))
        self.assertIsNone(image_cache.extension_for(b""))


class TestUnwritableCache(CacheTestCase):
    """A filesystem that cannot be written to must not stop pictures showing.

    Raspberry Pi SD cards fail read-only, and a LibreELEC storage partition
    can fill up. Either turns every cache write into an OSError, and none of
    that is a reason for a photo not to appear: the URL still goes to Kodi,
    which is what the add-on did before any of this existed.
    """

    def setUp(self):
        super().setUp()
        if getattr(os, "geteuid", lambda: 1)() == 0:
            # Root writes to a read-only directory regardless, so there would
            # be nothing to test. Some CI images run as root.
            self.skipTest("running as root")
        self.readonly = tempfile.mkdtemp()
        os.chmod(self.readonly, 0o500)
        xbmcvfs.temp_dir = self.readonly
        xbmc.builtins_called.clear()
        main.sessions.clear()
        main.sessions.append(FakeSession())
        self.previous_player, main.player = main.player, FakePlayer()
        main.playlist = None
        main.cancel_pending_image()

    def tearDown(self):
        os.chmod(self.readonly, 0o700)
        shutil.rmtree(self.readonly, ignore_errors=True)
        main.sessions.clear()
        main.image_viewer.close()
        main.player = self.previous_player
        main.cancel_pending_image()
        super().tearDown()

    def test_fetching_gives_up_instead_of_raising(self):
        xbmc.messages.clear()

        self.assertIsNone(image_cache.fetch(self.origin + "/p.png"))

        # And says so, rather than looking like a picture nobody asked for.
        self.assertTrue([level for level, message in xbmc.messages
                         if "Could not cache" in message and level >= xbmc.LOGWARNING])

    def test_the_picture_is_still_shown_straight_from_its_url(self):
        url = self.origin + "/p.png"

        main.handle_play(None, PlayMessage(container="image/png", url=url))

        self.assertTrue(wait_for(
            lambda: f"ShowPicture({url})" in xbmc.builtins_called))

    def test_pruning_and_clearing_survive_it(self):
        # Both run at start-up, before anything has been cast.
        image_cache.prune()
        image_cache.clear()


class TestPreloading(CacheTestCase):
    """Downloading a picture before it goes on screen."""

    def setUp(self):
        super().setUp()
        xbmc.builtins_called.clear()
        xbmcaddon.reset_settings()
        main.sessions.clear()
        self.session = FakeSession()
        main.sessions.append(self.session)
        self.previous_player, main.player = main.player, FakePlayer()
        main.playlist = None
        main.cancel_pending_image()

    def tearDown(self):
        main.sessions.clear()
        main.image_viewer.close()
        main.player = self.previous_player
        main.playlist = None
        main.cancel_pending_image()
        ImageServer.delay = 0.0
        xbmcaddon.reset_settings()
        super().tearDown()

    def shown_paths(self):
        return [c[len("ShowPicture("):-1] for c in xbmc.builtins_called
                if c.startswith("ShowPicture(")]

    def cast(self, path="/p.png", headers=None):
        main.handle_play(None, PlayMessage(container="image/png",
                                           url=self.origin + path,
                                           headers=headers))

    def test_a_downloaded_picture_is_what_goes_on_screen(self):
        self.cast()

        self.assertTrue(wait_for(lambda: self.shown_paths()))
        shown = self.shown_paths()[0]
        self.assertTrue(shown.endswith(".png"), shown)
        self.assertTrue(os.path.exists(shown))

    def test_turning_preloading_off_hands_the_url_straight_to_kodi(self):
        xbmcaddon.settings[settings.PRELOAD_IMAGES] = False

        self.cast()

        self.assertEqual(self.shown_paths(), [self.origin + "/p.png"])
        self.assertEqual(self.cached_files(), [])

    def test_a_download_that_fails_falls_back_to_the_url(self):
        # Whatever goes wrong, the picture still gets its chance to display:
        # this is the behaviour the add-on had before caching existed.
        self.cast("/missing.png")

        self.assertTrue(wait_for(lambda: self.shown_paths()))
        self.assertEqual(self.shown_paths(), [self.origin + "/missing.png"])

    def test_headers_go_to_the_download_not_onto_the_path(self):
        self.cast(headers={"Referer": "https://sender/"})

        self.assertTrue(wait_for(lambda: self.shown_paths()))
        self.assertEqual(ImageServer.received_headers.get("Referer"), "https://sender/")
        self.assertNotIn("|", self.shown_paths()[0])

    def test_a_picture_that_arrives_late_is_thrown_away(self):
        # Two pictures cast in quick succession: the first download must not
        # push the second one off the screen when it finally lands.
        self.cast()
        self.assertTrue(wait_for(lambda: self.shown_paths()))
        xbmc.builtins_called.clear()
        stale = main.image_request

        main.show_downloaded_image(stale - 1, PlayMessage(
            container="image/png", url=self.origin + "/old.png"), "url", 0.0)

        self.assertEqual(self.shown_paths(), [])

    def test_starting_a_video_discards_a_picture_still_downloading(self):
        # Otherwise the picture viewer opens straight back over the video.
        ImageServer.delay = 0.4
        self.cast("/slow.png")

        main.handle_play(None, PlayMessage(container="video/mp4", url="https://e/v.mp4"))
        time.sleep(0.6)

        self.assertEqual(self.shown_paths(), [])

    def test_the_picture_it_replaces_is_kept_for_next_time(self):
        # Stepping back through a slideshow is the common case, and it should
        # not mean downloading the previous photo all over again.
        self.cast("/one.png")
        self.assertTrue(wait_for(lambda: self.shown_paths()))

        self.cast("/two.png")
        self.assertTrue(wait_for(lambda: len(self.shown_paths()) == 2))

        self.assertEqual(len(self.cached_files()), 2)
        for path in self.shown_paths():
            self.assertTrue(os.path.exists(path))

    def test_casting_the_picture_already_on_screen_leaves_it_alone(self):
        # Senders re-send what is showing. Acting on it means a torn-down
        # viewer, a rebuild, a re-download and Kodi's window sound each time.
        self.cast()
        self.assertTrue(wait_for(lambda: self.shown_paths()))
        xbmc.builtins_called.clear()

        self.cast()
        time.sleep(0.1)

        self.assertEqual(self.shown_paths(), [])
        self.assertEqual(self.session.states()[-1], PlayBackState.PLAYING)

    def test_the_same_picture_is_shown_again_after_the_viewer_is_closed(self):
        # Which is the whole reason that guard asks whether it is on screen
        # rather than whether it was the last one asked for.
        self.cast()
        self.assertTrue(wait_for(lambda: self.shown_paths()))
        main.handle_stop(None)
        xbmc.builtins_called.clear()

        self.cast()

        self.assertTrue(wait_for(lambda: self.shown_paths()))

    def test_a_repeat_of_a_picture_still_downloading_is_ignored(self):
        ImageServer.delay = 0.3
        self.cast("/slow.png")

        self.cast("/slow.png")

        self.assertTrue(wait_for(lambda: self.shown_paths()))
        time.sleep(0.4)
        self.assertEqual(len(self.shown_paths()), 1)

    def test_the_same_photo_twice_in_a_queue_does_not_stall_it(self):
        # Skipping the redraw for a picture already up must not also skip the
        # countdown, or a queue holding the same photo twice stops there.
        item = {"container": "image/png", "url": self.origin + "/same.png",
                "showDuration": 0.2}

        main.handle_play(None, playlist_message([item, dict(item)]))
        self.assertTrue(wait_for(lambda: self.shown_paths()))
        xbmcgui.current_dialog_id = WINDOW_SLIDESHOW
        main.image_viewer.poll()

        time.sleep(0.25)
        main.image_viewer.poll()

        self.assertEqual(main.playlist.index, 1)
        self.assertGreater(main.image_viewer.seconds_left, 0.0)
        # Redrawn once, for the first of the two.
        self.assertEqual(len(self.shown_paths()), 1)

    def test_stopping_keeps_what_was_downloaded(self):
        self.cast()
        self.assertTrue(wait_for(lambda: self.shown_paths()))

        main.handle_stop(None)

        self.assertEqual(len(self.cached_files()), 1)

    def test_a_playlist_downloads_each_picture_as_it_comes_round(self):
        # The two halves of this work together or not at all: the countdown
        # runs in the poll, and what it moves on to arrives on a thread.
        def picture(name):
            return {"container": "image/png", "url": self.origin + "/%s.png" % name,
                    "showDuration": 0.05}

        main.handle_play(None, playlist_message([picture("one"), picture("two")]))
        self.assertTrue(wait_for(lambda: self.shown_paths()))
        xbmcgui.current_dialog_id = WINDOW_SLIDESHOW
        main.image_viewer.poll()

        time.sleep(0.06)
        main.image_viewer.poll()

        self.assertTrue(wait_for(lambda: len(self.shown_paths()) == 2))
        self.assertEqual(main.playlist.index, 1)
        for path in self.shown_paths():
            self.assertTrue(path.endswith(".png"), path)
            self.assertNotIn(self.origin, path)


class TestKeepingTheBoxAwake(unittest.TestCase):
    """A picture on screen has to count as the box being in use.

    Kodi resets the screensaver only for a slideshow of its own, so the idle
    timer runs on underneath a cast photo. When it expires the screensaver
    tries to come to the front, is refused because the picture viewer is a
    modal dialog, and on some skins the refusal is audible - which is how this
    was noticed on a device.
    """

    def setUp(self):
        xbmc.reset_jsonrpc()
        xbmcaddon.reset_settings()
        xbmcaddon.settings[settings.PRELOAD_IMAGES] = False
        xbmcgui.current_window_id = 10000
        xbmcgui.current_dialog_id = 9999
        main.sessions.clear()
        main.sessions.append(FakeSession())
        self.previous_player, main.player = main.player, FakePlayer()
        main.last_wake = 0.0

    def tearDown(self):
        main.sessions.clear()
        main.image_viewer.close()
        main.player = self.previous_player
        main.last_wake = 0.0
        xbmc.reset_jsonrpc()
        xbmcaddon.reset_settings()

    def wake_calls(self):
        return [call for call in xbmc.jsonrpc_calls
                if call.get("method") == "Input.ExecuteAction"]

    def show_a_picture(self):
        main.handle_image(PlayMessage(container="image/jpeg", url="https://e/p.jpg"))
        xbmc.reset_jsonrpc()

    def test_nothing_is_sent_with_no_picture_on_screen(self):
        main.keep_awake()

        self.assertEqual(self.wake_calls(), [])

    def test_a_picture_on_screen_keeps_the_screensaver_off(self):
        self.show_a_picture()

        main.keep_awake()

        self.assertEqual(len(self.wake_calls()), 1)
        # ACTION_NOOP: the window manager hands it to the picture viewer,
        # which ignores it, and Kodi resets the screensaver timer on the way.
        self.assertEqual(self.wake_calls()[0]["params"], {"action": "noop"})

    def test_it_is_not_sent_on_every_tick(self):
        self.show_a_picture()

        for _ in range(10):
            main.keep_awake()

        self.assertEqual(len(self.wake_calls()), 1)

    def test_it_goes_again_once_the_interval_has_passed(self):
        self.show_a_picture()
        main.keep_awake()

        main.last_wake -= main.WAKE_INTERVAL + 1
        main.keep_awake()

        self.assertEqual(len(self.wake_calls()), 2)

    def test_the_user_can_let_the_screen_sleep(self):
        xbmcaddon.settings[settings.KEEP_AWAKE_FOR_PICTURES] = False
        self.show_a_picture()

        main.keep_awake()

        self.assertEqual(self.wake_calls(), [])

    def test_it_stops_once_the_picture_is_gone(self):
        self.show_a_picture()
        main.image_viewer.close()

        main.keep_awake()

        self.assertEqual(self.wake_calls(), [])


class TestPictureDuration(unittest.TestCase):
    """showDuration: only playlists, and only as long as the user allows."""

    def setUp(self):
        xbmc.builtins_called.clear()
        xbmcaddon.reset_settings()
        # Not about how the picture arrives - see TestPreloading for that.
        xbmcaddon.settings[settings.PRELOAD_IMAGES] = False
        xbmcgui.current_window_id = 10000
        xbmcgui.current_dialog_id = 9999
        main.sessions.clear()
        self.session = FakeSession()
        main.sessions.append(self.session)
        self.previous_player, main.player = main.player, FakePlayer()
        main.playlist = None

    def tearDown(self):
        main.sessions.clear()
        main.image_viewer.close()
        main.player = self.previous_player
        main.playlist = None
        xbmcaddon.reset_settings()

    def on_screen(self):
        xbmcgui.current_dialog_id = WINDOW_SLIDESHOW

    def play(self, message):
        main.handle_play(None, message)

    def start(self, *items):
        """Cast a playlist and let the first picture reach the screen."""
        self.play(playlist_message(list(items)))
        self.on_screen()
        main.image_viewer.poll()

    def test_a_picture_cast_on_its_own_is_never_timed_out(self):
        # Ozan's constraint, and what FCast's own receiver does: it starts the
        # showDuration timer only for items that came from a playlist.
        xbmcaddon.settings[settings.PLAYLIST_IMAGE_DURATION] = 5

        self.play(PlayMessage(container="image/jpeg", url="https://e/p.jpg"))
        self.on_screen()
        main.image_viewer.poll()

        self.assertEqual(main.image_viewer.seconds_left, 0.0)

    def test_a_playlist_picture_gets_the_duration_the_sender_asked_for(self):
        self.start(image("a", showDuration=45), image("b"))

        self.assertAlmostEqual(main.image_viewer.seconds_left, 45, delta=0.5)

    def test_a_playlist_picture_with_no_duration_gets_the_users(self):
        # Otherwise it holds up the rest of the queue for good: nothing ever
        # reports that a picture finished.
        xbmcaddon.settings[settings.PLAYLIST_IMAGE_DURATION] = 20

        self.start(image("a"), image("b"))

        self.assertAlmostEqual(main.image_viewer.seconds_left, 20, delta=0.5)

    def test_the_user_can_override_the_senders_duration(self):
        xbmcaddon.settings.update({
            settings.PLAYLIST_IMAGE_DURATION: 20,
            settings.PLAYLIST_IMAGE_DURATION_OVERRIDE: True,
        })

        self.start(image("a", showDuration=45), image("b"))

        self.assertAlmostEqual(main.image_viewer.seconds_left, 20, delta=0.5)

    def test_a_duration_of_zero_leaves_the_picture_up(self):
        # The way out for anyone who wants to move through a slideshow by hand.
        xbmcaddon.settings[settings.PLAYLIST_IMAGE_DURATION] = 0

        self.start(image("a"), image("b"))

        self.assertEqual(main.image_viewer.seconds_left, 0.0)

    def test_the_senders_duration_still_applies_when_the_users_is_zero(self):
        xbmcaddon.settings[settings.PLAYLIST_IMAGE_DURATION] = 0

        self.start(image("a", showDuration=45), image("b"))

        self.assertAlmostEqual(main.image_viewer.seconds_left, 45, delta=0.5)

    def test_time_running_out_moves_the_queue_on(self):
        self.start(image("a", showDuration=0.05), image("b", showDuration=60))
        xbmc.builtins_called.clear()

        time.sleep(0.06)
        main.image_viewer.poll()

        self.assertEqual(main.playlist.index, 1)
        self.assertIn("ShowPicture(https://e/b.jpg)", xbmc.builtins_called)
        self.assertTrue(main.image_viewer.is_showing)

    def test_moving_on_reports_the_picture_that_finished(self):
        # A MediaItemEnd with a null item tells a sender nothing: it matches
        # the item against its own queue entry to know what ended.
        self.start(image("a", showDuration=0.05), image("b", showDuration=60))

        time.sleep(0.06)
        main.image_viewer.poll()

        self.assertEqual(len(self.session.media_events), 1)
        event_type, item = self.session.media_events[0]
        self.assertEqual(event_type, EventType.MEDIA_ITEM_END)
        self.assertIsNotNone(item)
        self.assertEqual(item.url, "https://e/a.jpg")

    def test_the_last_picture_closes_the_viewer_and_reports_idle(self):
        self.start(image("only", showDuration=0.05))
        self.session.playback_updates.clear()
        xbmc.builtins_called.clear()

        time.sleep(0.06)
        main.image_viewer.poll()

        self.assertIn("Action(Stop)", xbmc.builtins_called)
        self.assertFalse(main.image_viewer.is_showing)
        self.assertEqual(self.session.states()[-1], PlayBackState.IDLE)
        self.assertIsNone(main.playlist)

    def test_a_video_after_a_picture_is_played_not_shown(self):
        self.start(image("a", showDuration=0.05), video("b"))
        xbmc.builtins_called.clear()

        time.sleep(0.06)
        main.image_viewer.poll()

        self.assertEqual(main.playlist.index, 1)
        # The picture viewer sits above the video window, so it has to go.
        self.assertIn("Action(Stop)", xbmc.builtins_called)
        self.assertTrue(wait_for(lambda: main.player.played == ["https://e/b.mp4"]))

    def test_pausing_a_picture_holds_it_and_says_so(self):
        self.start(image("a", showDuration=60), image("b"))
        self.session.playback_updates.clear()

        main.handle_pause(None)

        self.assertEqual(self.session.states(), [PlayBackState.PAUSED])
        self.assertGreater(main.image_viewer.seconds_left, 0.0)

        main.handle_resume(None)

        self.assertEqual(self.session.states()[-1], PlayBackState.PLAYING)

    def test_pausing_a_picture_leaves_the_player_alone(self):
        # While a picture is up, whatever Kodi is playing is not ours.
        self.start(image("a", showDuration=60))
        main.player.paused = []

        main.handle_pause(None)

        self.assertEqual(main.player.paused, [])


if __name__ == "__main__":
    unittest.main()
