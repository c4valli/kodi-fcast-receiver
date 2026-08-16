"""Put the add-on and the Kodi stubs on sys.path for the test modules."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# ../.. from dev/tests: the tests live under dev/, the add-on does not.
ROOT = os.path.dirname(os.path.dirname(HERE))

# Stubs first: the real xbmc modules only exist inside Kodi.
for path in (os.path.join(HERE, "stubs"), os.path.join(ROOT, "resources", "lib")):
    if path not in sys.path:
        sys.path.insert(0, path)

import fcast_plugin  # noqa: E402,F401
