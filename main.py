import sys
import socket
from threading import Thread, Timer
from typing import List
import xbmcaddon
import xbmcgui
import xbmc
import selectors
from urllib.parse import urlparse
from pathlib import Path
from base64 import b64encode
from FCastSession import Event, FCastSession, PlayMessage, PlayBackUpdateMessage, PlayBackState, SeekMessage, SetVolumeMessage, VolumeUpdateMessage

session_threads: List[Thread] = []
sessions: List[FCastSession] = []
# Constants
FCAST_HOST = ''
FCAST_PORT = 46899
FCAST_TIMEOUT = 60 * 1000
FCAST_BUFFER_SIZE = 32000

# Retrieve Kodi addon information
addon       = xbmcaddon.Addon()
addonname   = addon.getAddonInfo('name')

plugin_handle = int(sys.argv[1]) if len(sys.argv) > 1 else None

player_thread: Thread = None

# Trottle repeated attempts at a function call
def debounce(func, wait):
    def debounced(*args, **kwargs):
        debounced.timer.cancel()
        debounced.timer = Timer(wait, func, args=args, kwargs=kwargs)
        debounced.timer.start()

    debounced.timer = Timer(0, lambda: None)  # Initial dummy timer
    return debounced

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
        log_and_notify(addonname, "Playback started")
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
    
# Player needs to be a global so it stays in scope and doesn't get GC'd
player: FCastPlayer = None

# Used to queue up seeks
seeks: list[float] = []

def check_player():
    global player
    log_and_notify(addonname, "Starting player thread", notify=False)
    monitor = xbmc.Monitor()
    while not monitor.abortRequested():
        if player.isPlaying():
            # Update the current time if it has changed
            if int(player.getTime()) != player.prev_time:
                player.onPlayBackTimeChanged()        
        
        if monitor.waitForAbort(0.05):
            break
    log_and_notify(addonname, "Exiting player thread", notify=False)

# Helper function to both print a message to the Kodi logs and create a notification
def log_and_notify(tag, msg, icon=xbmcgui.NOTIFICATION_INFO, timeout=3000, loglevel=xbmc.LOGDEBUG, notify=True):
    xbmc.log("%s: %s" % (tag, msg), level=loglevel)
    if notify:
        xbmcgui.Dialog().notification(tag, msg, icon, timeout, True)

def handle_play(session: FCastSession, message: PlayMessage):
    log_and_notify(addonname, f"Client request play", notify=False)
    play_item: xbmcgui.ListItem = None
    url: str = ''
    play_item = xbmcgui.ListItem()
    if message.url:
        url = message.url
        parsed_url = urlparse(url)
        # Detect HLS stream
        if Path(parsed_url.path).suffix == '.m3u8':
            log_and_notify(addonname, 'Detected HLS stream', notify=False)
            # Use inputstream adaptive to handle HLS stream
            play_item.setContentLookup(False)
            play_item.setMimeType('application/x-mpegURL')
            play_item.setProperty('inputstream', 'inputstream.adaptive')
            play_item.setProperty('inputstream.adaptive.manifest_type', 'hls')
            play_item.setProperty('inputstream.adaptive.stream_selection_type', 'adaptive')
    elif message.content:
        if message.container in ['application/dash+xml', 'application/xml+dash']:
            # Basing this off what the YouTube addon does to enable dash
            play_item.setContentLookup(False)
            play_item.setMimeType('application/xml+dash')
            play_item.setProperty('inputstream', 'inputstream.adaptive')
            play_item.setProperty('inputstream.adaptive.manifest_type', 'mpd')
            # Use data URLs to avoid having to host the manifest with HTTP
            base64_content = b64encode(message.content.encode('utf-8')).decode('ascii')
            url = f'data:application/xml+dash;base64,{base64_content}'

    if play_item:
        play_item.setPath(url)
        if player.isPlaying():
            player.stop()
        player.play(item=url, listitem=play_item)

def do_seek():
    global player, seeks

    # we are only interested in the last consecutive seek, so we skip the first one if there are more than one
    if len(seeks) > 1:
        seeks.pop(0)
    elif len(seeks) > 0:
        # Last seek in the queue, seek to it
        player.seekTime(seeks.pop(0))

def handle_seek(session: FCastSession, message: SeekMessage):
    global player, seeks
    log_and_notify(addonname, f"Client request seek to {message.time}", notify=False)
    # Send FCastMessage so the client's seek bar position updates better
    session.send_playback_update(PlayBackUpdateMessage(
        message.time,
        PlayBackState.PAUSED if player.is_paused else PlayBackState.PLAYING,
    ))

    # Append this seek to the seeks "queue"
    seeks.append(float(message.time))
    # Ensure that player.seekTime is called with a low frequency. This prevents Kodi from freezing
    debounce(do_seek, 0.15)()

def handle_stop(session: FCastPlayer, message = None):
    global player
    log_and_notify(addonname, f"Client request stop", notify=False)
    player.stop()

def handle_pause(session: FCastPlayer, message = None):
    global player
    log_and_notify(addonname, f"Client request pause", notify=False)
    player.doPause()

def handle_resume(session: FCastPlayer, message = None):
    global player
    log_and_notify(addonname, f"Client request resume", notify=False)
    player.doResume()

def handle_volume(session: FCastSession, message: SetVolumeMessage):
    global player
    log_and_notify(addonname, f"Client request set volume at {message.volume}", notify=False)
    volume_level = int(message.volume * 100)
    xbmc.executebuiltin(f'SetVolume({volume_level})')

# Connection handler thread function
def connection_handler(conn: socket.socket, addr):
    global player

    monitor = xbmc.Monitor()
    log_and_notify(addonname, "Connection from %s" % addr[0])

    session = FCastSession(conn)

    session.on(Event.PLAY, handle_play)
    session.on(Event.STOP, handle_stop)
    session.on(Event.PAUSE, handle_pause)
    session.on(Event.RESUME, handle_resume)
    session.on(Event.SEEK, handle_seek)
    # TODO: Find out how to get/set volume
    # session.on(Event.SET_VOLUME, handle_volume)

    # Allow Kodi to send playback update packets to this client
    player.addSession(session)

    # Receive data from the client and process it
    while not monitor.abortRequested():
        try:
            buff = conn.recv(FCAST_BUFFER_SIZE)
            if not buff or len(buff) <= 0:
                break
            session.process_bytes(buff)
        except BlockingIOError:
            # Normal behavior. Prevents blocking
            pass
        except Exception:
            break

        if monitor.waitForAbort(0.05):
            break

    player.removeSession(session)
    session.close()
    log_and_notify(addonname, "Connection closed from %s" % addr[0])

def main():
    global player, sessions, session_threads, player_thread

    log_and_notify(addonname, "Starting FCast receiver ...")
    # List of active sessions

    # Create a socket for the FCast receiver
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setblocking(False)
    s.settimeout(FCAST_TIMEOUT / 1000)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    player = FCastPlayer(sessions)
    player_thread = Thread(target=check_player)
    player_thread.start()

    try:
        s.bind((FCAST_HOST, FCAST_PORT))
        s.listen()
    except:
        log_and_notify(addonname, "Bind failed", xbmcgui.NOTIFICATION_ERROR)
        s.close()
        exit()

    # Set up event listener that detects for a new socket connection
    selector = selectors.DefaultSelector()
    selector.register(s, selectors.EVENT_READ, data=None)

    log_and_notify(addonname, "Server listening on port %d" % FCAST_PORT, timeout=1000)

    monitor = xbmc.Monitor()
    # Loop for new connections
    while not monitor.abortRequested():
        events = selector.select(timeout=0)

        # Check for connections
        for key, mask in events:
            if key.data is None:
                conn, addr = s.accept()
                conn.setblocking(False)
                # Create a new thread for the connection
                t = Thread(target=connection_handler, args=(conn, addr))
                session_threads.append(t)
                t.start()

        # Remove dead threads from sessions list on every timeout or other exception
        session_threads = [t for t in session_threads if t.is_alive()]

        if monitor.waitForAbort(0.250):
            break

    s.close()

    log_and_notify(addonname, "Server stopped")
    exit()

if __name__ == '__main__':
    main()
