import unittest

from fastapi import HTTPException

import main


class VehicleModelValidationTests(unittest.TestCase):
    def test_2001_mercedes_e320_is_valid(self):
        self.assertEqual(
            main.resolve_valid_vehicle_model(2001, "Mercedes-Benz", "E320"),
            "E320",
        )

    def test_mercedes_e_dash_320_resolves_to_e320(self):
        self.assertEqual(
            main.resolve_valid_vehicle_model(2001, "Mercedes-Benz", "E-320"),
            "E320",
        )

    def test_mercedes_e_space_320_resolves_to_e320(self):
        self.assertEqual(
            main.resolve_valid_vehicle_model(2001, "Mercedes-Benz", "E 320"),
            "E320",
        )

    def test_320_cannot_be_saved_as_mercedes_model(self):
        self.assertIsNone(
            main.resolve_valid_vehicle_model(2001, "Mercedes-Benz", "320")
        )

    def test_partial_320_lists_matching_mercedes_models_without_orphan_320(self):
        models = main.get_models_for_make_year("Mercedes-Benz", 2001)
        matches = [
            model
            for model in models
            if main.normalize_vehicle_model_key("320")
            in main.normalize_vehicle_model_key(model)
        ]

        self.assertEqual(matches, ["C320", "CLK320", "E320", "ML320"])
        self.assertNotIn("320", models)

    def test_ford_f150_resolves_to_f_150(self):
        self.assertEqual(
            main.resolve_valid_vehicle_model(2001, "Ford", "F150"),
            "F-150",
        )

    def test_silverado_1500_still_works_when_valid_model_exists(self):
        self.assertEqual(
            main.resolve_valid_vehicle_model(2001, "Chevrolet", "Silverado 1500"),
            "SILVERADO 1500",
        )

    def test_unknown_make_still_raises(self):
        with self.assertRaises(HTTPException):
            main.get_models_for_make_year("Not A Make", 2001)


if __name__ == "__main__":
    unittest.main()
