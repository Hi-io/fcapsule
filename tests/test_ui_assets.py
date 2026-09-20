import re
import unittest
import xml.etree.ElementTree as ET

from fcapsule.ui.app import CSS, HTML, ICONS


class UIAssetTests(unittest.TestCase):
    def test_every_stylesheet_icon_is_bundled(self):
        referenced = re.findall(r"/assets/icons/([a-z0-9-]+\.svg)", CSS)
        self.assertTrue(referenced)
        self.assertFalse(set(referenced) - ICONS.keys())

    def test_icons_are_valid_svg(self):
        for name, source in ICONS.items():
            with self.subTest(icon=name):
                self.assertEqual(ET.fromstring(source).tag, "{http://www.w3.org/2000/svg}svg")

    def test_navigation_retains_text_labels_and_local_assets(self):
        for label in ("Operations", "Targets", "Settings"):
            self.assertIn(label, HTML)
        self.assertNotIn("https://", HTML)
        self.assertIn("--accent:", CSS)
