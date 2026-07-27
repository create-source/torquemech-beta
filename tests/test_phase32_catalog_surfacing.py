import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import main
from scripts.validate_services_catalog import DEFAULT_CATALOG_PATH


REPRESENTATIVE_SERVICES_BY_CATEGORY = {
    "maintenance": "oil_change_diesel",
    "engine": "glow_plug_replacement_each",
    "cooling": "egr_cooler_replacement_diesel",
    "brakes": "pads_and_rotors_per_axle",
    "suspension": "air_suspension_diagnostic",
    "drivetrain": "front_diff_service_fluid_inspect",
    "transmission": "transmission_fluid_flush_if_applicable",
    "ac_heat": "a_c_leak_test_uv_dye_electronic",
    "electrical": "can_bus_network_diagnostic",
    "fuel": "fuel_pump_replacement_in_tank",
    "exhaust": "dpf_forced_regeneration_if_supported",
    "body_paint": "windshield_replacement_referral_estimate",
    "diagnostics": "pre_purchase_inspection",
    "restraint_safety": "srs_airbag_diagnostic",
    "adas_safety": "post_windshield_replacement_adas_calibration",
    "hybrid_ev": "high_voltage_battery_state_of_health_test",
    "glass_mirrors_wipers": "rear_window_back_glass_replacement",
    "lighting": "automatic_headlamp_sensor_replacement",
    "interior_accessories": "wireless_charging_pad_replacement",
    "body_exterior": "trailer_hitch_installation_estimate",
    "wheels_tires_alignment": "tire_replacement_set_four",
    "fluids_filters_preventive": "diesel_exhaust_fluid_top_off",
    "specialty_diagnostics_inspections": "multi_system_warning_light_diagnostic",
}

SEARCH_CASES = {
    "diesel oil": "oil_change_diesel",
    "glow plug": "glow_plug_replacement_each",
    "hybrid high voltage": "high_voltage_battery_state_of_health_test",
    "post windshield adas": "post_windshield_replacement_adas_calibration",
    "tire set four": "tire_replacement_set_four",
    "multi system warning light": "multi_system_warning_light_diagnostic",
    "trailer hitch estimate": "trailer_hitch_installation_estimate",
}


def searchable_text(service):
    parts = [service.get("code", ""), service.get("name", ""), service.get("summary", "")]
    for field in ("aliases", "keywords", "symptoms"):
        value = service.get(field)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
    return " ".join(parts).lower().replace("-", " ").replace("_", " ")


class Phase32CatalogSurfacingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main._services_cache = None
        main._services_mtime = None
        cls.catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        cls.client = TestClient(main.app, base_url="http://localhost")

    def test_all_frozen_categories_load_through_create_estimate_api(self):
        response = self.client.get("/api/categories")

        self.assertEqual(response.status_code, 200)
        categories = response.json()
        self.assertEqual(len(categories), 23)
        self.assertEqual([category["key"] for category in categories], [category["key"] for category in self.catalog["categories"]])
        self.assertEqual([category["name"] for category in categories], [category["name"] for category in self.catalog["categories"]])

    def test_every_service_is_reachable_through_its_category_filter(self):
        total = 0

        for category in self.catalog["categories"]:
            with self.subTest(category=category["key"]):
                response = self.client.get(f"/api/services/{category['key']}")
                self.assertEqual(response.status_code, 200)
                services = response.json()
                self.assertEqual(len(services), len(category.get("services", [])))
                self.assertTrue(services)
                self.assertEqual({service["code"] for service in services}, {service["code"] for service in category.get("services", [])})
                self.assertTrue(all(service.get("category") == category["key"] for service in services))
                total += len(services)

        self.assertEqual(total, 788)

    def test_representative_services_load_metadata_from_service_lookup_api(self):
        for category_key, service_code in REPRESENTATIVE_SERVICES_BY_CATEGORY.items():
            with self.subTest(category=category_key, service=service_code):
                response = self.client.get(f"/api/service/{service_code}")
                self.assertEqual(response.status_code, 200)
                service = response.json()
                self.assertEqual(service["code"], service_code)
                self.assertEqual(service["category"], category_key)
                self.assertTrue(service["name"].strip())
                self.assertIsInstance(service["labor_hours_min"], (int, float))
                self.assertIsInstance(service["labor_hours_max"], (int, float))
                self.assertLessEqual(service["labor_hours_min"], service["labor_hours_max"])
                self.assertTrue(searchable_text(service).strip())

    def test_service_search_metadata_finds_representative_edge_cases(self):
        services = [
            service
            for category in self.catalog["categories"]
            for service in category.get("services", [])
        ]

        for query, expected_code in SEARCH_CASES.items():
            query_terms = query.lower().split()
            matches = [
                service["code"]
                for service in services
                if all(term in searchable_text(service) for term in query_terms)
            ]

            with self.subTest(query=query):
                self.assertIn(expected_code, matches)

    def test_bounded_service_search_endpoint_returns_category_metadata(self):
        response = self.client.get("/api/services/search", params={"q": "intake", "limit": 25})

        self.assertEqual(response.status_code, 200)
        results = response.json()
        self.assertTrue(results)
        self.assertLessEqual(len(results), 25)
        self.assertTrue(all(result.get("category") for result in results))
        self.assertTrue(all(result.get("categoryName") for result in results))
        self.assertTrue(any("intake" in searchable_text(result) for result in results))

    def test_service_search_endpoint_rejects_empty_or_too_short_queries_without_catalog_dump(self):
        empty_response = self.client.get("/api/services/search", params={"q": "", "limit": 50})
        short_response = self.client.get("/api/services/search", params={"q": "i", "limit": 50})

        self.assertEqual(empty_response.status_code, 200)
        self.assertEqual(short_response.status_code, 200)
        self.assertEqual(empty_response.json(), [])
        self.assertEqual(short_response.json(), [])

    def test_invalid_category_and_service_return_not_found(self):
        category_response = self.client.get("/api/services/not_a_real_category")
        service_response = self.client.get("/api/service/not_a_real_service")

        self.assertEqual(category_response.status_code, 404)
        self.assertEqual(service_response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
