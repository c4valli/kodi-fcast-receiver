import sys
import socket
from threading import Thread
from typing import List, Optional
import xbmcgui
import xbmc
import selectors
from urllib.parse import urlparse
from pathlib import Path

from .FCastSession import Event, FCastSession, PlayMessage, PlayBackUpdateMessage, PlayBackState, SeekMessage, SetVolumeMessage, VolumeUpdateMessage
from .FCastHTTPServer import FCastHTTPServer
from .player import FCastPlayer
from .util import log, notify, debounce

session_threads: List[Thread] = []
sessions: List[FCastSession] = []

# Constants
FCAST_HOST = ''
FCAST_PORT = 46899
FCAST_TIMEOUT = 60 * 1000
FCAST_BUFFER_SIZE = 32000

plugin_handle = int(sys.argv[1]) if len(sys.argv) > 1 else None

player_thread: Optional[Thread] = None

# HTTP Server to stream manifest files
http_server: Optional[FCastHTTPServer] = None

# Player needs to be a global so it stays in scope and doesn't get GC'd
player: Optional[FCastPlayer] = None

# Used to queue up seeks
seeks: list[float] = []

def check_player():
    global player
    log("Starting player thread")
    monitor = xbmc.Monitor()
    while not monitor.abortRequested():
        if player and player.isPlaying():
            # Update the current time if it has changed
            if int(player.getTime()) != player.prev_time:
                player.onPlayBackTimeChanged()        
        
        if monitor.waitForAbort(0.05):
            break
    log("Exiting player thread")

def handle_play(session: FCastSession, message = None):
    log(f"Client request play")
    play_item: Optional[xbmcgui.ListItem] = None
    url: str = ""

    if not message:
        return

    if message.url:
        url = message.url
        parsed_url = urlparse(url)

        play_item = xbmcgui.ListItem(path=url)

        # Detect HLS stream
        if Path(parsed_url.path).suffix == '.m3u8':
            log('Detected HLS stream in URL')
            # Use inputstream adaptive to handle HLS stream
            play_item.setContentLookup(False)
            play_item.setMimeType('application/x-mpegURL')
            play_item.setProperty('inputstream', 'inputstream.adaptive')
            play_item.setProperty('inputstream.adaptive.manifest_type', 'hls')
            play_item.setProperty('inputstream.adaptive.stream_selection_type', 'adaptive')
        else:
            log('Detected URL')
            if message.container:
                play_item.setContentLookup(False)
                play_item.setMimeType(message.container)
            else:
                play_item.setContentLookup(True)

    elif message.content:
        if message.container in ['application/dash+xml', 'application/xml+dash']:
            log('Detected DASH stream')

            if http_server:
                http_server.set_content(message.container, message.content)
                url = f'http://{http_server.get_host()}:{http_server.get_port()}/manifest'

                # Basing this off what the YouTube addon does to enable dash
                play_item = xbmcgui.ListItem(path=url)
                play_item.setContentLookup(False)
                play_item.setMimeType(message.container)
                play_item.setProperty('inputstream', 'inputstream.adaptive')
                play_item.setProperty('inputstream.adaptive.manifest_type', 'mpd')
        else:
            notify(f'Unhandled content container {message.container}')

    if player and play_item:
        notify('Starting player ...')
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
        if player:
            player.seekTime(seeks.pop(0))

def handle_seek(session: FCastSession, message = None):
    global player, seeks

    if not message:
        return

    log(f"Client request seek to {message.time}")
    # Send FCastMessage so the client's seek bar position updates better
    session.send_playback_update(PlayBackUpdateMessage(
        message.time,
        PlayBackState.PAUSED if (player and player.is_paused) else PlayBackState.PLAYING,
    ))

    # Append this seek to the seeks "queue"
    seeks.append(float(message.time))
    # Ensure that player.seekTime is called with a low frequency. This prevents Kodi from freezing
    debounce(do_seek, 0.15)()

def handle_stop(session: FCastPlayer, message = None):
    global player
    log(f"Client request stop")
    if player:
        player.stop()

def handle_pause(session: FCastPlayer, message = None):
    global player
    log(f"Client request pause")
    if player:
        player.doPause()

def handle_resume(session: FCastPlayer, message = None):
    global player
    log(f"Client request resume")
    if player:
        player.doResume()

def handle_volume(session: FCastSession, message: SetVolumeMessage):
    global player
    log(f"Client request set volume at {message.volume}")
    volume_level = int(message.volume * 100)
    xbmc.executebuiltin(f'SetVolume({volume_level})')

# Connection handler thread function
def connection_handler(conn: socket.socket, addr):
    global player, http_server

    monitor = xbmc.Monitor()
    notify("Connection from %s" % addr[0])

    session = FCastSession(conn)

    session.on(Event.PLAY, handle_play)
    session.on(Event.STOP, handle_stop)
    session.on(Event.PAUSE, handle_pause)
    session.on(Event.RESUME, handle_resume)
    session.on(Event.SEEK, handle_seek)
    # TODO: Find out how to get/set volume
    # session.on(Event.SET_VOLUME, handle_volume)

    # Allow Kodi to send playback update packets to this client
    if player:
        player.addSession(session)

    # Receive data from the client and process it
    while not monitor.abortRequested():
        try:
            buff = conn.recv(FCAST_BUFFER_SIZE)
            if buff and len(buff) > 0:
                session.process_bytes(buff)
        except BlockingIOError:
            # Normal behavior. Prevents blocking
            pass
        except Exception as e:
            log(str(e), xbmc.LOGERROR)
            break

        if monitor.waitForAbort(0.05):
            break

    if player:
        player.removeSession(session)
    session.close()
    notify("Connection closed from %s" % addr[0])

def main():
    global player, sessions, session_threads, player_thread, http_server

    notify("Starting FCast receiver ...")
    # List of active sessions

    # Create a socket for the FCast receiver
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setblocking(False)
    s.settimeout(FCAST_TIMEOUT / 1000)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    player = FCastPlayer(sessions)
    player_thread = Thread(target=check_player)
    player_thread.start()

    # Create HTTP server to stream manifest files
    http_server = FCastHTTPServer()
    http_server.start()

    try:
        s.bind((FCAST_HOST, FCAST_PORT))
        s.listen()
    except:
        notify("Bind failed", xbmcgui.NOTIFICATION_ERROR)
        s.close()
        exit()

    # Set up event listener that detects for a new socket connection
    selector = selectors.DefaultSelector()
    selector.register(s, selectors.EVENT_READ, data=None)

    notify("Server listening on port %d" % FCAST_PORT, timeout=1000)

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

    http_server.stop()

    notify("Server stopped")
    exit()

if __name__ == '__main__':
    main()
