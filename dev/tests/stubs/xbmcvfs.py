"""Stand-in for Kodi's xbmcvfs module.

Only translatePath is used, to turn special://temp into a real directory.
Tests point `temp_dir` at somewhere disposable.
"""

import os
import tempfile

temp_dir = tempfile.gettempdir()


def translatePath(path):
    if path.startswith("special://temp"):
        return os.path.join(temp_dir, path[len("special://temp"):].lstrip("/"))
    return path
