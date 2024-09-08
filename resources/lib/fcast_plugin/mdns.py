from select import select
import socket

from .util import log

class MDNSUtil:
    MDNS_ADDRESS = "224.0.0.251"
    MDNS_PORT = 5353
    MAX_BUFFER_SIZE = 4096

    _socket: socket.socket

    def _setup_socket(self):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(MDNSUtil.MDNS_ADDRESS))
        self._socket.bind(("", MDNSUtil.MDNS_PORT))

    def __init__(self):
        self._setup_socket()

    def check(self):
        log("Checking for pending mDSN packets...")
        while True:
            available, _, _ = select([self._socket], [], [], 0)
            if not available or len(available) <= 0:
                break
            data, remote = self._socket.recvfrom(MDNSUtil.MAX_BUFFER_SIZE)
            log(f"Received {len(data)} bytes from {remote}")
            # TODO: handle mDNS packet ...

