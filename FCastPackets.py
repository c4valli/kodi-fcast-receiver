from enum import Enum

class PlayBackState(Enum):
    IDLE = 0
    PLAYING = 1
    PAUSED = 2

class PlayMessage:
    def __init__(self, container: str, url: str, content: str, time: int) -> None:
        self.container = container
        self.url = url
        self.content = content
        self.time = time

class SeekMessage:
    def __init__(self, time: int) -> None:
        self.time = time

class PlayBackUpdateMessage:
    def __init__(self, time: int, state: PlayBackState) -> None:
        self.time = time
        self.state = state

class VolumeUpdateMessage:
    def __init__(self, volume: float) -> None:
        self.volume = volume

class SetVolumeMessage:
    def __init__(self, volume: float) -> None:
        self.volume = volume