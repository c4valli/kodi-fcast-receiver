"""What the user has chosen in the add-on's settings dialog.

Reads never raise. Kodi throws when a setting is missing or has changed type,
which happens on a partial deploy and on any upgrade from a version that
shipped without resources/settings.xml - and an exception here would take down
a service add-on that was otherwise fine. Every read falls back to the default
declared alongside it instead.

The ids below must match resources/settings.xml. The tests check that they do,
because a name that is not in the file simply reads as its default, forever
and silently.
"""

import xbmcaddon

SHOW_NOTIFICATIONS = 'show_notifications'
PLAYLIST_IMAGE_DURATION = 'playlist_image_duration'
PLAYLIST_IMAGE_DURATION_OVERRIDE = 'playlist_image_duration_override'
PRELOAD_IMAGES = 'preload_images'
KEEP_AWAKE_FOR_PICTURES = 'keep_awake_for_pictures'

# Also what settings.xml declares as each setting's <default>.
DEFAULTS = {
    SHOW_NOTIFICATIONS: True,
    PLAYLIST_IMAGE_DURATION: 10,
    PLAYLIST_IMAGE_DURATION_OVERRIDE: False,
    PRELOAD_IMAGES: True,
    KEEP_AWAKE_FOR_PICTURES: True,
}


def _log(message: str) -> None:
    # Imported here rather than at the top: util asks this module whether
    # notifications are wanted, so importing it there would be circular.
    from .util import log
    log(message)


def _read(key: str, getter: str):
    try:
        return getattr(xbmcaddon.Addon(), getter)(key)
    except Exception as e:
        _log(f"Setting {key} unavailable ({e}), using {DEFAULTS[key]!r}")
        return DEFAULTS[key]


# A fresh Addon() per read. This add-on runs as a service for as long as Kodi
# does, and a long-lived instance has been known to keep serving the values it
# was created with, so a change made in the settings dialog would not take
# effect until Kodi restarted. Reads are rare enough for this to cost nothing.
def get_bool(key: str) -> bool:
    return bool(_read(key, 'getSettingBool'))


def get_int(key: str) -> int:
    return int(_read(key, 'getSettingInt'))


def notifications_enabled() -> bool:
    return get_bool(SHOW_NOTIFICATIONS)


def image_duration() -> float:
    """Seconds to leave a playlist picture up. 0 means wait for the sender."""
    return float(max(0, get_int(PLAYLIST_IMAGE_DURATION)))


def image_duration_overrides_sender() -> bool:
    """Whether the user's duration wins over the one the sender asked for."""
    return get_bool(PLAYLIST_IMAGE_DURATION_OVERRIDE)


def preload_images() -> bool:
    """Whether to download a picture before putting it on screen."""
    return get_bool(PRELOAD_IMAGES)


def keep_awake_for_pictures() -> bool:
    """Whether a picture on screen should count as the box being in use."""
    return get_bool(KEEP_AWAKE_FOR_PICTURES)
