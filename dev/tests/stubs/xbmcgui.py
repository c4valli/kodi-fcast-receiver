NOTIFICATION_INFO, NOTIFICATION_WARNING, NOTIFICATION_ERROR = 0, 1, 2

notifications = []

# Which window Kodi is currently showing, and which dialog is on top of it.
# The picture viewer is a dialog, so it reports through the latter.
current_window_id = 10000
current_dialog_id = 9999


def getCurrentWindowId():
    return current_window_id


def getCurrentWindowDialogId():
    return current_dialog_id

class Dialog:
    def notification(self, heading, message, icon=NOTIFICATION_INFO, time=5000, sound=True):
        notifications.append((heading, message, icon))

class ListItem:
    def __init__(self, label="", label2="", path="", offscreen=False):
        self.path = path
        self.mime_type = None
        self.content_lookup = None
        self.properties = {}

    def setPath(self, path):
        self.path = path

    def getPath(self):
        return self.path

    def setMimeType(self, mime_type):
        self.mime_type = mime_type

    def setContentLookup(self, enable):
        self.content_lookup = enable

    def setProperty(self, key, value):
        self.properties[key] = value

    def getProperty(self, key):
        return self.properties.get(key, "")
