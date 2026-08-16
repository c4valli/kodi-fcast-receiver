"""Add-on settings: the file Kodi reads, and the code that reads it."""

import os
import re
import unittest
import xml.etree.ElementTree as ET

from context import fcast_plugin  # noqa: F401  (sets up sys.path)
from fcast_plugin import settings, util

import xbmcaddon
import xbmcgui

# Strings taken from Kodi itself rather than defined here, with what they say
# in en_gb. They come already translated, which is the point of borrowing them.
CORE_STRINGS = {
    "351": "Off",
    "14045": "{0:d} sec",
}

# The repository root, two levels above dev/tests.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS_FILE = os.path.join(ROOT, "resources", "settings.xml")
STRINGS_FILE = os.path.join(
    ROOT, "resources", "language", "resource.language.en_gb", "strings.po")
ADDON_FILE = os.path.join(ROOT, "addon.xml")


def declared_settings():
    """Every setting element in settings.xml, by id."""
    tree = ET.parse(SETTINGS_FILE)
    return {s.get("id"): s for s in tree.iter("setting")}


def declared_strings():
    """The string ids strings.po defines."""
    with open(STRINGS_FILE, encoding="utf-8") as f:
        return set(re.findall(r'msgctxt\s+"#(\d+)"', f.read()))


class SettingsTestCase(unittest.TestCase):
    def setUp(self):
        xbmcaddon.reset_settings()

    def tearDown(self):
        xbmcaddon.reset_settings()


class TestReading(SettingsTestCase):

    def test_defaults_hold_when_kodi_has_no_such_setting(self):
        # What an upgrade from a version without settings.xml looks like, and
        # what a partial deploy looks like. Neither may raise: this add-on is
        # a service, and an exception here stops it running at all.
        self.assertTrue(settings.notifications_enabled())
        self.assertEqual(settings.image_duration(), 10.0)
        self.assertFalse(settings.image_duration_overrides_sender())

    def test_values_the_user_chose_are_read(self):
        xbmcaddon.settings.update({
            settings.SHOW_NOTIFICATIONS: False,
            settings.PLAYLIST_IMAGE_DURATION: 25,
            settings.PLAYLIST_IMAGE_DURATION_OVERRIDE: True,
        })

        self.assertFalse(settings.notifications_enabled())
        self.assertEqual(settings.image_duration(), 25.0)
        self.assertTrue(settings.image_duration_overrides_sender())

    def test_a_setting_of_the_wrong_type_falls_back(self):
        xbmcaddon.settings[settings.PLAYLIST_IMAGE_DURATION] = "twenty"

        self.assertEqual(settings.image_duration(), 10.0)

    def test_a_negative_duration_is_treated_as_none(self):
        xbmcaddon.settings[settings.PLAYLIST_IMAGE_DURATION] = -5

        self.assertEqual(settings.image_duration(), 0.0)

    def test_changes_are_picked_up_without_a_restart(self):
        # The service runs for as long as Kodi does, so a value read once and
        # kept would leave the settings dialog doing nothing until a restart.
        self.assertEqual(settings.image_duration(), 10.0)

        xbmcaddon.settings[settings.PLAYLIST_IMAGE_DURATION] = 45

        self.assertEqual(settings.image_duration(), 45.0)


class TestSettingsFile(unittest.TestCase):
    """The XML has to line up with the code: Kodi reports neither mismatch."""

    def setUp(self):
        self.root = ET.parse(SETTINGS_FILE).getroot()

    def test_the_format_is_the_one_kodi_19_and_later_expect(self):
        self.assertEqual(self.root.tag, "settings")
        # Without version="1" the section tag is ignored and the dialog is empty.
        self.assertEqual(self.root.get("version"), "1")

        sections = self.root.findall("section")
        self.assertEqual(len(sections), 1, "only Kodi's own settings may have several")

        addon_id = ET.parse(ADDON_FILE).getroot().get("id")
        self.assertEqual(sections[0].get("id"), addon_id)

    def test_every_setting_the_code_reads_is_declared(self):
        # A setting the code asks for by a name the file does not declare
        # reads as its default forever, and nothing anywhere says so.
        declared = declared_settings()
        for key in settings.DEFAULTS:
            self.assertIn(key, declared)

    def test_no_setting_is_declared_that_the_code_never_reads(self):
        for key in declared_settings():
            self.assertIn(key, settings.DEFAULTS)

    def test_the_declared_defaults_match_the_ones_in_the_code(self):
        # These are two separate declarations of the same thing: Kodi uses the
        # XML, and the code uses DEFAULTS when Kodi has nothing to say.
        parse = {
            "boolean": lambda text: text == "true",
            "integer": lambda text: int(text),
        }
        for key, element in declared_settings().items():
            with self.subTest(setting=key):
                kind = element.get("type")
                self.assertIn(kind, parse, "no parser for this setting type")
                declared = parse[kind](element.findtext("default"))
                self.assertEqual(declared, settings.DEFAULTS[key])

    def test_the_default_duration_is_reachable_on_the_spinner(self):
        # A default that is not a whole number of steps from the minimum
        # cannot be set again once the user has spun away from it.
        element = declared_settings()[settings.PLAYLIST_IMAGE_DURATION]
        constraints = element.find("constraints")
        minimum = int(constraints.findtext("minimum"))
        step = int(constraints.findtext("step"))
        maximum = int(constraints.findtext("maximum"))
        default = int(element.findtext("default"))

        self.assertTrue(minimum <= default <= maximum)
        self.assertEqual((default - minimum) % step, 0)

    def test_every_label_is_a_string_id_that_exists(self):
        # Labels have to be numeric ids from strings.po. Literal text renders
        # as nothing at all, and an id with no entry renders as its number.
        strings = declared_strings()
        for element in self.root.iter():
            for attribute in ("label", "help"):
                value = element.get(attribute)
                if value is None:
                    continue
                with self.subTest(element=element.tag, attribute=attribute):
                    self.assertRegex(value, r"^\d+$")
                    if int(value) < 30000:
                        # Kodi's own, from its core strings.po - the ranges
                        # from 30000 up are the ones add-ons may define.
                        self.assertIn(value, CORE_STRINGS)
                    else:
                        self.assertIn(value, strings)

    def test_borrowed_core_strings_are_in_the_range_kodi_owns(self):
        # An add-on id used where a core one is meant, or the other way
        # round, resolves to something unrelated rather than failing.
        for value in CORE_STRINGS:
            self.assertLess(int(value), 30000)

    def test_our_own_strings_stay_in_the_range_for_services(self):
        for value in declared_strings():
            self.assertTrue(32000 <= int(value) <= 32999, value)

    def test_dependencies_point_at_settings_that_exist(self):
        declared = declared_settings()
        for dependency in self.root.iter("dependency"):
            target = dependency.get("setting")
            if target is not None:
                self.assertIn(target, declared)


class TestNotificationSetting(SettingsTestCase):
    def setUp(self):
        super().setUp()
        xbmcgui.notifications.clear()

    def test_notifications_are_shown_by_default(self):
        util.notify("Connection from 10.0.0.5")

        self.assertEqual(len(xbmcgui.notifications), 1)

    def test_turning_them_off_silences_them(self):
        xbmcaddon.settings[settings.SHOW_NOTIFICATIONS] = False

        util.notify("Starting player ...")

        self.assertEqual(xbmcgui.notifications, [])

    def test_errors_are_shown_even_when_they_are_off(self):
        # The ones worth interrupting whatever is on screen for.
        xbmcaddon.settings[settings.SHOW_NOTIFICATIONS] = False

        util.notify("Bind failed", xbmcgui.NOTIFICATION_ERROR)

        self.assertEqual(len(xbmcgui.notifications), 1)


if __name__ == "__main__":
    unittest.main()
