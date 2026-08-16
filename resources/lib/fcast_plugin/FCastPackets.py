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
        volume: Optional[float] = None,
        headers = None,
        metadata = None
    ) -> None:
        self.container = container
        self.url = url
        self.content = content
        self.time = time
        self.speed = speed
        self.volume = volume
        self.headers = headers
        self.metadata = metadata

class SeekMessage:
    def __init__(self, time: int) -> None:
        self.time = time

class PlayBackUpdateMessage:
    def __init__(self,
        time: int,
        state: PlayBackState,
        speed: float = 1.0,
        duration: Optional[float] = None,
        generationTime: Optional[int] = None,
        itemIndex: Optional[int] = None
    ) -> None:
        self.time = time
        self.duration = duration
        self.speed = speed
        self.state = state
        self.itemIndex = itemIndex
        self.generationTime = generationTime if generationTime else int(datetime.now(timezone.utc).timestamp() * 1000)

class VolumeUpdateMessage:
    def __init__(self,
        volume: float,
        generationTime: Optional[int] = None
    ) -> None:
        self.volume = volume
        self.generationTime = generationTime if generationTime else int(datetime.now(timezone.utc).timestamp() * 1000)

class SetVolumeMessage:
    def __init__(self, volume: float) -> None:
        self.volume = volume

class SetSpeedMessage:
    def __init__(self, speed: float = 1.0) -> None:
        self.speed = speed

class PlaybackErrorMessage:
    def __init__(self, message: str) -> None:
        self.message = message

class VersionMessage:
    def __init__(self, version: int) -> None:
        self.version = version

# --- Protocol v3 ------------------------------------------------------------

class InitialSenderMessage:
    def __init__(self,
        displayName: Optional[str] = None,
        appName: Optional[str] = None,
        appVersion: Optional[str] = None
    ) -> None:
        self.displayName = displayName
        self.appName = appName
        self.appVersion = appVersion

class InitialReceiverMessage:
    def __init__(self,
        displayName: Optional[str] = None,
        appName: Optional[str] = None,
        appVersion: Optional[str] = None,
        playData: Optional[PlayMessage] = None
    ) -> None:
        self.displayName = displayName
        self.appName = appName
        self.appVersion = appVersion
        self.playData = playData

class PlayUpdateMessage:
    def __init__(self,
        playData: Optional[PlayMessage] = None,
        generationTime: Optional[int] = None
    ) -> None:
        self.playData = playData
        self.generationTime = generationTime if generationTime else int(datetime.now(timezone.utc).timestamp() * 1000)

class SetPlaylistItemMessage:
    def __init__(self, itemIndex: int) -> None:
        self.itemIndex = itemIndex

class EventType(int, Enum):
    MEDIA_ITEM_START = 0
    MEDIA_ITEM_END = 1
    MEDIA_ITEM_CHANGE = 2
    KEY_DOWN = 3
    KEY_UP = 4

class MediaItem:
    """One entry of playable content, as carried inside an event."""

    def __init__(self,
        container: str,
        url: Optional[str] = None,
        content: Optional[str] = None,
        time: Optional[float] = None,
        volume: Optional[float] = None,
        speed: Optional[float] = None,
        cache: Optional[bool] = None,
        showDuration: Optional[float] = None,
        headers = None,
        metadata = None
    ) -> None:
        self.container = container
        self.url = url
        self.content = content
        self.time = time
        self.volume = volume
        self.speed = speed
        self.cache = cache
        self.showDuration = showDuration
        self.headers = headers
        self.metadata = metadata

class ContentType(int, Enum):
    PLAYLIST = 0

class PlaylistContent:
    """The body of a Play whose container is application/json.

    Senders hand the receiver a whole queue this way and expect it to walk
    through the items itself, rather than sending each one in turn.
    """

    def __init__(self,
        items = None,
        offset: Optional[int] = None,
        volume: Optional[float] = None,
        speed: Optional[float] = None,
        forwardCache: Optional[int] = None,
        backwardCache: Optional[int] = None,
        metadata = None,
        contentType: Optional[int] = None
    ) -> None:
        self.items = items or []
        self.offset = offset
        self.volume = volume
        self.speed = speed
        self.forwardCache = forwardCache
        self.backwardCache = backwardCache
        self.metadata = metadata
        self.contentType = contentType

def play_message_from_media_item(
    item: MediaItem, volume: Optional[float] = None, speed: Optional[float] = None
) -> PlayMessage:
    """Turn a playlist entry into the Play request the rest of the code takes.

    Playlist-wide volume and speed apply to items that do not set their own.
    """
    return PlayMessage(
        container=item.container,
        url=item.url,
        content=item.content,
        time=item.time,
        volume=item.volume if item.volume is not None else volume,
        speed=item.speed if item.speed is not None else speed,
        headers=item.headers,
        metadata=item.metadata,
    )

class MediaItemEvent:
    def __init__(self, type: int, item: Optional[MediaItem] = None) -> None:
        self.type = type
        self.item = item

class EventMessage:
    def __init__(self, event, generationTime: Optional[int] = None) -> None:
        self.event = event
        self.generationTime = generationTime if generationTime else int(datetime.now(timezone.utc).timestamp() * 1000)

class SubscribeEventMessage:
    def __init__(self, event = None) -> None:
        self.event = event

class UnsubscribeEventMessage:
    def __init__(self, event = None) -> None:
        self.event = event

def media_item_from_play_message(
    message: Optional[PlayMessage], time: Optional[float] = None
) -> Optional[MediaItem]:
    """Describe what was playing, for the item field of a media event.

    Senders match this against their own queue entry to work out which item
    finished, so it mirrors the fields they sent us. `content` is left out for
    the same reason it is stripped from PlayUpdate: an inline manifest would
    blow the 32KB packet ceiling.
    """
    if message is None:
        return None

    return MediaItem(
        container=message.container,
        url=message.url,
        time=time if time is not None else message.time,
        volume=message.volume,
        speed=message.speed,
        headers=message.headers,
        metadata=message.metadata,
    )

def summarize_play_message(message: Optional[PlayMessage]) -> Optional[PlayMessage]:
    """Copy a PlayMessage for echoing back to senders, without its content.

    `content` carries an entire inline manifest. Echoing it in Initial or
    PlayUpdate can push the packet past the protocol's 32KB ceiling, and the
    sender already knows what it sent us. The URL and container are what the
    other senders actually need to display what is playing.
    """
    if message is None:
        return None

    return PlayMessage(
        container=message.container,
        url=message.url,
        time=message.time,
        speed=message.speed,
        volume=message.volume,
        headers=message.headers,
        metadata=message.metadata,
    )
