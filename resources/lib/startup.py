import traceback

import xbmc
import xbmcaddon
import xbmcgui

# Any exception escaping this file leaves the service silently dead -- Kodi
# reports it once as a PythonToCppException in kodi.log and nothing else. A
# half-copied add-on directory looks exactly like a working one from the UI,
# so make the failure visible on screen.
try:
    from fcast_plugin.main import main

    main()
except Exception:
    addon_name = xbmcaddon.Addon().getAddonInfo('name')
    xbmc.log(
        "%s: service failed to start:\n%s" % (addon_name, traceback.format_exc()),
        level=xbmc.LOGERROR,
    )
    xbmcgui.Dialog().notification(
        addon_name,
        "Failed to start - see kodi.log",
        xbmcgui.NOTIFICATION_ERROR,
        5000,
    )
