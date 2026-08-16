import xbmc
import xbmcgui
import xbmcaddon
from threading import Timer

from . import settings

# Retrieve Kodi addon information
addon        = xbmcaddon.Addon()
addonname    = addon.getAddonInfo('name')
addonversion = addon.getAddonInfo('version')

def notify(msg, icon=xbmcgui.NOTIFICATION_INFO, timeout=3000, sound=False):
    # Errors are always shown: they are the ones worth interrupting whatever
    # is on screen for. The rest - connections, playback starting - are the
    # ones that become noise on a TV, and the user can turn them off.
    if icon != xbmcgui.NOTIFICATION_ERROR and not settings.notifications_enabled():
        return
    xbmcgui.Dialog().notification(addonname, msg, icon, timeout, sound)

def log(msg, level=xbmc.LOGDEBUG):
    xbmc.log("%s: %s" % (addonname, msg), level=level)

# Trottle repeated attempts at a function call
def debounce(func, wait):
    timer = [None]
    def debounced(*args, **kwargs):
        if timer[0]:
            timer[0].cancel()
        timer[0] = Timer(wait, func, args=args, kwargs=kwargs)
        timer[0].start()
    return debounced