"""FCast wire protocol: framing, opcode tolerance and version negotiation."""

import json
import random
import socket
import struct
import unittest
from unittest import mock

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin import FCastSession as session_module
from fcast_plugin.FCastSession import (
    FCAST_VERSION,
    MAXIMUM_PACKET_LENGTH,
    Event,
    FCastSession,
    OpCode,
    SessionState,
    message_from_json,
)
from fcast_plugin.FCastPackets import EventType, MediaItem, PlayMessage


def packet(opcode, body=None):
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    return struct.pack("<IB", len(raw) + 1, opcode) + raw


def decode_packets(data):
    """Split a byte stream into [(opcode, parsed body or None), ...]."""
    packets, pos = [], 0
    while pos < len(data):
        size, opcode = struct.unpack("<IB", data[pos:pos + 5])
        body = data[pos + 5:pos + 4 + size]
        packets.append((opcode, json.loads(body) if body else None))
        pos += 4 + size
    return packets


class SessionHarness:
    """Drives a FCastSession over a socketpair and records emitted events."""

    def __init__(self, get_play_data=None):
        self.receiver_sock, self.sender_sock = socket.socketpair()
        self.session = FCastSession(self.receiver_sock, get_play_data=get_play_data)
        self.events = []
        for event in Event:
            self.session.on(event, self._record(event))

    def _record(self, event):
        return lambda session, message: self.events.append((event, message))

    def feed(self, data, chunk_size=None):
        if chunk_size is None:
            self.session.process_bytes(data)
            return
        for i in range(0, len(data), chunk_size):
            self.session.process_bytes(data[i:i + chunk_size])

    def sent(self):
        self.sender_sock.setblocking(False)
        try:
            return self.sender_sock.recv(65536)
        except BlockingIOError:
            return b""

    def close(self):
        self.receiver_sock.close()
        self.sender_sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class TestFraming(unittest.TestCase):
    """Packets must survive arriving in arbitrarily sized reads.

    Reading a fixed four bytes for the length prefix regardless of what the
    buffer already held used to drop payload bytes, desynchronising the stream
    and killing the connection. It only ever worked when each packet happened
    to arrive as exactly one read.
    """

    STREAM_MESSAGES = [
        (OpCode.SEEK, {"time": 10}),
        (OpCode.PLAY, {"container": "video/mp4", "url": "https://example/v.mp4",
                       "metadata": {"type": 0, "title": "T" * 300}}),
        (OpCode.SEEK, {"time": 20}),
        (OpCode.PAUSE, None),
        (OpCode.SEEK, {"time": 30}),
    ]
    EXPECTED = [Event.SEEK, Event.PLAY, Event.SEEK, Event.PAUSE, Event.SEEK]

    def setUp(self):
        self.stream = b"".join(packet(op, body) for op, body in self.STREAM_MESSAGES)

    def replay(self, chunks):
        with SessionHarness() as harness:
            for chunk in chunks:
                harness.session.process_bytes(chunk)
            return [event for event, _ in harness.events]

    def test_every_fixed_chunk_size(self):
        for size in range(1, len(self.stream) + 1):
            with self.subTest(chunk_size=size):
                chunks = [self.stream[i:i + size] for i in range(0, len(self.stream), size)]
                self.assertEqual(self.replay(chunks), self.EXPECTED)

    def test_random_splits(self):
        rng = random.Random(1234)
        for run in range(500):
            chunks, pos = [], 0
            while pos < len(self.stream):
                size = rng.randint(1, 40)
                chunks.append(self.stream[pos:pos + size])
                pos += size
            with self.subTest(run=run):
                self.assertEqual(self.replay(chunks), self.EXPECTED)

    def test_oversized_packet_is_rejected(self):
        with SessionHarness() as harness:
            with self.assertRaises(Exception):
                harness.session.process_bytes(struct.pack("<IB", 999999, OpCode.PAUSE))
            self.assertEqual(harness.session.state, SessionState.DISCONNECTED)


class TestOpcodeTolerance(unittest.TestCase):
    """Unknown and unsupported opcodes are dropped, never fatal.

    A v3 sender may put Initial or SubscribeEvent on the wire before it has
    processed our v2 announcement. Tearing the session down over that is what
    made newer senders look broken.
    """

    def test_unsupported_and_unknown_opcodes_keep_session_alive(self):
        with SessionHarness() as harness:
            harness.feed(b"".join([
                packet(OpCode.VERSION, {"version": 3}),
                packet(OpCode.INITIAL, {"displayName": "Pixel", "appName": "Grayjay"}),
                packet(OpCode.SUBSCRIBEEVENT, {"event": {"type": 3, "keys": ["ArrowLeft"]}}),
                packet(42, {"from": "a future protocol version"}),
                packet(OpCode.PAUSE),
            ]), chunk_size=7)

            self.assertNotEqual(harness.session.state, SessionState.DISCONNECTED)
            self.assertEqual([event for event, _ in harness.events], [Event.PAUSE])

    def test_malformed_body_does_not_kill_session(self):
        with SessionHarness() as harness:
            body = b"{not json"
            harness.feed(struct.pack("<IB", len(body) + 1, OpCode.SEEK) + body)
            harness.feed(packet(OpCode.PAUSE))

            self.assertNotEqual(harness.session.state, SessionState.DISCONNECTED)
            self.assertEqual([event for event, _ in harness.events], [Event.PAUSE])

    def test_ping_is_answered_with_pong(self):
        with SessionHarness() as harness:
            harness.sent()  # discard the Version we announce on connect
            harness.feed(packet(OpCode.PING))

            reply = harness.sent()
            self.assertEqual(struct.unpack("<IB", reply[:5]), (1, OpCode.PONG))


class TestVersionNegotiation(unittest.TestCase):

    def test_version_announced_once_on_connect(self):
        with SessionHarness() as harness:
            sent = harness.sent()
            size, opcode = struct.unpack("<IB", sent[:5])
            self.assertEqual(opcode, OpCode.VERSION)
            self.assertEqual(json.loads(sent[5:4 + size]), {"version": FCAST_VERSION})
            self.assertEqual(len(sent), 4 + size)

    def test_no_echo_when_sender_announces(self):
        # Initial legitimately follows for a v3 peer; a second Version does not.
        with SessionHarness() as harness:
            harness.sent()
            harness.feed(packet(OpCode.VERSION, {"version": 3}))

            replies = [opcode for opcode, _ in decode_packets(harness.sent())]
            self.assertNotIn(OpCode.VERSION, replies)

    def test_session_downgrades_to_our_version(self):
        with SessionHarness() as harness:
            harness.feed(packet(OpCode.VERSION, {"version": 3}))
            self.assertEqual(harness.session.peer_version, 3)
            self.assertEqual(harness.session.protocol_version, FCAST_VERSION)

    def test_session_follows_an_older_sender_down(self):
        with SessionHarness() as harness:
            harness.feed(packet(OpCode.VERSION, {"version": 1}))
            self.assertEqual(harness.session.protocol_version, 1)


class TestInitialHandshake(unittest.TestCase):
    """v3 requires an Initial exchange once both sides know the version.

    Guarded on the negotiated version, so it stays dormant while this receiver
    still announces v2 and switches on by itself when FCAST_VERSION is raised.
    """

    def test_no_initial_to_a_v2_sender(self):
        with SessionHarness() as harness:
            harness.sent()
            harness.feed(packet(OpCode.VERSION, {"version": 2}))
            self.assertEqual(harness.sent(), b"")

    def test_initial_sent_once_both_sides_are_v3(self):
        with mock.patch.object(session_module, "FCAST_VERSION", 3):
            with SessionHarness() as harness:
                harness.sent()
                harness.feed(packet(OpCode.VERSION, {"version": 3}))

                packets = decode_packets(harness.sent())
                self.assertEqual([op for op, _ in packets], [OpCode.INITIAL])

                body = packets[0][1]
                self.assertTrue(body["displayName"].startswith("Kodi - "))
                self.assertEqual(body["appName"], "FCast Receiver")
                self.assertEqual(body["appVersion"], "0.0.0-test")
                # Unset fields are omitted, not sent as explicit nulls.
                self.assertNotIn("playData", body)

    def test_initial_is_not_repeated(self):
        with mock.patch.object(session_module, "FCAST_VERSION", 3):
            with SessionHarness() as harness:
                harness.feed(packet(OpCode.VERSION, {"version": 3}))
                harness.sent()
                harness.feed(packet(OpCode.VERSION, {"version": 3}))
                self.assertEqual(harness.sent(), b"")

    def test_initial_reports_what_is_already_playing(self):
        playing = PlayMessage(container="video/mp4", url="https://e/v.mp4", time=42)
        with mock.patch.object(session_module, "FCAST_VERSION", 3):
            with SessionHarness(get_play_data=lambda: playing) as harness:
                harness.sent()
                harness.feed(packet(OpCode.VERSION, {"version": 3}))

                body = decode_packets(harness.sent())[0][1]
                self.assertEqual(body["playData"]["url"], "https://e/v.mp4")
                self.assertEqual(body["playData"]["time"], 42)

    def test_inline_manifest_is_not_echoed_back(self):
        # An inline DASH manifest can be tens of kilobytes; echoing it would
        # breach the 32KB packet ceiling for no benefit.
        playing = PlayMessage(container="application/dash+xml", content="<MPD>" + "x" * 40000)
        with mock.patch.object(session_module, "FCAST_VERSION", 3):
            with SessionHarness(get_play_data=lambda: playing) as harness:
                harness.sent()
                harness.feed(packet(OpCode.VERSION, {"version": 3}))

                sent = harness.sent()
                self.assertLess(len(sent), MAXIMUM_PACKET_LENGTH)
                self.assertNotIn("content", decode_packets(sent)[0][1]["playData"])


class TestPlayUpdate(unittest.TestCase):

    def test_not_sent_to_a_v2_sender(self):
        with SessionHarness() as harness:
            harness.feed(packet(OpCode.VERSION, {"version": 2}))
            harness.sent()

            harness.session.send_play_update(PlayMessage(container="video/mp4", url="https://e/v.mp4"))
            self.assertEqual(harness.sent(), b"")

    def test_sent_to_a_v3_sender(self):
        with mock.patch.object(session_module, "FCAST_VERSION", 3):
            with SessionHarness() as harness:
                harness.feed(packet(OpCode.VERSION, {"version": 3}))
                harness.sent()

                harness.session.send_play_update(
                    PlayMessage(container="video/mp4", url="https://e/v.mp4")
                )

                opcode, body = decode_packets(harness.sent())[0]
                self.assertEqual(opcode, OpCode.PLAYUPDATE)
                self.assertEqual(body["playData"]["url"], "https://e/v.mp4")
                self.assertIsInstance(body["generationTime"], int)


class TestEventSubscription(unittest.TestCase):
    """Events only go to senders that asked for them.

    Grayjay subscribes to MediaItemEnd on connect and answers the event with
    the Play message for the next video, which is how its queue advances.
    """

    def subscribe(self, harness, event_type):
        harness.feed(packet(OpCode.VERSION, {"version": 3}))
        harness.feed(packet(OpCode.SUBSCRIBEEVENT, {"event": {"type": event_type}}))
        harness.sent()

    def test_event_is_sent_to_a_subscriber(self):
        with SessionHarness() as harness:
            self.subscribe(harness, EventType.MEDIA_ITEM_END)

            item = MediaItem(container="video/mp4", url="https://e/v.mp4")
            self.assertTrue(
                harness.session.send_media_event(EventType.MEDIA_ITEM_END, item))

            opcode, body = decode_packets(harness.sent())[0]
            self.assertEqual(opcode, OpCode.EVENT)
            self.assertEqual(body["event"]["type"], EventType.MEDIA_ITEM_END)
            self.assertEqual(body["event"]["item"]["url"], "https://e/v.mp4")
            self.assertIsInstance(body["generationTime"], int)

    def test_no_event_without_a_subscription(self):
        with SessionHarness() as harness:
            harness.feed(packet(OpCode.VERSION, {"version": 3}))
            harness.sent()

            self.assertFalse(
                harness.session.send_media_event(EventType.MEDIA_ITEM_END, None))
            self.assertEqual(harness.sent(), b"")

    def test_subscription_is_per_event_type(self):
        with SessionHarness() as harness:
            self.subscribe(harness, EventType.MEDIA_ITEM_END)

            self.assertFalse(
                harness.session.send_media_event(EventType.MEDIA_ITEM_START, None))
            self.assertTrue(
                harness.session.send_media_event(EventType.MEDIA_ITEM_END, None))

    def test_unsubscribe_stops_the_events(self):
        with SessionHarness() as harness:
            self.subscribe(harness, EventType.MEDIA_ITEM_END)
            harness.feed(packet(OpCode.UNSUBSCRIBEEVENT,
                                {"event": {"type": EventType.MEDIA_ITEM_END}}))

            self.assertFalse(
                harness.session.send_media_event(EventType.MEDIA_ITEM_END, None))

    def test_no_event_to_a_v2_sender_even_if_it_subscribed(self):
        with SessionHarness() as harness:
            harness.feed(packet(OpCode.VERSION, {"version": 2}))
            harness.feed(packet(OpCode.SUBSCRIBEEVENT,
                                {"event": {"type": EventType.MEDIA_ITEM_END}}))
            harness.sent()

            self.assertFalse(
                harness.session.send_media_event(EventType.MEDIA_ITEM_END, None))
            self.assertEqual(harness.sent(), b"")

    def test_malformed_subscription_is_ignored(self):
        with SessionHarness() as harness:
            harness.feed(packet(OpCode.VERSION, {"version": 3}))
            harness.feed(packet(OpCode.SUBSCRIBEEVENT, {"event": {}}))
            harness.feed(packet(OpCode.SUBSCRIBEEVENT, {}))

            self.assertNotEqual(harness.session.state, SessionState.DISCONNECTED)
            self.assertEqual(harness.session.subscribed_events, set())


class TestMessageFromJson(unittest.TestCase):

    def test_unknown_fields_are_dropped(self):
        message = message_from_json(PlayMessage, json.dumps({
            "container": "video/mp4",
            "url": "https://example/v.mp4",
            "volume": 0.8,
            "metadata": {"type": 0, "title": "Test"},
            "someFutureField": True,
        }).encode())

        self.assertEqual(message.url, "https://example/v.mp4")
        self.assertEqual(message.volume, 0.8)
        self.assertFalse(hasattr(message, "someFutureField"))

    def test_non_object_body_is_rejected(self):
        with self.assertRaises(ValueError):
            message_from_json(PlayMessage, b"[1, 2, 3]")


if __name__ == "__main__":
    unittest.main()
