"""Regression tests for the KROOG mobile catalogue and original artwork."""
import re
import unittest
from pathlib import Path

import colours


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "miniapp" / "assets" / "shop.js").read_text(encoding="utf-8")
CSS = (ROOT / "miniapp" / "assets" / "shop.css").read_text(encoding="utf-8")
ASSETS = ROOT / "miniapp" / "assets"


class KROOGStorefrontTests(unittest.TestCase):
    def test_all_supplier_photos_are_original_jpeg_files(self):
        photos = sorted((ASSETS / "supplier").glob("*.jpg"))
        self.assertEqual([p.name for p in photos],
                         [f"{i:02}.jpg" for i in range(32)])
        for photo in photos:
            with self.subTest(photo=photo.name):
                data = photo.read_bytes()
                self.assertGreaterEqual(len(data), 35000)
                self.assertTrue(data.startswith(b"\\xff\\xd8\\xff"))
                self.assertTrue(data.endswith(b"\\xff\\xd9"))
        self.assertEqual(
            sorted(idx for _, _, idx in colours.COLOURS), list(range(32)
        ))

    def test_order_form_replaces_colour_gallery_without_scrolling_below_stock(self):
        self.assertLess(HTML.index('id="colors"'), HTML.index('id="form"'))
        self.assertLess(HTML.index('id="form"'), HTML.index('id="incomingSection"'))
        self.assertLess(HTML.index('id="form"'), HTML.index('id="unavailableSection"'))
        self.assertIn('id="stockHint"', HTML)
        self.assertIn('$("incomingSection").hidden=!choosing', JS)
        self.assertIn('$("unavailableSection").hidden=!choosing', JS)
        self.assertIn('$("stockHint").hidden=!choosing', JS)
        self.assertIn('getBoundingClientRect().top', JS)

    def test_original_photos_are_never_downsampled_into_sprite(self):
        self.assertIn('/assets/supplier/', JS)
        self.assertNotIn('colours-atlas.webp', JS)
        self.assertNotIn('backgroundSize="400%', JS)
        self.assertIn('object-fit:contain', CSS)
        self.assertIn('object-position:center center', CSS)
        self.assertNotIn('filter:brightness(.65)', CSS)

    def test_clean_navigation_and_no_unwanted_art(self):
        self.assertLess(HTML.index('id="aboutNav"'), HTML.index('id="worksNav"'))
        self.assertNotIn("Твоя пластинка", HTML)
        self.assertNotIn("mascot", HTML.lower())
        self.assertNotIn('color-disc.mp4', HTML)
        self.assertNotIn("↗", HTML)
        self.assertNotIn("brand-hero", CSS)

    def test_requested_white_violet_background_and_no_black_line_assets(self):
        self.assertIn('id="ambientBg"', HTML)
        self.assertIn('white-violet-lines-hq.mp4', HTML)
        self.assertIn('white-violet-bg.png', HTML)
        self.assertTrue((ASSETS / "white-violet-lines-hq.mp4").is_file())
        self.assertTrue((ASSETS / "white-violet-bg.png").is_file())
        self.assertIn('prefers-reduced-motion', JS)
        self.assertIn('preload="none"', HTML)


if __name__ == "__main__":
    unittest.main()
