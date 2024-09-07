import xbmc
import xbmcgui
import xbmcaddon
from threading import Timer

# Retrieve Kodi addon information
addon       = xbmcaddon.Addon()
addonname   = addon.getAddonInfo('name')

def notify(msg, icon=xbmcgui.NOTIFICATION_INFO, timeout=3000, sound=False):
    xbmcgui.Dialog().notification(addonname, msg, icon, timeout, sound)

def log(msg, level=xbmc.LOGDEBUG):
    xbmc.log("%s: %s" % (addonname, msg), level=level)

# Trottle repeated attempts at a function call
def debounce(func, wait):
    class _debounce:
        timer: Timer

        @staticmethod
        def debounced(*args, **kwargs):
            _debounce.timer.cancel()
            _debounce.timer = Timer(wait, func, args=args, kwargs=kwargs)
            _debounce.timer.start()

    _debounce.timer = Timer(0, lambda: None)  # Initial dummy timer
    return _debounce.debounced
