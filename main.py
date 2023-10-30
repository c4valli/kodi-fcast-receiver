import xbmcaddon
import xbmcgui
import xbmc
import socket

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
    s.listen(1)
except:
    log_and_notify(addonname, "Bind failed", xbmcgui.NOTIFICATION_ERROR)
    s.close()
    exit()

log_and_notify(addonname, "Server listening on port %d" % FCAST_PORT, timeout=1000)
log_and_notify(addonname, "Waiting %d seconds for a connection ..." % (FCAST_TIMEOUT / 1000), timeout=FCAST_TIMEOUT)

try:
    conn, addr = s.accept()
except socket.timeout:
    log_and_notify(addonname, "No connection before timeout")
    pass
except:
    raise
else:
    client_addr = addr[0]
    log_and_notify(addonname, "Connection from %s" % client_addr)

    # Receive data from the client
    data = bytes()
    while True:
        buff = conn.recv(FCAST_BUFFER_SIZE)
        if not buff or len(buff) == 0:
            break
        data += buff

    if data:
        xbmc.log(data.hex())
    else:
        log_and_notify(addonname, "No data received")

s.close()

xbmc.log("Server stopped.")
