"""Playlist playback state.

A sender can hand over a whole queue in a single Play message, by setting the
container to application/json and putting a PlaylistContent in `content` (or
at `url`). The receiver is then expected to walk the queue itself: advancing
when an item finishes, and honouring SetPlaylistItem when a sender jumps
about. This tracks where in the queue we are; main drives it.

Note this is a different mechanism from the one Grayjay uses, which keeps the
queue on the sender and reacts to MediaItemEnd events by sending the next
Play itself. Both have to work.
"""

import json
from typing import List, Optional
from urllib.request import Request, urlopen

from .FCastPackets import (
    MediaItem,
    PlayMessage,
    PlaylistContent,
    play_message_from_media_item,
)
from .FCastSession import message_from_fields
from .util import log

PLAYLIST_CONTAINERS = frozenset(['application/json'])

FETCH_TIMEOUT = 10
MAX_PLAYLIST_BYTES = 4 * 1024 * 1024


def is_playlist(message: PlayMessage) -> bool:
    container = (message.container or '').split(';')[0].strip().lower()
    return container in PLAYLIST_CONTAINERS


def parse_playlist(message: PlayMessage) -> Optional[PlaylistContent]:
    """Read the playlist out of a Play message, inline or by URL."""
    raw = message.content
    if not raw and message.url:
        raw = _fetch(message.url, message.headers)
    if not raw:
        return None

    fields = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    if not isinstance(fields, dict):
        raise ValueError(f"expected a JSON object, got {type(fields).__name__}")

    content = message_from_fields(PlaylistContent, fields)
    content.items = [
        message_from_fields(MediaItem, item)
        for item in (content.items or [])
        if isinstance(item, dict)
    ]
    return content


def _fetch(url: str, headers) -> Optional[str]:
    request = Request(url, headers={str(k): str(v) for k, v in (headers or {}).items()})
    with urlopen(request, timeout=FETCH_TIMEOUT) as response:
        body = response.read(MAX_PLAYLIST_BYTES + 1)

    if len(body) > MAX_PLAYLIST_BYTES:
        raise ValueError(f"playlist exceeds {MAX_PLAYLIST_BYTES} bytes")
    return body.decode('utf-8')


class Playlist:
    """Where we are in a queue the sender handed over."""

    def __init__(self, content: PlaylistContent):
        self.items: List[MediaItem] = [i for i in content.items if i is not None]
        # Apply to items that do not set their own.
        self.volume = content.volume
        self.speed = content.speed
        self.index = self._clamped(int(content.offset or 0))

    def __len__(self) -> int:
        return len(self.items)

    def _clamped(self, index: int) -> int:
        if not self.items:
            return 0
        return max(0, min(index, len(self.items) - 1))

    @property
    def current(self) -> Optional[MediaItem]:
        if 0 <= self.index < len(self.items):
            return self.items[self.index]
        return None

    @property
    def exhausted(self) -> bool:
        return self.current is None

    def advance(self) -> Optional[MediaItem]:
        """Step to the next item. None once the queue is finished."""
        self.index += 1
        return self.current

    def select(self, index: int) -> Optional[MediaItem]:
        """Jump to an item. None if the sender asked for one that is not there."""
        if not (0 <= index < len(self.items)):
            log(f"Ignoring SetPlaylistItem for index {index}, playlist has {len(self.items)}")
            return None
        self.index = index
        return self.current

    def play_message(self) -> Optional[PlayMessage]:
        item = self.current
        if item is None:
            return None
        return play_message_from_media_item(item, self.volume, self.speed)
