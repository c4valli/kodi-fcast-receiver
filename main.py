import socket
import threading
from typing import List
import xbmcaddon
import xbmcgui
import xbmc

from FCastPackets import *
from FCastSession import Event, FCastSession

# Helper function to both print a message to the Kodi logs and create a notification
def log_and_notify(tag, msg, icon=xbmcgui.NOTIFICATION_INFO, timeout=3000, loglevel=xbmc.LOGDEBUG):
    xbmc.log("%s: %s" % (tag, msg), level=loglevel)
    xbmcgui.Dialog().notification(tag, msg, icon, timeout, True)

# Constants
FCAST_HOST = ''
FCAST_PORT = 46899
FCAST_TIMEOUT = 60 * 1000
FCAST_BUFFER_SIZE = 4096

# Retrieve Kodi addon information
addon       = xbmcaddon.Addon()
addonname   = addon.getAddonInfo('name')

xbmc.log("Starting server ...")

# Create a socket for the FCast receiver
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(FCAST_TIMEOUT / 1000)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

try:
    s.bind((FCAST_HOST, FCAST_PORT))
    s.listen()
except:
    log_and_notify(addonname, "Bind failed", xbmcgui.NOTIFICATION_ERROR)
    s.close()
    exit()

sessions: List[threading.Thread] = []

log_and_notify(addonname, "Server listening on port %d" % FCAST_PORT, timeout=1000)
log_and_notify(addonname, "Waiting %d seconds for a connection ..." % (FCAST_TIMEOUT / 1000), timeout=FCAST_TIMEOUT)

def connection_handler(conn, addr):
    global sessions
    log_and_notify(addonname, "Connection from %s" % addr[0])

    session = FCastSession(conn)

    # TODO: Add event handlers
    #session.on(Event.PLAY, handle_play)
    #session.on(Event.PLAY, handle_stop)

    while True:
        
        buff = conn.recv(FCAST_BUFFER_SIZE)
        if not buff or len(buff) <= 0:
            break

        session.process_bytes(buff)

    session.close()

    # Remove dead threads from sessions list
    sessions = [t for t in sessions if t.is_alive()]
    log_and_notify("Connection closed from %s" % addr[0])

while True:
    try:
        conn, addr = s.accept()
    except socket.timeout:
        # If there are no active sessions, exit the loop
        if len(sessions) < 0:
            break
    except:
        raise
    else:
        # Create a new thread for the connection
        t = threading.Thread(target=connection_handler, args=(conn, addr))
        sessions.append(t)
        t.start()

    s.close()

    log_and_notify(addonname, "Server stopped")
    xbmc.log("Server stopped.")
