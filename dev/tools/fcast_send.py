#!/usr/bin/env python3
"""An FCast sender for exercising this receiver, with its own test media.

Nothing else casts a v3 playlist: Grayjay keeps its queue on the sender and
drives it with MediaItemEnd events, so the receiver-side playlist path has no
app that will exercise it. This sends one.

The media is generated and served from here, so there is nothing to download
or configure - short tones of a different pitch per item, which end on their
own and therefore make the playlist advance.

    # walk a five item playlist
    python3 fcast_send.py --receiver 192.168.1.42 --playlist 5

    # jump to item 3 midway, to exercise SetPlaylistItem
    python3 fcast_send.py --receiver 192.168.1.42 --playlist 5 --set-item 3

    # a single still picture, for the image viewer
    python3 fcast_send.py --receiver 192.168.1.42 --image

Everything the receiver sends back is logged. The itemIndex on its
PlaybackUpdate messages is what proves the queue is advancing.
"""

import argparse
import http.server
import io
import json
import math
import socket
import struct
import sys
import threading
import time
import wave
import zlib

OPCODES = {
    0: "None", 1: "Play", 2: "Pause", 3: "Resume", 4: "Stop", 5: "Seek",
    6: "PlaybackUpdate", 7: "VolumeUpdate", 8: "SetVolume", 9: "PlaybackError",
    10: "SetSpeed", 11: "Version", 12: "Ping", 13: "Pong", 14: "Initial",
    15: "PlayUpdate", 16: "SetPlaylistItem", 17: "SubscribeEvent",
    18: "UnsubscribeEvent", 19: "Event",
}
NAME = {v: k for k, v in OPCODES.items()}
STATES = {0: "Idle", 1: "Playing", 2: "Paused"}
MEDIA_ITEM_END = 1

started = time.time()
seen_item_indices = []
# PlayUpdate tells us what the receiver switched to even if it never reports
# an itemIndex, so the summary stays useful either way.
seen_play_urls = []


def stamp():
    return f"{time.time() - started:7.2f}s"


def tone(seconds, frequency, rate=22050):
    """A mono WAV tone, faded at both ends so it does not click."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        total = int(rate * seconds)
        fade = int(rate * 0.05)
        frames = bytearray()
        for i in range(total):
            envelope = min(1.0, i / fade, (total - i) / fade)
            value = 0.3 * envelope * math.sin(2 * math.pi * frequency * i / rate)
            frames += struct.pack("<h", int(32767 * value))
        out.writeframes(bytes(frames))
    return buffer.getvalue()


def png(width, height, rgb):
    """A solid colour PNG, written by hand to avoid needing Pillow."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind, payload):
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


class MediaServer(http.server.BaseHTTPRequestHandler):
    media = {}

    def do_GET(self):
        item = self.media.get(self.path)
        if item is None:
            self.send_error(404)
            return
        content_type, body = item
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Sender:
    def __init__(self, sock, version):
        self.sock = sock
        self.version = version
        self.running = True

    def send(self, opcode, body=None):
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self.sock.sendall(struct.pack("<IB", len(raw) + 1, opcode) + raw)
        rendered = json.dumps(body) if body is not None else ""
        if len(rendered) > 300:
            rendered = rendered[:300] + f"... (+{len(rendered) - 300} bytes)"
        print(f"{stamp()} --> {OPCODES[opcode]:<16} {rendered}", flush=True)

    def receive_forever(self):
        buffer = b""
        while self.running:
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while len(buffer) >= 4:
                size = struct.unpack("<I", buffer[:4])[0]
                if len(buffer) < 4 + size:
                    break
                packet, buffer = buffer[4:4 + size], buffer[4 + size:]
                if packet:
                    self.handle(packet[0],
                                json.loads(packet[1:]) if len(packet) > 1 else None)

    def handle(self, opcode, body):
        name = OPCODES.get(opcode, f"Unknown({opcode})")
        note = ""

        if opcode == NAME["PlaybackUpdate"] and body:
            index = body.get("itemIndex")
            note = f"  [{STATES.get(body.get('state'), body.get('state'))}" \
                   f" t={body.get('time')} item={index}]"
            if index is not None and (not seen_item_indices
                                      or seen_item_indices[-1] != index):
                seen_item_indices.append(index)
                print(f"{stamp()} ==== receiver moved to playlist item {index}", flush=True)
            # The position updates are frequent; the interesting part is above.
            if body.get("state") == 1:
                return

        if opcode == NAME["PlayUpdate"] and body:
            url = (body.get("playData") or {}).get("url")
            if url and (not seen_play_urls or seen_play_urls[-1] != url):
                seen_play_urls.append(url)

        if opcode == NAME["Ping"]:
            self.send(NAME["Pong"])
            return

        rendered = json.dumps(body) if body is not None else ""
        if len(rendered) > 300:
            rendered = rendered[:300] + f"... (+{len(rendered) - 300} bytes)"
        print(f"{stamp()} <-- {name:<16}{note} {rendered}", flush=True)


def local_address_for(host):
    """The address of ours the receiver will be able to reach."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((host, 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--receiver", required=True, help="address of the Kodi box")
    parser.add_argument("--port", type=int, default=46899)
    parser.add_argument("--version", type=int, default=3,
                        help="protocol version to announce (default: 3)")
    parser.add_argument("--playlist", type=int, metavar="N",
                        help="cast a playlist of N generated tones")
    parser.add_argument("--seconds", type=float, default=6.0,
                        help="length of each generated tone (default: 6)")
    parser.add_argument("--offset", type=int, default=None,
                        help="playlist item to start from")
    parser.add_argument("--set-item", type=int, metavar="N",
                        help="jump to item N once the first one is playing")
    parser.add_argument("--image", action="store_true",
                        help="cast a single generated still picture")
    parser.add_argument("--hold", type=float, default=90.0,
                        help="seconds to stay connected (default: 90)")
    args = parser.parse_args()

    if not args.playlist and not args.image:
        parser.error("choose --playlist N or --image")

    # Build the media and serve it from an address the receiver can reach.
    media = {}
    if args.playlist:
        for index in range(args.playlist):
            frequency = 330 * (2 ** (index / 12.0))  # a rising scale
            media[f"/tone{index}.wav"] = ("audio/wav", tone(args.seconds, frequency))
    if args.image:
        media["/picture.png"] = ("image/png", png(1280, 720, (40, 90, 160)))

    MediaServer.media = media
    httpd = http.server.HTTPServer(("", 0), MediaServer)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    origin = f"http://{local_address_for(args.receiver)}:{httpd.server_address[1]}"
    print(f"serving {len(media)} test file(s) from {origin}")
    print(f"connecting to {args.receiver}:{args.port}\n")

    sock = socket.create_connection((args.receiver, args.port), timeout=10)
    sender = Sender(sock, args.version)
    threading.Thread(target=sender.receive_forever, daemon=True).start()

    sender.send(NAME["Version"], {"version": args.version})
    time.sleep(0.5)
    if args.version >= 3:
        sender.send(NAME["Initial"], {
            "displayName": socket.gethostname(),
            "appName": "fcast_send",
            "appVersion": "1.0",
        })
        sender.send(NAME["SubscribeEvent"], {"event": {"type": MEDIA_ITEM_END}})
        time.sleep(0.3)

    if args.image:
        sender.send(NAME["Play"], {
            "container": "image/png",
            "url": f"{origin}/picture.png",
        })
    else:
        items = [{"container": "audio/wav", "url": f"{origin}/tone{i}.wav"}
                 for i in range(args.playlist)]
        content = {"contentType": 0, "items": items}
        if args.offset is not None:
            content["offset"] = args.offset
        sender.send(NAME["Play"], {
            "container": "application/json",
            "content": json.dumps(content),
        })

    if args.set_item is not None:
        time.sleep(max(2.0, args.seconds / 2))
        print(f"\n{stamp()} ==== asking the receiver to jump to item {args.set_item}\n",
              flush=True)
        sender.send(NAME["SetPlaylistItem"], {"itemIndex": args.set_item})

    try:
        time.sleep(args.hold)
    except KeyboardInterrupt:
        pass

    print()
    if args.playlist:
        print(f"itemIndex values reported:  {seen_item_indices}")
        print(f"items the receiver started: {len(seen_play_urls)}")
        expected = list(range(args.offset or 0, args.playlist))
        if not seen_play_urls:
            print("nothing was played - the playlist was not taken up at all")
        elif args.set_item is None and seen_item_indices == expected:
            print("the queue advanced through every item on its own")
        elif args.set_item is None and len(seen_play_urls) == len(expected):
            print("the queue advanced through every item, but reported no itemIndex")

    sender.running = False
    sock.close()
    httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
