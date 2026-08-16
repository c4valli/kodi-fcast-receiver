#!/usr/bin/env python3
"""A standalone FCast receiver that logs every packet, for probing senders.

Runs without Kodi. Disable the add-on (or use --port), start this, connect a
sender to this machine, and every message in both directions is printed with
its opcode name and decoded body.

Its real purpose is answering "what does this sender actually react to?".
It fakes a short playback and then emits whichever end-of-media signal you
ask for, so you can tell which one makes a sender close its viewer or move to
the next item:

    python3 fcast_trace.py --end-signal idle     # PlaybackUpdate(state=Idle)
    python3 fcast_trace.py --end-signal event    # Event(MediaItemEnd), v3 only
    python3 fcast_trace.py --end-signal stop     # bare Stop opcode
    python3 fcast_trace.py --end-signal all

Announce a different protocol version to see how the sender adapts:

    python3 fcast_trace.py --version 3
"""

import argparse
import json
import socket
import struct
import sys
import threading
import time

OPCODES = {
    0: "None", 1: "Play", 2: "Pause", 3: "Resume", 4: "Stop", 5: "Seek",
    6: "PlaybackUpdate", 7: "VolumeUpdate", 8: "SetVolume", 9: "PlaybackError",
    10: "SetSpeed", 11: "Version", 12: "Ping", 13: "Pong", 14: "Initial",
    15: "PlayUpdate", 16: "SetPlaylistItem", 17: "SubscribeEvent",
    18: "UnsubscribeEvent", 19: "Event",
}
NAME_TO_OPCODE = {name: code for code, name in OPCODES.items()}

IDLE, PLAYING = 0, 1
MEDIA_ITEM_END = 1

started = time.time()


def stamp():
    return f"{time.time() - started:7.2f}s"


FULL_BODIES = False


def log(direction, opcode, body=None, note=""):
    name = OPCODES.get(opcode, f"Unknown({opcode})")
    rendered = ""
    if body is not None:
        if FULL_BODIES:
            rendered = json.dumps(body, indent=2)
        else:
            text = json.dumps(body)
            rendered = text if len(text) <= 400 else text[:400] + f"... (+{len(text) - 400} bytes, use --full)"
    print(f"{stamp()} {direction} {name:<16} {rendered}{note}", flush=True)


class Connection:
    def __init__(self, sock, addr, args):
        self.sock = sock
        self.addr = addr
        self.args = args
        self.peer_version = None
        self.playing = False
        self.play_timer = None

    def send(self, opcode, body=None, quiet=False):
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self.sock.sendall(struct.pack("<IB", len(raw) + 1, opcode) + raw)
        if not quiet:
            log("SENT <--", opcode, body)

    def now_ms(self):
        return int(time.time() * 1000)

    def playback_update(self, state, position=0.0):
        body = {
            "generationTime": self.now_ms(),
            "state": state,
            "time": position,
            "duration": float(self.args.duration),
            "speed": 1.0,
        }
        # The reference receiver includes this whenever it is playing an item
        # out of a playlist, and null otherwise.
        if self.args.item_index is not None:
            body["itemIndex"] = self.args.item_index
        self.send(NAME_TO_OPCODE["PlaybackUpdate"], body,
                  quiet=self.args.quiet_updates and state == PLAYING)

    def fake_playback(self, play_body):
        """Pretend to play the item, then emit the requested end signal."""
        self.playing = True
        for elapsed in range(self.args.duration):
            if not self.playing:
                return
            self.playback_update(PLAYING, float(elapsed))
            time.sleep(1)
        if not self.playing:
            return

        print(f"{stamp()} ---- item finished, sending end signal: {self.args.end_signal}",
              flush=True)
        self.emit_end_signal(play_body)
        self.playing = False

    def emit_end_signal(self, play_body):
        wanted = self.args.end_signal
        if wanted in ("idle", "all"):
            self.playback_update(IDLE, float(self.args.duration))
        if wanted in ("event", "all"):
            # Only meaningful to a v3 sender that subscribed to it.
            self.send(NAME_TO_OPCODE["Event"], {
                "generationTime": self.now_ms(),
                "event": {"type": MEDIA_ITEM_END, "item": play_body or {}},
            })
        if wanted in ("stop", "all"):
            # Not a receiver-to-sender message; included because the Kodi
            # receiver used to send it and senders may react anyway.
            self.send(NAME_TO_OPCODE["Stop"])

    def handle(self, opcode, body):
        log("RECV -->", opcode, body)

        if opcode == NAME_TO_OPCODE["Version"]:
            self.peer_version = (body or {}).get("version")
            print(f"{stamp()} ---- sender speaks v{self.peer_version}, "
                  f"we announced v{self.args.version}", flush=True)
            if self.args.version >= 3 and (self.peer_version or 0) >= 3:
                self.send(NAME_TO_OPCODE["Initial"], {
                    "displayName": "fcast_trace",
                    "appName": "fcast_trace",
                    "appVersion": "1.0",
                    "playData": None,
                })

        elif opcode == NAME_TO_OPCODE["Ping"]:
            self.send(NAME_TO_OPCODE["Pong"])

        elif opcode == NAME_TO_OPCODE["Play"]:
            self.playing = False  # cancel any previous fake playback
            if self.play_timer and self.play_timer.is_alive():
                self.play_timer.join(timeout=2)
            self.play_timer = threading.Thread(
                target=self.fake_playback, args=(body,), daemon=True)
            self.play_timer.start()

        elif opcode == NAME_TO_OPCODE["Stop"]:
            self.playing = False
            self.playback_update(IDLE)

        elif opcode == NAME_TO_OPCODE["SubscribeEvent"]:
            print(f"{stamp()} ---- sender SUBSCRIBED to an event: "
                  f"{json.dumps(body)}", flush=True)

    def run(self):
        print(f"{stamp()} ==== connected from {self.addr[0]}", flush=True)
        self.send(NAME_TO_OPCODE["Version"], {"version": self.args.version})

        buffer = b""
        try:
            while True:
                chunk = self.sock.recv(32000)
                if not chunk:
                    break
                buffer += chunk
                # Length-prefixed framing: 4-byte size (opcode + body), then
                # that many bytes.
                while len(buffer) >= 4:
                    size = struct.unpack("<I", buffer[:4])[0]
                    if len(buffer) < 4 + size:
                        break
                    packet = buffer[4:4 + size]
                    buffer = buffer[4 + size:]
                    opcode = packet[0]
                    body = json.loads(packet[1:]) if len(packet) > 1 else None
                    self.handle(opcode, body)
        except Exception as e:
            print(f"{stamp()} ==== connection error: {e}", flush=True)
        finally:
            self.playing = False
            self.sock.close()
            print(f"{stamp()} ==== disconnected {self.addr[0]}", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=46899)
    parser.add_argument("--version", type=int, default=2,
                        help="protocol version to announce (default: 2)")
    parser.add_argument("--duration", type=int, default=15,
                        help="seconds of faked playback before the end signal")
    parser.add_argument("--end-signal", default="idle",
                        choices=["idle", "event", "stop", "all", "none"],
                        help="what to send when the faked item finishes")
    parser.add_argument("--full", action="store_true",
                        help="print message bodies in full instead of truncating")
    parser.add_argument("--item-index", type=int, default=None,
                        help="include this itemIndex in PlaybackUpdate messages")
    parser.add_argument("--quiet-updates", action="store_true",
                        help="do not print the periodic PlaybackUpdate spam")
    args = parser.parse_args()

    global FULL_BODIES
    FULL_BODIES = args.full

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("", args.port))
    listener.listen(5)

    host = socket.gethostbyname(socket.gethostname())
    print(f"listening on {host}:{args.port}, announcing protocol v{args.version}")
    print(f"faking {args.duration}s of playback, then sending: {args.end_signal}")
    print("add this address manually in the sender, then cast something\n")

    try:
        while True:
            sock, addr = listener.accept()
            threading.Thread(target=Connection(sock, addr, args).run, daemon=True).start()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        listener.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
