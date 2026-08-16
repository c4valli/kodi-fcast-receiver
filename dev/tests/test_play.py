"""Play request handling: stream classification and HTTP header propagation."""

import unittest

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin.main import encode_headers, stream_type, apply_inputstream

import xbmcgui


class TestStreamType(unittest.TestCase):

    def test_container_spellings(self):
        for container in ("application/vnd.apple.mpegurl", "application/x-mpegurl",
                          "application/mpegurl", "audio/x-mpegurl", "AUDIO/MPEGURL"):
            with self.subTest(container=container):
                self.assertEqual(stream_type(container, "https://e/s"), "hls")

        for container in ("application/dash+xml", "application/xml+dash",
                          "APPLICATION/DASH+XML"):
            with self.subTest(container=container):
                self.assertEqual(stream_type(container, "https://e/s"), "mpd")

    def test_container_with_charset_parameter(self):
        self.assertEqual(stream_type("application/dash+xml; charset=utf-8", ""), "mpd")

    def test_falls_back_to_url_extension(self):
        self.assertEqual(stream_type(None, "https://e/master.m3u8"), "hls")
        self.assertEqual(stream_type("", "https://e/manifest.mpd"), "mpd")

    def test_extension_survives_query_string(self):
        # Grayjay appends tokens to CDN URLs; the query must not hide the suffix.
        self.assertEqual(stream_type(None, "https://e/master.m3u8?token=abc&x=1"), "hls")

    def test_plain_media_is_not_adaptive(self):
        self.assertIsNone(stream_type("video/mp4", "https://e/video.mp4"))
        self.assertIsNone(stream_type(None, "https://e/video.mp4"))


class TestEncodeHeaders(unittest.TestCase):

    def test_encodes_as_query_string(self):
        self.assertEqual(
            encode_headers({"User-Agent": "Grayjay", "Referer": "https://youtube.com/"}),
            "User-Agent=Grayjay&Referer=https%3A%2F%2Fyoutube.com%2F",
        )

    def test_empty_and_invalid_inputs(self):
        for value in (None, {}, "", [], "not-a-dict"):
            with self.subTest(value=value):
                self.assertEqual(encode_headers(value), "")


class TestApplyInputstream(unittest.TestCase):

    def test_hls_properties(self):
        item = xbmcgui.ListItem()
        apply_inputstream(item, "hls", "")

        self.assertEqual(item.mime_type, "application/x-mpegURL")
        self.assertFalse(item.content_lookup)
        self.assertEqual(item.properties["inputstream"], "inputstream.adaptive")
        self.assertEqual(item.properties["inputstream.adaptive.manifest_type"], "hls")

    def test_dash_properties(self):
        item = xbmcgui.ListItem()
        apply_inputstream(item, "mpd", "")

        self.assertEqual(item.mime_type, "application/dash+xml")
        self.assertEqual(item.properties["inputstream.adaptive.manifest_type"], "mpd")

    def test_headers_reach_both_manifest_and_segments(self):
        item = xbmcgui.ListItem()
        headers = encode_headers({"Referer": "https://youtube.com/"})
        apply_inputstream(item, "mpd", headers)

        self.assertEqual(item.properties["inputstream.adaptive.manifest_headers"], headers)
        self.assertEqual(item.properties["inputstream.adaptive.stream_headers"], headers)

    def test_no_header_properties_when_none_sent(self):
        item = xbmcgui.ListItem()
        apply_inputstream(item, "hls", "")

        self.assertNotIn("inputstream.adaptive.manifest_headers", item.properties)
        self.assertNotIn("inputstream.adaptive.stream_headers", item.properties)


if __name__ == "__main__":
    unittest.main()
