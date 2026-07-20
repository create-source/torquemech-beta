import copy
import json
import unittest
from pathlib import Path

from scripts.validate_services_catalog import DEFAULT_CATALOG_PATH, validate_catalog_data


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
        self.assertEqual(result.categories, 13)
        self.assertEqual(result.services, 328)

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


if __name__ == "__main__":
    unittest.main()
