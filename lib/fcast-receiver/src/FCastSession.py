from enum import Enum
import json
import socket
import struct
from typing import Callable, Dict

from FCastPackets import *

class SessionState(Enum):
    IDLE = 0
    WAITING_FOR_LENGTH = 1
    WAITING_FOR_DATA = 2
    DISCONNECTED = 3

class OpCode(Enum):
    NONE = 0
    PLAY = 1
    PAUSE = 2
    RESUME = 3
    STOP = 4
    SEEK = 5
    PLAYBACK_UPDATE = 6
    VOLUME_UPDATE = 7
    SET_VOLUME = 8

LENGTH_BYTES = 4
MAXIMUM_PACKET_LENGTH = 32000

class FCastSession:

    buffer: bytes = bytes()
    bytes_read: int = 0
    packet_length: int = 0
    client: socket.socket = None
    state: SessionState = SessionState.DISCONNECTED

    __listeners: Dict[str, Callable[[any], any]] = {}

    def __init__(self, client: socket.socket):
        self.client = client
        self.state = SessionState.WAITING_FOR_LENGTH

    def close(self):
        self.client.close()
        self.client = None
        self.state = SessionState.DISCONNECTED

    def send_playback_update(self, value: PlayBackUpdateMessage):
        self.__send(OpCode.PLAYBACK_UPDATE, value)

    def send_volume_update(self, value: VolumeUpdateMessage):
        self.__send(OpCode.VOLUME_UPDATE, value)

    def __send(self, opcode: OpCode, message = None):
        # FCast packet header
        json_message = json.dumps(message.__dict__) if message else None
        header = struct.pack("<IB", len(json_message) if json_message else 0, opcode.value)

        packet = header

        # Append data to FCast packet, if any
        if json_message:
            packet += json_message.encode("utf-8")

        # Send the packet
        self.client.send(packet)

    def process_bytes(self, received_bytes: bytes):
        if not received_bytes or len(received_bytes) <= 0:
            return
        
        if self.state == SessionState.WAITING_FOR_LENGTH:
            self.__handle_length_bytes(received_bytes)
        elif self.state == SessionState.WAITING_FOR_DATA:
            self.__handle_packet_bytes(received_bytes)
        else:
            raise Exception("Data received is unhandled in current session state %s" % self.state)
        
    def __handle_length_bytes(self, received_bytes: bytes):
        bytes_to_read = min(LENGTH_BYTES, len(received_bytes))
        bytes_remaining = len(received_bytes) - bytes_to_read

        self.buffer += received_bytes[:bytes_to_read]
        self.bytes_read += bytes_to_read

        if self.bytes_read >= LENGTH_BYTES:
            self.state = SessionState.WAITING_FOR_DATA
            self.packet_length = struct.unpack("<I", self.buffer[:4])[0]
            self.bytes_read = 0

            if self.packet_length > MAXIMUM_PACKET_LENGTH:
                self.client.close()
                self.state = SessionState.DISCONNECTED
                raise Exception("Packet length %d exceeds maximum packet length %d" % (self.packet_length, MAXIMUM_PACKET_LENGTH))
            
            if bytes_remaining > 0:
                self.__handle_packet_bytes(received_bytes[bytes_to_read:])

    def __handle_packet_bytes(self, received_bytes: bytes):
        bytes_to_read = min(self.packet_length, len(received_bytes))
        bytes_remaining = len(received_bytes) - bytes_to_read

        self.buffer += received_bytes[:bytes_to_read]
        self.bytes_read += bytes_to_read

        # Packet fully received
        if self.bytes_read >= self.packet_length:

            self.__handle_packet()

            self.state = SessionState.WAITING_FOR_LENGTH
            self.packet_length = 0
            self.bytes_read = 0

            # If there are more bytes to read, treat them as a new packet
            if bytes_remaining > 0:
                self.__handle_length_bytes(received_bytes[bytes_to_read:])

    def on(self, event: str, callback: Callable[[any], any]):
        if event not in self.__listeners:
            self.__listeners[event] = []
        self.__listeners[event].append(callback)

    def __emit(self, event: str, body = None):
        if event in self.__listeners:
            for listener in self.__listeners[event]:
                listener(body)

    def __handle_packet(self):
        opcode = OpCode(struct.unpack("<B", self.buffer[:1])[0])
        body = self.buffer[1:] if len(self.buffer) > 1 else None

        if opcode == OpCode.PLAY:
            self.__emit("play", PlayMessage(**json.loads(body)))
        elif opcode == OpCode.PAUSE:
            self.__emit("pause")
        elif opcode == OpCode.RESUME:
            self.__emit("resume")
        elif opcode == OpCode.STOP:
            self.__emit("stop")
        elif opcode == OpCode.SEEK:
            self.__emit("seek", SeekMessage(**json.loads(body)))
        elif opcode == OpCode.SET_VOLUME:
            self.__emit("set_volume", SetVolumeMessage(**json.loads(body)))
        else:
            raise Exception("Unhandled opcode %s" % opcode)
