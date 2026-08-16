"""Fetch a picture to local storage before it goes on screen.

Kodi's picture viewer resets the slide the moment it is told to show
something new (GUI_MSG_SHOW_PICTURE does Reset, Add, RunSlideShow), so the
picture already up disappears before the next one has arrived and the screen
is black for as long as the download takes. Fetching it here first means the
picture on screen stays until the next is ready to draw.

The file is written with the extension its own bytes call for, and that is not
cosmetic: Kodi chooses the image decoder from the extension, turning it into a
mime type in ImageFactory::CreateLoader. A cached file with the wrong
extension - or none - decodes as nothing and shows as a black screen, which is
what an earlier attempt at this did. Bytes we cannot identify are not cached
at all; the URL goes to Kodi instead, exactly as before.
"""

import hashlib
import os
import time
from typing import Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import xbmc
import xbmcvfs

from .util import log

CACHE_DIR = 'special://temp/service.fcast.receiver/'

FETCH_TIMEOUT = 20
# Enough for a phone camera's full-size photo, and a ceiling on what a sender
# can make us write to a device whose storage may be a memory card.
MAX_BYTES = 64 * 1024 * 1024
CHUNK = 64 * 1024

# What is kept for next time. A picture is named after its URL, so stepping
# back through a slideshow, or casting the same photo twice, costs nothing.
# The oldest go first when either limit is reached.
MAX_FILES = 12
MAX_TOTAL_BYTES = 256 * 1024 * 1024

# What the first bytes of a picture look like. Trusting these rather than the
# container the sender declared also rejects the other thing a URL can return
# with a 200: an HTML error page, which would otherwise be cached as a picture
# and displayed as a black screen.
SIGNATURES = (
    (b'\xff\xd8\xff', '.jpg'),
    (b'\x89PNG\r\n\x1a\n', '.png'),
    (b'GIF87a', '.gif'),
    (b'GIF89a', '.gif'),
    (b'BM', '.bmp'),
    (b'\x00\x00\x01\x00', '.ico'),
)


def extension_for(head: bytes) -> Optional[str]:
    """The extension these bytes call for, or None if they are not a picture."""
    for signature, extension in SIGNATURES:
        if head.startswith(signature):
            return extension

    # Both are RIFF containers, told apart by the tag at byte 8.
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return '.webp'
    # ISO base media, which is how AVIF and HEIC arrive.
    if head[4:8] == b'ftyp':
        brand = head[8:12]
        if brand in (b'avif', b'avis'):
            return '.avif'
        if brand in (b'heic', b'heix', b'mif1', b'msf1'):
            return '.heic'
    return None


def directory() -> str:
    path = xbmcvfs.translatePath(CACHE_DIR)
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
    return path


def key_for(url: str) -> str:
    """The cache's name for a URL.

    Hashed rather than taken from the URL's own filename: two photos are very
    often both called image.jpg, and one would then be served for the other.
    """
    return hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]


def touch(path: str) -> None:
    """Mark a picture as the most recently used, so it is the last to go.

    The timestamp is written explicitly rather than left to the filesystem.
    A file's own modification time comes from the kernel's coarse clock, which
    does not move within a millisecond, and two pictures handled that close
    together would then be indistinguishable to the cull below.
    """
    try:
        stamp = time.time_ns()
        os.utime(path, ns=(stamp, stamp))
    except OSError as e:
        log(f"Could not touch {path}: {e}", xbmc.LOGWARNING)


def cached(url: str) -> Optional[str]:
    """The picture already on disk for this URL, if there is one."""
    key = key_for(url)
    try:
        for entry in os.listdir(directory()):
            if entry.startswith(key + '.'):
                path = os.path.join(directory(), entry)
                if os.path.getsize(path) > 0:
                    touch(path)
                    return path
    except OSError as e:
        log(f"Could not look in the picture cache: {e}", xbmc.LOGWARNING)
    return None


def fetch(url: str, headers=None) -> Optional[str]:
    """Return a local copy of a picture, downloading it if it is not cached.

    None is not a failure the caller needs to report: it means show the URL
    the way we always have and let Kodi do the fetching.
    """
    existing = cached(url)
    if existing:
        log(f"Already have {url} as {existing}")
        return existing

    path = None
    try:
        request = Request(url, headers={str(k): str(v)
                                        for k, v in (headers or {}).items()})
        with urlopen(request, timeout=FETCH_TIMEOUT) as response:
            head = response.read(32)
            extension = extension_for(head)
            if extension is None:
                log(f"Not caching {url}: {head[:8]!r} is not a picture we know", xbmc.LOGINFO)
                return None

            # Downloaded under a part name and moved into place, so a
            # transfer that fails halfway cannot leave something that looks
            # cached and shows as a torn picture.
            final = os.path.join(directory(), key_for(url) + extension)
            # Named so that a half-written file cannot be picked up as a
            # cached picture: cached() matches on the key, this does not.
            path = os.path.join(directory(), 'part-' + key_for(url) + extension)
            written = len(head)
            with open(path, 'wb') as picture:
                picture.write(head)
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_BYTES:
                        raise ValueError(f"picture exceeds {MAX_BYTES} bytes")
                    picture.write(chunk)

        os.replace(path, final)
        touch(final)
        log(f"Cached {url} as {final} ({written} bytes)")
        prune(keep=final)
        return final
    except HTTPError as e:
        # An HTTPError is itself the response, holding the socket, so it has
        # to be closed rather than just logged.
        log(f"Could not cache {url}: {e}", xbmc.LOGWARNING)
        e.close()
        remove(path)
        return None
    except Exception as e:
        log(f"Could not cache {url}: {e}", xbmc.LOGWARNING)
        remove(path)
        return None


def remove(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def contents():
    """Cached pictures, least recently used first."""
    path = directory()
    files = []
    for entry in os.listdir(path):
        entry = os.path.join(path, entry)
        try:
            stat = os.stat(entry)
        except OSError:
            continue
        files.append((stat.st_mtime_ns, stat.st_size, entry))
    return sorted(files)


def prune(keep: Optional[str] = None) -> None:
    """Drop the oldest pictures until the cache is back inside its limits.

    Run after every download rather than at some interval, so the ceiling
    holds even if the add-on never gets to shut down tidily.
    """
    try:
        files = contents()
        count = len(files)
        total = sum(size for _, size, _ in files)

        for _, size, entry in files:
            if count <= MAX_FILES and total <= MAX_TOTAL_BYTES:
                break
            if entry == keep:
                continue
            remove(entry)
            count -= 1
            total -= size
    except Exception as e:
        log(f"Could not prune the picture cache: {e}", xbmc.LOGWARNING)


def clear() -> None:
    """Throw away every cached picture."""
    try:
        for _, _, entry in contents():
            remove(entry)
    except Exception as e:
        log(f"Could not clear the picture cache: {e}", xbmc.LOGWARNING)
