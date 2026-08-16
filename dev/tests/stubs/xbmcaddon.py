"""Stand-in for Kodi's xbmcaddon module.

Tests put values in `settings` to stand for what the user has chosen. An id
that is not there raises, which is what Kodi itself does for a setting that
resources/settings.xml does not declare - see Addon.cpp, where every getter
throws WrongTypeException rather than returning anything.
"""

settings = {}


def reset_settings():
    settings.clear()


class Addon:
    _info = {
        "name": "FCast Receiver",
        "version": "0.0.0-test",
        "id": "service.fcast.receiver",
    }

    def getAddonInfo(self, key):
        return self._info[key]

    def _get(self, key, *types):
        if key not in settings:
            raise TypeError("Invalid setting type")
        value = settings[key]
        # bool is a subclass of int, so an explicit type check is the only way
        # to tell "true" from 1 the way Kodi's typed settings do.
        if type(value) not in types:
            raise TypeError("Invalid setting type")
        return value

    def getSettingBool(self, key):
        return self._get(key, bool)

    def getSettingInt(self, key):
        return self._get(key, int)

    def getSettingNumber(self, key):
        return self._get(key, float, int)

    def getSettingString(self, key):
        return self._get(key, str)
