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
        self.assertIn('До 21 минуты на сторону', HTML)
        self.assertIn('Подходит для альбомов', HTML)
        self.assertIn('До 15 минут на сторону', HTML)
        self.assertIn('Подходит для синглов и EP', HTML)
        self.assertNotIn('Чуть лучше качество', HTML)
        self.assertIn('id="rpmFaqLink"', HTML)
        self.assertIn('faqSpeed', JS)
        self.assertIn('Изготовление — от 3 рабочих дней', HTML)
        self.assertIn('Точную дату уточним при подтверждении заказа', HTML)
        self.assertIn('Мы в сети', HTML)
        self.assertIn('id="stockHint"', HTML)
        self.assertIn('for (const c of catalog.colors) $("colors").append', JS)
        self.assertIn('$("stockHint").hidden=!choosing', JS)
        self.assertIn('getBoundingClientRect().top', JS)

    def test_generated_previews_are_uniform_and_source_images_untouched(self):
        from tempfile import TemporaryDirectory
        from PIL import Image
        from vinyl_previews import generate, BG
        with TemporaryDirectory() as folder:
            output = Path(folder)
            self.assertEqual(generate(ASSETS / "supplier", output), 32)
            self.assertEqual(generate(ASSETS / "supplier", output), 32)
            for i in range(32):
                path = output / f"{i:02}.jpg"
                self.assertTrue(path.exists(), path.name)
                with Image.open(path) as image:
                    self.assertEqual(image.size, (960, 960))
                    for point in ((0,0),(959,0),(0,959),(959,959)):
                        value = image.getpixel(point)
                        self.assertTrue(all(abs(value[j]-BG[j]) <= 5
                                            for j in range(3)),(path.name,value))
        self.assertIn('/assets/previews/', JS)
        self.assertIn('/assets/supplier/', JS)  # fallback/originals untouched
        self.assertNotIn('media.style.backgroundImage', JS)
        self.assertNotIn('data-atlas="29"', CSS)
        self.assertIn('background-image:none!important', CSS)
        self.assertIn('transform:none!important', CSS)
        self.assertIn('Заказ вне очереди', HTML)

    def test_renamed_and_hidden_colours(self):
        self.assertIn(("Розовый марбл", 6000, 18), colours.COLOURS)
        self.assertNotIn(("Лиловый марбл", 6000, 18), colours.COLOURS)

    def test_faq_tab_and_editorial_copy(self):
        self.assertIn('id="faqNav"', HTML)
        self.assertIn('id="faq"', HTML)
        self.assertIn('id="faqManagerLink"', HTML)
        self.assertEqual(HTML.count('class="faq-item"'), 8)
        self.assertIn('id="faqProduction"', HTML)
        self.assertIn('id="faqSpeed"', HTML)
        self.assertIn('Заказ вне очереди» (+25%)', HTML)
        self.assertIn('обе скорости', HTML.lower())
        self.assertIn('согласовываются индивидуально', HTML)
        self.assertIn("Lathe Cut (прямой рез)", HTML)
        self.assertIn("Благодаря качественным материалам", HTML)
        self.assertNotIn("современные заготовки из специального износостойкого", HTML)
        self.assertNotIn("Для наилучшего результата рекомендуем", HTML)
        self.assertIn("Есть вопрос? Напишите менеджеру.", HTML)
        self.assertIn('["faqNav","faq"]', JS)
        self.assertIn('managerLink.href = catalog.manager_url', JS)
        self.assertIn('.faq-contact-button', CSS)
        self.assertIn('margin-inline:auto', CSS)
        self.assertIn('promo-delete', JS)
        self.assertIn('promo-row-actions', CSS)

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
        self.assertIn('prefers-reduced-motion', (ASSETS / 'ambient.js').read_text())
        self.assertIn('preload="none"', HTML)


if __name__ == "__main__":
    unittest.main()
