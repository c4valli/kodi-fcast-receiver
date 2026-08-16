import xbmc

from .FCastSession import (FCastSession, PlayBackUpdateMessage, PlayBackState, PlayMessage,
                           PlaybackErrorMessage, EventType, OpCode, media_item_from_play_message)
from .util import log

from typing import List

class FCastPlayer(xbmc.Player):
    playback_speed: float = 1.0
    sessions: List[FCastSession]
    is_paused: bool = False
    # Used to perform time updates
    prev_time: int = -1
    get_play_data = None
    on_media_ended = None
    get_item_index = None
    # Fallback only. main assigns the sender's PlayMessage.time to the instance
    # before every cast, which shadows this. It matters because Kodi calls our
    # callbacks for anything it plays, including playback we did not start, and
    # onAVStarted would otherwise raise AttributeError.
    start_time: float = 0.0
    # Whether the playback Kodi is reporting on is ours. It calls these
    # callbacks for everything it plays, so without this the add-on acts on
    # the user's own music: every track end triggered a PlayerControl(Stop)
    # that killed the track Kodi had just started, cutting an album to
    # seconds per song.
    owns_playback: bool = False

    def __init__(self, sessions: List[FCastSession], get_play_data=None,
                 on_media_ended=None, get_item_index=None):
        self.sessions = sessions
        # Supplies the PlayMessage currently on screen, for the item field of
        # media events. Set by main, which owns that state.
        self.get_play_data = get_play_data
        # Asked when an item finishes: returns True if something else has been
        # started, which means playback is not over after all.
        self.on_media_ended = on_media_ended
        # Playlist position to report in PlaybackUpdate, or None.
        self.get_item_index = get_item_index
        super().__init__()

    def doPause(self) -> None:
        if not self.is_paused:
            self.is_paused = True
            self.pause()

    def doResume(self) -> None:
        if self.is_paused:
            self.is_paused = False
            self.pause()

    def onAVStarted(self) -> None:
        if not self.owns_playback:
            return
        log("Playback started")
        self.is_paused = False
        if self.start_time > 0.0:
            log(f"Seeking to start time {self.start_time}")
            try:
                self.seekTime(self.start_time)
            except Exception as e:
                log(f"Could not seek to start time: {e}")
            self.start_time = 0.0
        self.onPlayBackTimeChanged()

    # These three callbacks used to force PlayerControl(Stop) and send an
    # OpCode.STOP back to the sender. That was a workaround for the player
    # hanging and taking Kodi's UI with it, and it did make the player quit
    # cleanly - but it also cancels the remaining items of a queued playlist,
    # which is why playlists cannot work while it is in place.
    #
    # The old lines are left commented at each site deliberately. Several
    # likely causes of those hangs have since been fixed (packet reassembly
    # corrupting the stream, and leaked connection threads that kept dead
    # sessions in self.sessions while this ran at 20Hz), but that has not been
    # confirmed on a device. If freezes reappear, restore these first.

    def onPlayBackStopped(self) -> None:
        if not self.owns_playback:
            return
        self.owns_playback = False
        # Playback was ended deliberately, by a sender or from the Kodi UI.
        # Nothing further should start, so a queued playlist is abandoned here
        # rather than advanced.
        # xbmc.executebuiltin('PlayerControl(Stop)')
        self.report_idle()

    def onPlayBackPaused(self) -> None:
        if not self.owns_playback:
            return
        self.is_paused = True
        self.onPlayBackTimeChanged()

    def onPlayBackResumed(self) -> None:
        if not self.owns_playback:
            return
        self.is_paused = False

    def onPlayBackEnded(self) -> None:
        if not self.owns_playback:
            return
        # Released before reporting: if a playlist advances from here, main
        # claims ownership again for the item it starts.
        self.owns_playback = False
        # The item played to its end on its own.
        # xbmc.executebuiltin('PlayerControl(Stop)')
        self.report_media_item_end()

    def onPlayBackError(self) -> None:
        if not self.owns_playback:
            return
        self.owns_playback = False
        # xbmc.executebuiltin('PlayerControl(Stop)')
        for session in list(self.sessions):
            session.send_playback_error(PlaybackErrorMessage("Playback failed"))
        self.report_idle()

    def report_media_item_end(self) -> None:
        """Signal that the current item finished playing on its own.

        Senders drive their own queue from this: Grayjay answers a
        MediaItemEnd event with the Play message for the next video. The
        official receiver sends *only* the event here, with no preceding
        PlaybackUpdate(Idle) - a sender that is told the receiver went idle
        stands its queue down and never acts on the event that follows.

        So Idle is reserved for senders that cannot receive the event at all.
        """
        self.is_paused = False
        item = media_item_from_play_message(
            self.get_play_data() if self.get_play_data else None,
            time=max(self.prev_time, 0),
        )

        unreached = []
        for session in list(self.sessions):
            if not session.send_media_event(EventType.MEDIA_ITEM_END, item):
                unreached.append(session)

        # A playlist carries on from here, in which case playback is not over
        # and reporting Idle would tell senders it was.
        if self.on_media_ended and self.on_media_ended():
            self.prev_time = -1
            return

        if unreached:
            self.report_idle(sessions=unreached)
        else:
            self.prev_time = -1

    def report_idle(self, sessions=None) -> None:
        """Tell every sender that playback is over.

        Idle is the only state available for this: FCast defines just Idle,
        Playing and Paused, with no separate stopped state. The previous
        `session.sendOpCode(OpCode.STOP)` did the job by accident - Stop is
        defined as sender-to-receiver only, so what a sender made of receiving
        one was undefined.

        Built from the last known position, because getTime() and
        getTotalTime() raise once the player has torn down.
        """
        self.is_paused = False
        message = PlayBackUpdateMessage(
            max(self.prev_time, 0),
            PlayBackState.IDLE,
            speed=self.playback_speed,
        )
        for session in list(self.sessions if sessions is None else sessions):
            session.send_playback_update(message)
        self.prev_time = -1

    def onPlayBackSpeedChanged(self, speed: float) -> None:
        if not self.owns_playback:
            return
        self.playback_speed = speed

    def onPlayBackTimeChanged(self) -> None:
        if not self.owns_playback:
            return
        # getTime() raises "Kodi is not playing any media file" rather than
        # returning anything when the player has moved on. Kodi calls these
        # callbacks for everything it plays, not only what we cast, and with
        # gapless audio onAVStarted can arrive after playback has already
        # advanced past the item it was announcing.
        try:
            self.prev_time = int(self.getTime())
            duration = int(self.getTotalTime())
        except Exception as e:
            log(f"Skipping playback update, player is not ready: {e}")
            return
        pb_message = PlayBackUpdateMessage(
            self.prev_time,
            PlayBackState.PAUSED if self.is_paused else PlayBackState.PLAYING,
            speed=self.playback_speed,
            duration=duration,
            itemIndex=self.get_item_index() if self.get_item_index else None
        )
        for session in self.sessions:
            session.send_playback_update(pb_message)

    def addSession(self, session: FCastSession):
        self.sessions.append(session)

    def removeSession(self, session: FCastSession):
        self.sessions.remove(session)