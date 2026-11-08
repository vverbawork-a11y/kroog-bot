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
                self.assertTrue(data.startswith(bytes.fromhex("ffd8ff")))
                self.assertTrue(data.endswith(bytes.fromhex("ffd9")))
        self.assertEqual(
            sorted(idx for _, _, idx in colours.COLOURS), list(range(32)
        ))

    def test_order_form_replaces_colour_gallery_without_scrolling_below_stock(self):
        self.assertLess(HTML.index('id="colors"'), HTML.index('id="form"'))
        self.assertNotIn('id="incomingSection"', HTML)
        self.assertNotIn('id="unavailableSection"', HTML)
        self.assertIn('id="audioSetup"', HTML)
        self.assertIn('id="rpmTime"', HTML)
        self.assertNotIn('id="tracksA"', HTML)
        self.assertNotIn('id="tracksB"', HTML)
        self.assertNotIn('id="audioLink"', HTML)
        self.assertIn('Оформить заказ', HTML)
        self.assertIn('до 21 мин / сторона', HTML)
        self.assertIn('до 15 мин / сторона', HTML)
        self.assertIn('id="stockHint"', HTML)
        self.assertIn('for (const c of catalog.colors) $("colors").append', JS)
        self.assertIn('$("stockHint").hidden=!choosing', JS)
        self.assertIn('getBoundingClientRect().top', JS)

    def test_original_photos_are_never_downsampled_into_sprite(self):
        self.assertIn('/assets/supplier/', JS)
        self.assertNotIn('colours-atlas.webp', JS)
        self.assertNotIn('backgroundSize="400%', JS)
        self.assertIn('background-size:136% auto!important', CSS)
        self.assertIn('media.classList.add("has-vinyl-photo")', JS)
        self.assertIn('object-fit:cover', CSS)
        self.assertIn('object-position:center center', CSS)
        self.assertNotIn('filter:brightness(.65)', CSS)

    def test_clean_navigation_and_no_unwanted_art(self):
        self.assertLess(HTML.index('id="aboutNav"'), HTML.index('id="worksNav"'))
        self.assertNotIn("Твоя пластинка", HTML)
        self.assertNotIn('mascot-lilac.svg', HTML)
        self.assertIn('class="works-text"', HTML)
        self.assertNotIn("brand-mascot", HTML)
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
