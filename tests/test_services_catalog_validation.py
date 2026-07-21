import copy
import json
import unittest
from pathlib import Path

from scripts.validate_services_catalog import DEFAULT_CATALOG_PATH, concept_key, normalize_name, validate_catalog_data


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

NEW_BATCH_6_CATEGORY_KEYS = {
    "body_exterior",
}

NEW_BATCH_7_CATEGORY_KEYS = {
    "wheels_tires_alignment",
}

NEW_BATCH_7_WHEELS_TIRES_ALIGNMENT_SERVICE_CODES = {
    "tire_condition_inspection",
    "tire_pressure_check_adjustment",
    "tire_rotation_with_balance",
    "four_wheel_tire_balance",
    "tire_tread_depth_measurement",
    "tire_wear_pattern_diagnosis",
    "tire_age_sidewall_inspection",
    "tire_replacement_each",
    "tire_replacement_pair",
    "tire_replacement_set_four",
    "seasonal_tire_changeover",
    "directional_tire_correction",
    "tire_bead_leak_diagnosis",
    "tire_bead_reseal",
    "tubeless_tire_valve_stem_replacement",
    "tire_disposal_service",
    "flat_tire_inspection",
    "slow_tire_leak_diagnosis",
    "tire_sidewall_damage_inspection",
    "tire_road_hazard_inspection",
    "tire_repairability_inspection",
    "tire_demount_internal_inspection",
    "tire_sealant_kit_inspection",
    "tire_vibration_diagnosis",
    "highway_speed_vibration_diagnosis",
    "wheel_tire_imbalance_diagnosis",
    "radial_force_variation_diagnosis",
    "tire_conicity_pull_diagnosis",
    "tire_flat_spot_diagnosis",
    "wheel_tire_runout_measurement",
    "match_mounting_tire_wheel_indexing",
    "tire_noise_diagnosis",
    "tpms_system_diagnosis",
    "tpms_warning_light_diagnosis",
    "tpms_sensor_replacement_set",
    "tpms_sensor_service_kit_replacement",
    "tpms_valve_stem_service",
    "tpms_sensor_programming",
    "indirect_tpms_calibration",
    "tpms_sensor_battery_failure_diagnosis",
    "spare_tire_tpms_sensor_inspection",
    "wheel_inspection",
    "bent_wheel_inspection",
    "cracked_wheel_inspection",
    "wheel_replacement_each",
    "wheel_refinishing_referral_estimate",
    "wheel_repair_referral_estimate",
    "wheel_runout_measurement",
    "wheel_stud_replacement_axle_set",
    "lug_nut_replacement_each",
    "swollen_lug_nut_replacement",
    "wheel_lock_removal",
    "wheel_lock_key_replacement_referral",
    "lug_thread_repair_inspection",
    "hub_centric_ring_inspection_installation",
    "wheel_spacer_inspection",
    "wheel_spacer_removal",
    "aftermarket_wheel_fitment_inspection",
    "wheel_offset_clearance_inspection",
    "wheel_torque_verification",
    "wheel_retorque_after_service",
    "spare_wheel_inspection",
    "spare_tire_installation",
    "compact_spare_inspection",
    "spare_tire_hoist_inspection",
    "spare_tire_hoist_service_replacement",
    "wheel_alignment_inspection",
    "front_end_alignment",
    "alignment_measurement_only",
    "toe_adjustment",
    "front_toe_adjustment",
    "rear_toe_adjustment",
    "camber_adjustment",
    "front_camber_adjustment",
    "rear_camber_adjustment",
    "caster_adjustment",
    "thrust_angle_correction",
    "steering_wheel_centering",
    "alignment_after_suspension_repair",
    "alignment_after_steering_repair",
    "alignment_after_tire_replacement",
    "post_collision_alignment_inspection",
    "alignment_vehicle_pull_diagnosis",
    "alignment_uneven_tire_wear_diagnosis",
    "alignment_off_center_steering_wheel_diagnosis",
    "alignment_steering_wander_diagnosis",
    "alignment_dog_tracking_thrust_angle_diagnosis",
    "ride_height_check_before_alignment",
    "alignment_adjustment_limited_inspection",
    "seized_alignment_adjuster_inspection",
    "alignment_hardware_replacement_estimate",
    "steering_angle_sensor_calibration_after_alignment",
    "electronic_power_steering_center_calibration",
    "adas_alignment_prerequisite_inspection",
    "alignment_verification_before_adas_calibration",
    "modified_suspension_alignment_inspection",
    "lowered_vehicle_alignment_inspection",
    "lifted_vehicle_alignment_inspection",
    "oversized_tire_fitment_inspection",
}

NEW_BATCH_6_BODY_EXTERIOR_SERVICE_CODES = {
    "front_bumper_reinforcement_replacement",
    "rear_bumper_reinforcement_replacement",
    "front_bumper_bracket_retainer_replacement",
    "rear_bumper_bracket_retainer_replacement",
    "bumper_energy_absorber_replacement",
    "bumper_guide_hardware_replacement",
    "front_lower_valance_panel_replacement",
    "air_deflector_splash_shield_replacement",
    "grille_assembly_replacement",
    "active_grille_shutter_assembly_replacement",
    "fender_liner_replacement",
    "quarter_panel_damage_referral_estimate",
    "rocker_panel_damage_referral_estimate",
    "body_panel_gap_inspection",
    "minor_body_panel_alignment",
    "underbody_shield_replacement",
    "exterior_panel_hardware_replacement",
    "door_shell_inspection_replacement_estimate",
    "door_alignment",
    "door_hinge_replacement",
    "door_check_strap_replacement",
    "door_striker_adjustment",
    "door_weatherstrip_replacement",
    "exterior_door_molding_trim_replacement",
    "hood_replacement_referral_estimate",
    "hood_alignment",
    "hood_hinge_replacement",
    "hood_release_cable_replacement",
    "hood_insulation_pad_replacement",
    "hood_lift_support_strut_replacement",
    "hood_bump_stop_seal_adjustment",
    "trunk_lid_alignment",
    "trunk_release_cable_actuator_service",
    "liftgate_hatch_alignment",
    "liftgate_latch_lock_actuator_inspection",
    "liftgate_power_actuator_inspection",
    "hatch_lift_support_strut_replacement",
    "tailgate_handle_replacement",
    "tailgate_latch_adjustment",
    "tailgate_cable_replacement_pair",
    "tailgate_hinge_assist_service",
    "trunk_hatch_tailgate_weatherstrip_replacement",
    "exterior_molding_cladding_replacement",
    "wheel_opening_trim_replacement",
    "emblem_badge_replacement",
    "spoiler_replacement",
    "roof_rack_rail_crossbar_service",
    "license_plate_bracket_replacement",
    "mud_flap_installation_replacement",
    "running_board_side_step_service",
    "tow_hook_cover_access_panel_replacement",
    "rust_corrosion_inspection",
    "surface_rust_treatment_estimate",
    "underbody_corrosion_inspection",
    "body_drain_cleaning",
    "seam_sealer_inspection",
    "structural_rust_referral_estimate",
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
    / "services_catalog_phase31_batch5_codes.json"
)

PRE_BATCH_6_SERVICE_CODES_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "services_catalog_phase31_batch5_baseline_codes.json"
)

PRE_BATCH_7_SERVICE_CODES_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "services_catalog_phase31_batch6_baseline_codes.json"
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
        self.assertEqual(result.categories, 21)
        self.assertEqual(result.services, 663)
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

    def test_batch_6_body_exterior_category_exists_and_has_services(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        categories = {category["key"]: category for category in catalog["categories"]}

        self.assertTrue(NEW_BATCH_6_CATEGORY_KEYS.issubset(categories))
        self.assertEqual(categories["body_exterior"]["name"], "Body & Exterior")
        self.assertEqual(len(categories["body_exterior"]["services"]), 57)

        current_codes = {service["code"] for service in categories["body_exterior"]["services"]}
        self.assertEqual(current_codes, NEW_BATCH_6_BODY_EXTERIOR_SERVICE_CODES)

    def test_batch_6_body_exterior_services_have_complete_search_metadata(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        body_exterior = next(category for category in catalog["categories"] if category["key"] == "body_exterior")

        for service in body_exterior["services"]:
            with self.subTest(service=service["code"]):
                self.assertIsInstance(service.get("aliases"), list)
                self.assertTrue(service["aliases"])
                self.assertIsInstance(service.get("keywords"), list)
                self.assertTrue(service["keywords"])
                self.assertIsInstance(service.get("symptoms"), list)
                self.assertTrue(service["symptoms"])
                self.assertIsInstance(service.get("summary"), str)
                self.assertTrue(service["summary"].strip())

    def test_batch_6_body_exterior_has_no_duplicate_or_near_duplicate_names(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        body_exterior = next(category for category in catalog["categories"] if category["key"] == "body_exterior")
        normalized_names = [normalize_name(service["name"]) for service in body_exterior["services"]]
        concepts = [concept_key(service["name"]) for service in body_exterior["services"]]
        allowed_location_variants = {
            "bumper bracket retainer",
            "bumper reinforcement",
        }

        duplicate_names = {name for name in normalized_names if normalized_names.count(name) > 1}
        duplicate_concepts = {concept for concept in concepts if concepts.count(concept) > 1}

        self.assertEqual(duplicate_names, set())
        self.assertEqual(duplicate_concepts - allowed_location_variants, set())

    def test_pre_batch_6_service_codes_remain_present(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        current_codes = {
            service["code"]
            for category in catalog["categories"]
            for service in category.get("services", [])
        }
        pre_batch_codes = set(json.loads(PRE_BATCH_6_SERVICE_CODES_PATH.read_text(encoding="utf-8")))

        self.assertEqual(len(pre_batch_codes), 507)
        self.assertTrue(pre_batch_codes.issubset(current_codes))

    def test_batch_7_wheels_tires_alignment_category_exists_and_has_services(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        categories = {category["key"]: category for category in catalog["categories"]}

        self.assertTrue(NEW_BATCH_7_CATEGORY_KEYS.issubset(categories))
        self.assertEqual(categories["wheels_tires_alignment"]["name"], "Wheels, Tires & Alignment")
        self.assertEqual(len(categories["wheels_tires_alignment"]["services"]), 99)

        current_codes = {service["code"] for service in categories["wheels_tires_alignment"]["services"]}
        self.assertEqual(current_codes, NEW_BATCH_7_WHEELS_TIRES_ALIGNMENT_SERVICE_CODES)

    def test_batch_7_wheels_tires_alignment_services_have_complete_search_metadata(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        wheels = next(category for category in catalog["categories"] if category["key"] == "wheels_tires_alignment")

        for service in wheels["services"]:
            with self.subTest(service=service["code"]):
                self.assertIsInstance(service.get("aliases"), list)
                self.assertTrue(service["aliases"])
                self.assertIsInstance(service.get("keywords"), list)
                self.assertTrue(service["keywords"])
                self.assertIsInstance(service.get("symptoms"), list)
                self.assertTrue(service["symptoms"])
                self.assertIsInstance(service.get("summary"), str)
                self.assertTrue(service["summary"].strip())

    def test_batch_7_wheels_tires_alignment_has_no_duplicate_names(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        wheels = next(category for category in catalog["categories"] if category["key"] == "wheels_tires_alignment")
        normalized_names = [normalize_name(service["name"]) for service in wheels["services"]]

        duplicate_names = {name for name in normalized_names if normalized_names.count(name) > 1}

        self.assertEqual(duplicate_names, set())

    def test_batch_7_representative_services_remain_in_expected_category(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        wheels = next(category for category in catalog["categories"] if category["key"] == "wheels_tires_alignment")
        services = {service["code"]: service for service in wheels["services"]}

        for code in (
            "tire_condition_inspection",
            "tpms_warning_light_diagnosis",
            "wheel_lock_removal",
            "alignment_vehicle_pull_diagnosis",
            "adas_alignment_prerequisite_inspection",
        ):
            with self.subTest(service=code):
                self.assertIn(code, services)

    def test_pre_batch_7_service_codes_remain_present(self):
        catalog = json.loads(Path(DEFAULT_CATALOG_PATH).read_text(encoding="utf-8"))
        current_codes = {
            service["code"]
            for category in catalog["categories"]
            for service in category.get("services", [])
        }
        pre_batch_codes = set(json.loads(PRE_BATCH_7_SERVICE_CODES_PATH.read_text(encoding="utf-8")))

        self.assertEqual(len(pre_batch_codes), 564)
        self.assertTrue(pre_batch_codes.issubset(current_codes))


if __name__ == "__main__":
    unittest.main()
