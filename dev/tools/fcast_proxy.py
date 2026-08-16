#!/usr/bin/env python3
"""A logging man-in-the-middle between an FCast sender and a real receiver.

Point a sender at this, point this at a receiver that already behaves
correctly, and it prints the entire conversation in both directions while
forwarding bytes untouched. The transcript is the authoritative answer to
"what does the working receiver send that we do not?".

    # on a host the sender can reach, with the official receiver at .50
    python3 fcast_proxy.py --target 192.168.1.50 --full

Then add this machine's address manually in the sender and cast as usual.

Bytes are relayed verbatim; framing is parsed only to produce the log, so
nothing here can perturb the exchange being measured.
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
    18: "UnsubscribeEvent", 19: "Event", 20: "Flatbuf", 21: "Resource",
}

PLAYBACK_STATES = {0: "Idle", 1: "Playing", 2: "Paused"}

started = time.time()
print_lock = threading.Lock()
args = None


def emit(direction, opcode, body):
    name = OPCODES.get(opcode, f"Unknown({opcode})")

    # PlaybackUpdate arrives several times a second and drowns everything else.
    if opcode == 6 and args.quiet_updates and (body or {}).get("state") == 1:
        return

    note = ""
    if opcode == 6 and body:
        note = f"  [{PLAYBACK_STATES.get(body.get('state'), body.get('state'))}]"

    rendered = ""
    if body is not None:
        if args.full:
            rendered = "\n" + json.dumps(body, indent=2)
        else:
            text = json.dumps(body)
            rendered = text if len(text) <= 400 else text[:400] + f"... (+{len(text) - 400} bytes, use --full)"

    with print_lock:
        print(f"{time.time() - started:7.2f}s {direction} {name:<16}{note} {rendered}",
              flush=True)


def pump(source, sink, direction):
    """Relay source -> sink verbatim, decoding frames only to log them."""
    buffer = b""
    try:
        while True:
            chunk = source.recv(65536)
            if not chunk:
                break
            sink.sendall(chunk)  # forward first, untouched

            buffer += chunk
            while len(buffer) >= 4:
                size = struct.unpack("<I", buffer[:4])[0]
                if size > 32000 or len(buffer) < 4 + size:
                    break
                packet = buffer[4:4 + size]
                buffer = buffer[4 + size:]
                if not packet:
                    continue
                opcode = packet[0]
                body = None
                if len(packet) > 1:
                    try:
                        body = json.loads(packet[1:])
                    except Exception:
                        body = {"<undecodable>": packet[1:11].hex() + "..."}
                emit(direction, opcode, body)
    except Exception as e:
        with print_lock:
            print(f"{time.time() - started:7.2f}s ==== {direction} closed: {e}", flush=True)
    finally:
        for s in (source, sink):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass


def handle(sender_sock, sender_addr):
    with print_lock:
        print(f"{time.time() - started:7.2f}s ==== sender connected from {sender_addr[0]}, "
              f"dialing {args.target}:{args.target_port}", flush=True)
    try:
        receiver_sock = socket.create_connection((args.target, args.target_port), timeout=10)
    except Exception as e:
        with print_lock:
            print(f"==== could not reach receiver: {e}", flush=True)
        sender_sock.close()
        return

    threads = [
        threading.Thread(target=pump, args=(sender_sock, receiver_sock, "S-->R"), daemon=True),
        threading.Thread(target=pump, args=(receiver_sock, sender_sock, "S<--R"), daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    sender_sock.close()
    receiver_sock.close()
    with print_lock:
        print(f"{time.time() - started:7.2f}s ==== session ended", flush=True)


def main():
    global args
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", required=True,
                        help="address of the real receiver to forward to")
    parser.add_argument("--target-port", type=int, default=46899)
    parser.add_argument("--port", type=int, default=46899,
                        help="port to listen on for the sender")
    parser.add_argument("--full", action="store_true",
                        help="print message bodies in full instead of truncating")
    parser.add_argument("--quiet-updates", action="store_true",
                        help="suppress the periodic PlaybackUpdate(Playing) spam")
    args = parser.parse_args()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("", args.port))
    listener.listen(5)

    print(f"listening on :{args.port}, forwarding to {args.target}:{args.target_port}")
    print("S-->R is sender to receiver, S<--R is receiver to sender")
    print("add this machine's address manually in the sender, then cast\n")

    try:
        while True:
            sock, addr = listener.accept()
            threading.Thread(target=handle, args=(sock, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        listener.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
