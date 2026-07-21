import copy
import json
import unittest
from pathlib import Path

from scripts.validate_services_catalog import DEFAULT_CATALOG_PATH, validate_catalog_data


NEW_BATCH_4_CATEGORY_KEYS = {
    "restraint_safety",
    "adas_safety",
    "hybrid_ev",
}

NEW_BATCH_5_CATEGORY_KEYS = {
    "glass_mirrors_wipers",
    "lighting",
    "interior_accessories",
}

PRE_BATCH_5_CATEGORY_KEYS = {
    "maintenance",
    "engine",
    "cooling",
    "brakes",
    "suspension",
    "drivetrain",
    "transmission",
    "ac_heat",
    "electrical",
    "fuel",
    "exhaust",
    "body_paint",
    "diagnostics",
    "restraint_safety",
    "adas_safety",
    "hybrid_ev",
}

PRE_BATCH_5_SERVICE_CODES_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "services_catalog_phase31_batch4_codes.json"
)


def minimal_catalog():
    return {
        "version": "test",
        "categories": [
            {
                "key": "maintenance",
                "name": "Maintenance",
                "services": [
                    {
                        "code": "oil_change",
                        "name": "Oil Change",
                        "labor_hours_min": 0.5,
                        "labor_hours_max": 1.0,
                        "aliases": ["Oil Service"],
                    }
                ],
            }
        ],
    }


class ServicesCatalogValidationTests(unittest.TestCase):
    def test_current_live_catalog_passes_required_validation(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))

        result = validate_catalog_data(catalog)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.categories, 19)
        self.assertEqual(result.services, 507)
        self.assertEqual(len(result.warnings), 2)

    def test_duplicate_service_code_fails(self):
        catalog = minimal_catalog()
        duplicate = copy.deepcopy(catalog["categories"][0]["services"][0])
        duplicate["name"] = "Oil Filter Service"
        catalog["categories"][0]["services"].append(duplicate)

        result = validate_catalog_data(catalog)

        self.assertTrue(any("duplicate service code" in issue.message for issue in result.errors))

    def test_missing_labor_value_fails(self):
        catalog = minimal_catalog()
        del catalog["categories"][0]["services"][0]["labor_hours_min"]

        result = validate_catalog_data(catalog)

        self.assertTrue(any("labor_hours_min" in issue.message for issue in result.errors))

    def test_invalid_labor_range_fails(self):
        catalog = minimal_catalog()
        catalog["categories"][0]["services"][0]["labor_hours_min"] = 2.0
        catalog["categories"][0]["services"][0]["labor_hours_max"] = 1.0

        result = validate_catalog_data(catalog)

        self.assertTrue(any("labor_hours_max" in issue.message for issue in result.errors))

    def test_invalid_metadata_type_fails(self):
        catalog = minimal_catalog()
        catalog["categories"][0]["services"][0]["keywords"] = "oil"

        result = validate_catalog_data(catalog)

        self.assertTrue(any("keywords" in issue.message for issue in result.errors))

    def test_duplicate_normalized_name_fails(self):
        catalog = minimal_catalog()
        duplicate = copy.deepcopy(catalog["categories"][0]["services"][0])
        duplicate["code"] = "oil_service"
        duplicate["name"] = "Oil-Change"
        catalog["categories"][0]["services"].append(duplicate)

        result = validate_catalog_data(catalog)

        self.assertTrue(any("duplicate normalized service name" in issue.message for issue in result.errors))

    def test_missing_optional_metadata_warns_without_failure(self):
        catalog = minimal_catalog()
        catalog["categories"][0]["services"][0].pop("aliases")

        result = validate_catalog_data(catalog)

        self.assertEqual(result.errors, [])
        self.assertTrue(any("lacks optional searchable metadata" in issue.message for issue in result.warnings))
        self.assertEqual(result.services_with_search_metadata, 0)
        self.assertEqual(result.services_without_search_metadata, 1)

    def test_allowlisted_location_variant_does_not_warn(self):
        catalog = minimal_catalog()
        catalog["categories"][0]["services"] = [
            {
                "code": "front_brake_pads_replacement",
                "name": "Front Brake Pads Replacement",
                "labor_hours_min": 1.0,
                "labor_hours_max": 2.0,
                "summary": "Replaces front brake pads.",
            },
            {
                "code": "rear_brake_pads_replacement",
                "name": "Rear Brake Pads Replacement",
                "labor_hours_min": 1.0,
                "labor_hours_max": 2.0,
                "summary": "Replaces rear brake pads.",
            },
        ]

        result = validate_catalog_data(catalog)

        self.assertEqual(result.errors, [])
        self.assertFalse(any("suspicious duplicate concept `brake pads`" in issue.message for issue in result.warnings))

    def test_unreviewed_duplicate_concept_still_warns(self):
        catalog = minimal_catalog()
        catalog["categories"][0]["services"] = [
            {
                "code": "water_pump_replacement",
                "name": "Water Pump Replacement",
                "labor_hours_min": 1.0,
                "labor_hours_max": 3.0,
                "summary": "Replaces the water pump.",
            },
            {
                "code": "water_pump_diagnosis",
                "name": "Water Pump Diagnosis",
                "labor_hours_min": 0.8,
                "labor_hours_max": 1.5,
                "summary": "Checks water pump concerns.",
            },
        ]

        result = validate_catalog_data(catalog)

        self.assertEqual(result.errors, [])
        self.assertTrue(any("suspicious duplicate concept `water pump`" in issue.message for issue in result.warnings))

    def test_batch_4_categories_exist_and_have_services(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        categories = {category["key"]: category for category in catalog["categories"]}

        self.assertTrue(NEW_BATCH_4_CATEGORY_KEYS.issubset(categories))
        self.assertEqual(len(categories["restraint_safety"]["services"]), 20)
        self.assertEqual(len(categories["adas_safety"]["services"]), 23)
        self.assertEqual(len(categories["hybrid_ev"]["services"]), 38)

    def test_batch_4_services_have_required_search_metadata(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))

        for category in catalog["categories"]:
            if category["key"] not in NEW_BATCH_4_CATEGORY_KEYS:
                continue
            for service in category["services"]:
                with self.subTest(service=service["code"]):
                    self.assertIsInstance(service.get("aliases"), list)
                    self.assertTrue(service["aliases"])
                    self.assertIsInstance(service.get("keywords"), list)
                    self.assertTrue(service["keywords"])
                    self.assertIsInstance(service.get("symptoms"), list)
                    self.assertTrue(service["symptoms"])
                    self.assertIsInstance(service.get("summary"), str)
                    self.assertTrue(service["summary"].strip())

    def test_live_catalog_service_codes_are_globally_unique(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        codes = [
            service["code"]
            for category in catalog["categories"]
            for service in category.get("services", [])
        ]

        self.assertEqual(len(codes), len(set(codes)))

    def test_batch_5_categories_exist_and_have_services(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        categories = {category["key"]: category for category in catalog["categories"]}

        self.assertTrue(NEW_BATCH_5_CATEGORY_KEYS.issubset(categories))
        self.assertEqual(len(categories["glass_mirrors_wipers"]["services"]), 28)
        self.assertEqual(len(categories["lighting"]["services"]), 26)
        self.assertEqual(len(categories["interior_accessories"]["services"]), 44)

    def test_batch_5_services_have_complete_search_metadata(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))

        for category in catalog["categories"]:
            if category["key"] not in NEW_BATCH_5_CATEGORY_KEYS:
                continue
            for service in category["services"]:
                with self.subTest(service=service["code"]):
                    self.assertIsInstance(service.get("aliases"), list)
                    self.assertTrue(service["aliases"])
                    self.assertIsInstance(service.get("keywords"), list)
                    self.assertTrue(service["keywords"])
                    self.assertIsInstance(service.get("symptoms"), list)
                    self.assertTrue(service["symptoms"])
                    self.assertIsInstance(service.get("summary"), str)
                    self.assertTrue(service["summary"].strip())

    def test_pre_batch_5_service_codes_remain_present(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        current_codes = {
            service["code"]
            for category in catalog["categories"]
            for service in category.get("services", [])
        }
        pre_batch_codes = set(json.loads(PRE_BATCH_5_SERVICE_CODES_PATH.read_text(encoding="utf-8")))

        self.assertEqual(len(pre_batch_codes), 409)
        self.assertTrue(pre_batch_codes.issubset(current_codes))

    def test_pre_batch_5_category_keys_remain_present(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        current_category_keys = {category["key"] for category in catalog["categories"]}

        self.assertTrue(PRE_BATCH_5_CATEGORY_KEYS.issubset(current_category_keys))


if __name__ == "__main__":
    unittest.main()
