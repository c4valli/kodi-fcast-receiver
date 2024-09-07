from datetime import datetime, timezone
from enum import Enum
from typing import Optional

class PlayBackState(int, Enum):
    IDLE = 0
    PLAYING = 1
    PAUSED = 2

class PlayMessage:
    def __init__(self,
        container: str,
        url: Optional[str] = None,
        time: Optional[float] = None,
        content: Optional[str] = None,
        speed: float = 1.0,
        headers = None
    ) -> None:
        self.container = container
        self.url = url
        self.content = content
        self.time = time
        self.speed = speed
        self.headers = headers

class SeekMessage:
    def __init__(self, time: float) -> None:
        self.time = time

class PlayBackUpdateMessage:
    def __init__(self,
        time: float,
        state: PlayBackState,
        speed: float = 1.0,
        duration: Optional[float] = None,
        generationTime: Optional[int] = None
    ) -> None:
        self.time = time
        self.duration = duration
        self.speed = speed
        self.state = state
        self.generationTime = generationTime if generationTime else datetime.now(timezone.utc).timestamp() * 1000

class VolumeUpdateMessage:
    def __init__(self,
        volume: float,
        generationTime: Optional[float] = None
    ) -> None:
        self.volume = volume
        self.generationTime = generationTime if generationTime else datetime.now(timezone.utc).timestamp() * 1000

class SetVolumeMessage:
    def __init__(self, volume: float) -> None:
        self.volume = volume

class SetSpeedMessage:
    def __init__(self, speed: float = 1.0) -> None:
        self.speed = speed

class PlaybackErrorMessage:
    def __init__(self, message: str) -> None:
        self.message = message
