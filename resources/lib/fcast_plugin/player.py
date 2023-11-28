import xbmc

from .FCastSession import FCastSession, PlayBackUpdateMessage, PlayBackState
from .util import log_and_notify

from typing import List

class FCastPlayer(xbmc.Player):
    playback_speed: float = 1.0
    sessions: List[FCastSession]
    is_paused: bool = False
    # Used to perform time updates
    prev_time: int = -1

    def __init__(self, sessions: FCastSession):
        self.sessions = sessions
        super().__init__(self)
    
    def doPause(self) -> None:
        if not self.is_paused:
            self.is_paused = True
            self.pause()
    
    def doResume(self) -> None:
        if self.is_paused:
            self.is_paused = False
            self.pause()

    def onAVStarted(self) -> None:
        log_and_notify("Playback started")
        self.is_paused = False
        # Start time loop once the player is active
        self.onPlayBackTimeChanged()

    def onPlayBackStopped(self) -> None:
        self.onPlayBackEnded()

    def onPlayBackPaused(self) -> None:
        self.is_paused = True
        self.onPlayBackTimeChanged()

    def onPlayBackResumed(self) -> None:
        self.is_paused = False
    
    def onPlayBackEnded(self) -> None:
        for session in self.sessions:
            session.send_playback_update(PlayBackUpdateMessage(
                0,
                PlayBackState.IDLE,
            ))
    
    def onPlayBackError(self) -> None:
        self.onPlayBackEnded()
    
    def onPlayBackSpeedChanged(self, speed: int) -> None:
        self.playback_speed = speed
    
    # Not overriden
    def onPlayBackTimeChanged(self) -> None:
        time_int = int(self.getTime())
        self.prev_time = int(self.getTime())
        pb_message = PlayBackUpdateMessage(
            time_int,
            PlayBackState.PAUSED if self.is_paused else PlayBackState.PLAYING,
        )
        for session in self.sessions:
            session.send_playback_update(pb_message)
    
    def addSession(self, session: FCastSession):
        self.sessions.append(session)
    
    def removeSession(self, session: FCastSession):
        self.sessions.remove(session)