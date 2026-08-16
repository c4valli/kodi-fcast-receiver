"""Discovery across platforms that ship different D-Bus bindings.

Debian and Raspberry Pi OS have python-dbus, LibreELEC and CoreELEC have
DBussy, and some images have neither. The backends are tried in turn, and none
of it may raise: a service that dies during registration takes casting with
it, where a receiver that merely fails to advertise can still be reached by IP.
"""

import unittest

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin import mdns

import xbmc


class FailingBackend:
    name = "failing"

    def register(self, service_name, port, txt):
        raise RuntimeError("no binding here")

    def unregister(self):
        pass


class WorkingBackend:
    name = "working"
    registered = None

    def register(self, service_name, port, txt):
        type(self).registered = (service_name, port, txt)

    def unregister(self):
        type(self).registered = None


class RaisingOnUnregister(WorkingBackend):
    name = "cranky"

    def unregister(self):
        raise RuntimeError("bus already gone")


class MdnsTestCase(unittest.TestCase):
    def setUp(self):
        xbmc.messages.clear()
        self.backends = mdns._BACKENDS
        mdns._backend = None
        WorkingBackend.registered = None

    def tearDown(self):
        mdns._BACKENDS = self.backends
        mdns._backend = None

    def warnings(self):
        return [m for level, m in xbmc.messages if level >= xbmc.LOGWARNING]


class TestBackendSelection(MdnsTestCase):

    def test_the_first_backend_that_works_is_used(self):
        mdns._BACKENDS = (FailingBackend, WorkingBackend)

        self.assertTrue(mdns.register(port=46899, protocol_version=3))

        self.assertIsNotNone(WorkingBackend.registered)
        name, port, txt = WorkingBackend.registered
        self.assertEqual(port, 46899)
        self.assertIn(b"version=3", txt)

    def test_a_platform_with_no_backend_at_all_does_not_raise(self):
        # An image with neither binding and no avahi-publish. Discovery is a
        # convenience; casting by IP still has to work.
        mdns._BACKENDS = (FailingBackend, FailingBackend)

        self.assertFalse(mdns.register())

    def test_failing_to_advertise_is_reported_where_it_can_be_seen(self):
        # Otherwise "it doesn't show up in the app" has nothing behind it.
        mdns._BACKENDS = (FailingBackend,)

        mdns.register()

        self.assertTrue([w for w in self.warnings() if "no backend" in w])
        self.assertTrue([w for w in self.warnings() if "no binding here" in w],
                        "the reason each backend failed should be in there")

    def test_registering_twice_keeps_the_first_registration(self):
        mdns._BACKENDS = (WorkingBackend,)
        mdns.register()
        WorkingBackend.registered = None

        self.assertTrue(mdns.register())

        self.assertIsNone(WorkingBackend.registered, "registered a second time")


class TestUnregister(MdnsTestCase):

    def test_unregistering_without_a_registration_is_a_no_op(self):
        mdns.unregister()

    def test_a_backend_that_throws_on_the_way_out_is_swallowed(self):
        # Kodi is shutting down at this point; raising here would leave the
        # rest of the shutdown undone.
        mdns._BACKENDS = (RaisingOnUnregister,)
        mdns.register()

        mdns.unregister()

        self.assertIsNone(mdns._backend)


if __name__ == "__main__":
    unittest.main()
