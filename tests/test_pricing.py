"""Retail prices include the base package once; print remains optional."""
import unittest

import calculator
import colours


class PackagePricingTests(unittest.TestCase):
    def test_every_catalogue_price_includes_the_approved_500_ruble_increase(self):
        # Expected retail prices in supplier atlas order, including packaging.
        self.assertEqual([price for _,price,_ in colours.COLOURS], [
            6000,5700,6000,5700,6000,6400,6400,6200,
            6000,6400,6000,6400,6000,6000,6400,6000,
            6400,6000,6000,6000,6400,7000,6000,6400,
            6000,6000,4400,6000,6400,6000,6200,5000,
        ])

    def test_legacy_and_unknown_colours_use_the_same_updated_base_prices(self):
        self.assertEqual(colours.record("Голубой")["price"],6000)
        self.assertEqual(colours.record("Фиолетовый дымчатый")["price"],6000)
        self.assertEqual(colours.record("Неизвестный цвет")["price"],6000)
        self.assertEqual(colours.record("Неизвестный чёрный",True)["price"],5000)

    def test_base_package_is_not_charged_again_at_checkout(self):
        base=calculator.calculate(True,1,False,False)
        self.assertEqual(base["unit_price"],5000)
        self.assertEqual(base["total"],5000)
        transparent=calculator.calculate(False,1,False,False,unit_price_override=4400)
        self.assertEqual(transparent["total"],4400)

    def test_optional_print_prices_remain_unchanged(self):
        result=calculator.calculate(True,1,True,True)
        self.assertEqual(calculator.PRICE_ENVELOPE,1000)
        self.assertEqual(calculator.PRICE_LABEL,500)
        self.assertEqual(result["total"],6500)

    def test_quantity_discount_uses_the_packaged_unit_price(self):
        result=calculator.calculate(True,5,False,False)
        self.assertEqual(result["subtotal"],25000)
        self.assertEqual(result["discount_rub"],1250)
        self.assertEqual(result["total"],23750)


if __name__ == "__main__":
    unittest.main()
