"""Display cast images through Kodi's picture viewer.

Images do not go through xbmc.Player, so none of its callbacks fire for them
and playback state has to be tracked here instead. Senders still expect the
usual Playing/Idle reporting, which is what the callbacks here are for.

Previously an image was handed to the video player, which treated it as a
broken stream: it appeared for a few milliseconds and the player closed again.
"""

import time

import xbmc
import xbmcgui

from .util import log

# From xbmc/guilib/WindowIDs.h. Note it is 12007, not the 12005 used for
# fullscreen video.
WINDOW_SLIDESHOW = 12007

# ShowPicture is asynchronous, so the window is not up the instant it returns.
# Allow this long before concluding it failed to open.
OPEN_TIMEOUT = 5.0


class ImageViewer:
    """Shows one image at a time and reports when it goes away."""

    def __init__(self, on_closed=None, on_expired=None):
        self._showing = False
        self._confirmed = False
        self._opened_at = 0.0
        self._on_closed = on_closed
        # Called when a picture has been up for as long as it was meant to be.
        # Only playlists set a duration, so only they ever reach this.
        self._on_expired = on_expired
        self._duration = 0.0
        self._expires_at = 0.0
        self._remaining = 0.0
        self._paused = False
        # Whether the countdown has begun. It does not begin until the picture
        # is up, so this is not the same question as "is there a duration".
        self._started = False

    @property
    def is_showing(self) -> bool:
        return self._showing

    @property
    def is_on_screen(self) -> bool:
        return self._on_screen()

    @property
    def seconds_left(self) -> float:
        """Time left on the countdown, or 0 when nothing is counting down."""
        if self._paused:
            return self._remaining
        if not self._expires_at:
            return 0.0
        return max(0.0, self._expires_at - time.time())

    def show(self, url: str, duration: float = 0.0) -> None:
        # Deliberately no close() first. ShowPicture delivers
        # GUI_MSG_SHOW_PICTURE straight to the slideshow window, so calling it
        # again while the viewer is open swaps the picture in place. Closing
        # and reopening drops back to the UI behind for a frame, which is what
        # made changing image to image flash dark.
        already_open = self._showing and self._on_screen()

        log(f"Showing image {url}" + (f" for {duration}s" if duration else ""))
        # Kodi's own picture viewer, so scaling, rotation and the background
        # all behave the way they do for local pictures, in any skin.
        xbmc.executebuiltin(f'ShowPicture({url})')

        self._showing = True
        self._duration = max(0.0, float(duration or 0.0))
        self._remaining = 0.0
        self._paused = False
        # The countdown starts when the picture is actually up, which for a
        # picture opening from cold is the first poll that finds the window -
        # ShowPicture returns long before Kodi has fetched anything. Swapping
        # in place has no such wait, so that one starts here.
        self._expires_at = time.time() + self._duration if (already_open and self._duration) else 0.0
        self._started = self._expires_at > 0.0

        if not already_open:
            self._confirmed = False
            self._opened_at = time.time()

    def restart_countdown(self, duration: float) -> None:
        """Give the picture on screen its time again, without redrawing it.

        For a sender that re-sends the picture already up: there is nothing
        to redraw, but a playlist that holds the same photo twice in a row
        would otherwise sit on it for good, with no countdown to move it on.
        """
        if not self._showing:
            return

        self._duration = max(0.0, float(duration or 0.0))
        self._paused = False
        self._remaining = 0.0
        # If it is not on screen yet the poll arms this, exactly as it would
        # for a picture that had just been asked for.
        self._expires_at = (time.time() + self._duration
                            if self._confirmed and self._duration else 0.0)
        self._started = self._expires_at > 0.0

    def close(self) -> bool:
        """Dismiss the picture viewer. Returns True if there was one to dismiss.

        ACTION_STOP, not Back. Stop is what Kodi binds to X by default, which
        is the action that actually exits the picture viewer, and it is safe
        to send when nothing is showing - it stops playback that is not
        happening. Back would navigate whatever is focused instead.

        Deliberately acts on either our own state or the window actually being
        up, so a sender's Stop still works if the two have drifted apart.
        """
        on_screen = self._on_screen()
        if not self._showing and not on_screen:
            return False

        log(f"Closing image viewer (tracked={self._showing}, on screen={on_screen})")
        xbmc.executebuiltin('Action(Stop)')

        self._showing = False
        self._confirmed = False
        self._clear_countdown()
        return True

    def pause(self) -> bool:
        """Hold the countdown. Returns True if there was a picture to pause.

        A picture has no player behind it, so a sender's Pause has to be
        answered here. False means the sender meant something else, and the
        caller should let the player deal with it.
        """
        if not self._showing:
            return False

        if not self._paused:
            # A sender can pause before Kodi has even put the picture up, and
            # the countdown has not begun by then: what is held in that case
            # is the whole duration rather than nothing.
            self._remaining = self.seconds_left if self._started else self._duration
            self._expires_at = 0.0
            self._paused = True
            log(f"Picture paused with {self._remaining:.1f}s left")
        return True

    def resume(self) -> bool:
        """Start the countdown again from where pause left it."""
        if not self._showing:
            return False

        if self._paused:
            self._paused = False
            # A picture with no duration was never counting down; it just
            # stays up, and resuming it is not supposed to start a clock.
            self._expires_at = time.time() + self._remaining if self._remaining else 0.0
            self._started = self._started or self._expires_at > 0.0
            self._remaining = 0.0
        return True

    def poll(self) -> None:
        """Notice the viewer being dismissed from the Kodi UI, and time it out."""
        if not self._showing:
            return

        if self._on_screen():
            if not self._confirmed:
                self._confirmed = True
                # The picture is up: this is where its time on screen starts.
                if self._duration and not self._paused:
                    self._expires_at = time.time() + self._duration
                    self._started = True
            self._check_expiry()
            return

        if not self._confirmed:
            # Still opening, or it never opened at all.
            if time.time() - self._opened_at < OPEN_TIMEOUT:
                return
            log("Image viewer did not open", xbmc.LOGWARNING)

        self._showing = False
        self._confirmed = False
        self._clear_countdown()
        if self._on_closed:
            self._on_closed()

    def _check_expiry(self) -> None:
        """Hand over once the picture has had its time.

        The deadline is cleared before the callback runs, because that
        callback is what shows the next picture: it calls back into show(),
        which sets the next deadline, and clearing afterwards would wipe it.
        """
        if not self._expires_at or time.time() < self._expires_at:
            return

        self._expires_at = 0.0
        if self._on_expired:
            self._on_expired()

    def _clear_countdown(self) -> None:
        self._duration = 0.0
        self._expires_at = 0.0
        self._remaining = 0.0
        self._paused = False
        self._started = False

    def _on_screen(self) -> bool:
        """Whether Kodi's picture viewer is currently up.

        Checks the dialog id as well as the window id. CGUIWindowSlideShow
        derives from CGUIDialog despite its name and its 12xxx id, so it is
        the *dialog* that reports 12007 and getCurrentWindowId() returns
        whatever is underneath. Asking only the window meant this was always
        False: the poll then decided the viewer had failed to open, told
        senders playback was idle five seconds in, and left close() with
        nothing to act on when a sender asked to stop.
        """
        try:
            return WINDOW_SLIDESHOW in (
                xbmcgui.getCurrentWindowId(),
                xbmcgui.getCurrentWindowDialogId(),
            )
        except Exception:
            return False
