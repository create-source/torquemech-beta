from pathlib import Path
import json
import sqlite3
import unittest
from unittest.mock import patch

import routers.pro as pro_module


ROOT = Path(__file__).resolve().parents[1]


class RepairWorkspaceCleanupTests(unittest.TestCase):
    def test_workspace_items_label_sources_and_estimate_totals(self):
        vehicle = {"id": 1, "year": 2016, "make": "Honda", "model": "Accord", "mileage": 120000}
        findings = [
            {
                "id": 10,
                "customer_id": 1,
                "vehicle_id": 1,
                "status": "Approved",
                "finding": "Front pads are below spec",
                "recommendation": "Replace front brake pads",
                "request_type": "finding",
                "repair_work_status": "ready",
                "created_at": "2026-06-24T10:00:00",
                "mileage": 120000,
            }
        ]
        repairs = [
            {
                "id": 20,
                "customer_id": 1,
                "vehicle_id": 1,
                "repair_name": "Replace front brake pads",
                "notes": "Source: Estimate\nCreated from estimator.",
                "status": "Open",
                "labor_hours": 1.2,
                "labor_rate": 125,
                "labor_cost": 150,
                "parts_cost": 115,
                "workflow_source_type": "estimate",
                "created_at": "2026-06-24T10:05:00",
                "repair_date": "2026-06-24",
                "mileage": 120000,
            },
            {
                "id": 21,
                "customer_id": 1,
                "vehicle_id": 1,
                "repair_name": "Rotate tires",
                "notes": "Manual counter repair.",
                "status": "Open",
                "labor_hours": 0.5,
                "labor_rate": 100,
                "labor_cost": 50,
                "parts_cost": 0,
                "workflow_source_type": "",
                "created_at": "2026-06-24T10:06:00",
                "repair_date": "2026-06-24",
                "mileage": 120000,
            },
        ]

        with patch.object(pro_module, "get_repair_blueprint_for_work_item", return_value=None):
            items = pro_module.build_repair_work_items(vehicle, findings, [], repairs)

        by_title = {item["title"]: item for item in items}
        self.assertEqual(by_title["Replace front brake pads"]["source_label"], "Source: Estimate")
        self.assertEqual(by_title["Replace front brake pads"]["repair_work_status_label"], "Ready for Repair")
        self.assertEqual(by_title["Replace front brake pads"]["labor_total"], 150)
        self.assertEqual(by_title["Replace front brake pads"]["parts_total"], 115)
        self.assertEqual(by_title["Replace front brake pads"]["grand_total"], 265)
        self.assertTrue(by_title["Replace front brake pads"]["has_pricing"])
        self.assertEqual(by_title["Replace front brake pads"]["detail"], "Created from estimator.")

        self.assertEqual(by_title["Rotate tires"]["source_label"], "Source: Manual Repair")
        self.assertEqual(by_title["Replace front brake pads"]["source_type"], "repair")

        source_labels = {item["source_label"] for item in items}
        self.assertIn("Source: Finding", source_labels)
        self.assertIn("Source: Estimate", source_labels)
        finding_item = next(item for item in items if item["source_type"] == "finding")
        self.assertEqual(finding_item["source_label"], "Source: Finding")
        self.assertEqual(finding_item["original_finding"], "Front pads are below spec")

    def test_workspace_card_includes_parts_sources_when_vendor_data_exists(self):
        vehicle = {"id": 1, "year": 2016, "make": "Honda", "model": "Accord", "mileage": 120000}
        repairs = [
            {
                "id": 20,
                "customer_id": 1,
                "vehicle_id": 1,
                "repair_name": "Replace front brake pads",
                "notes": "Source: Estimate",
                "status": "Open",
                "labor_hours": 1.2,
                "labor_rate": 125,
                "labor_cost": 150,
                "parts_cost": 115,
                "workflow_source_type": "estimate",
                "created_at": "2026-06-24T10:05:00",
                "repair_date": "2026-06-24",
                "mileage": None,
            }
        ]
        blueprint = {
            "title": "Front Brake Pads",
            "vendor_links": [
                {"label": "OEM/dealer catalog", "status": "VIN-confirmed source"},
                {"label": "NAPA", "url": "https://example.test/napa"},
            ],
        }

        with patch.object(pro_module, "get_repair_blueprint_for_work_item", return_value=blueprint):
            items = pro_module.build_repair_work_items(vehicle, [], [], repairs)

        self.assertEqual(items[0]["parts_sources"][0]["label"], "OEM/dealer catalog")
        self.assertEqual(items[0]["parts_sources"][0]["note"], "VIN-confirmed source")
        self.assertEqual(items[0]["parts_sources"][1]["url"], "https://example.test/napa")

    def test_photo_stage_labels_are_present(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        repair_detail = (ROOT / "templates" / "pro" / "repair_detail.html").read_text(encoding="utf-8")

        self.assertIn("Before / Inspection Photos", vehicle_detail)
        self.assertIn('id="before_inspection_photos"', vehicle_detail)
        self.assertIn('type="file"', vehicle_detail)
        self.assertIn("Upload photos of the original problem before repair.", vehicle_detail)
        self.assertIn("After Repair Photos", repair_detail)
        self.assertIn('id="after_repair_photos"', repair_detail)
        self.assertIn('name="after_repair_photos"', repair_detail)
        self.assertIn('type="file"', repair_detail)
        self.assertIn('enctype="multipart/form-data"', repair_detail)
        self.assertIn("Upload photos showing the completed repair or proof of work.", repair_detail)
        self.assertIn("completion.after_repair_photo_urls", repair_detail)
        self.assertIn("Uploaded After Photos", repair_detail)

    def test_finding_form_uploads_before_photos_separately_from_after_photos(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        repair_detail = (ROOT / "templates" / "pro" / "repair_detail.html").read_text(encoding="utf-8")

        self.assertIn('action="/pro/customers/{{ customer.id }}/vehicles/{{ vehicle.id }}/findings" enctype="multipart/form-data"', vehicle_detail)
        self.assertIn('name="before_inspection_photos"', vehicle_detail)
        self.assertIn("item.before_inspection_photo_urls", vehicle_detail)
        self.assertNotIn('name="after_repair_photos"', vehicle_detail)
        self.assertNotIn('name="before_inspection_photos"', repair_detail)

    def test_workspace_card_does_not_repeat_source_label_in_footer(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        self.assertIn("tm-repair-source-chip", vehicle_detail)
        self.assertIn("Source Record", vehicle_detail)
        self.assertNotIn('tm-repair-work-source-label">{{ item.source_label', vehicle_detail)

    def test_repair_detail_status_open_displays_ready_for_repair(self):
        repair_detail = (ROOT / "templates" / "pro" / "repair_detail.html").read_text(encoding="utf-8")
        self.assertIn('repair_status_display = "Ready for Repair"', repair_detail)
        self.assertIn("{{ repair_status_display }}", repair_detail)
        self.assertIn("repair_display_mileage", repair_detail)

    def test_vehicle_detail_moves_findings_inside_repair_workspace(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        workspace_idx = vehicle_detail.index('id="repair-workspace"')
        timeline_idx = vehicle_detail.index('id="vehicle-timeline"')
        findings_idx = vehicle_detail.index('id="recommendations-findings"')

        self.assertLess(workspace_idx, timeline_idx)
        self.assertLess(workspace_idx, findings_idx)
        self.assertLess(findings_idx, timeline_idx)
        self.assertIn("Active Repair Jobs", vehicle_detail)
        self.assertIn("Additional Findings / Recommended Work", vehicle_detail)
        self.assertIn('aria-label="Saved additional findings and recommended work"', vehicle_detail)
        self.assertIn("Document problems found during inspection or during a repair. Approved recommended repairs become repair jobs.", vehicle_detail)
        self.assertIn("+ Add Finding / Recommended Work", vehicle_detail)
        self.assertIn("Declined / Deferred", vehicle_detail)
        self.assertIn("Completed / Repaired Services", vehicle_detail)
        self.assertNotIn('<h2 style="margin:4px 0 0;">Inspection Findings</h2>', vehicle_detail)

    def test_repair_completion_persists_uploaded_after_photos(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        now = "2026-06-24T12:00:00"

        saved = pro_module.upsert_repair_completion(
            conn,
            repair_record_id=77,
            form={"completion_notes": "Done", "final_inspection_notes": "QA pass"},
            completed_at=now,
            now=now,
            after_repair_photo_paths=["/static/uploads/after-1.jpg"],
        )
        saved = pro_module.upsert_repair_completion(
            conn,
            repair_record_id=77,
            form={"completion_notes": "Done", "final_inspection_notes": "QA pass"},
            completed_at=now,
            now=now,
            after_repair_photo_paths=["/static/uploads/after-2.jpg"],
        )

        self.assertEqual(
            saved["after_repair_photo_urls"],
            ["/static/uploads/after-1.jpg", "/static/uploads/after-2.jpg"],
        )
        self.assertEqual(
            json.loads(saved["after_repair_photo_paths"]),
            ["/static/uploads/after-1.jpg", "/static/uploads/after-2.jpg"],
        )

    def test_estimate_conversion_mobile_controls_and_phone_mask_are_present(self):
        conversion = (ROOT / "templates" / "pro" / "estimate_conversion.html").read_text(encoding="utf-8")

        self.assertIn("tm-convert-choice--selected", conversion)
        self.assertIn("/static/pro_form_helpers.js", conversion)
        self.assertIn('id="new_customer_phone"', conversion)
        self.assertIn("data-pro-phone-input", conversion)
        self.assertIn('id="new_vehicle_mileage"', conversion)
        self.assertIn("data-pro-mileage-input", conversion)
        self.assertIn("Select Existing Customer", conversion)
        self.assertIn("Create New Customer", conversion)
        self.assertIn("Select Existing Vehicle", conversion)
        self.assertIn("Create New Vehicle", conversion)
        self.assertIn('placeholder="Enter current mileage"', conversion)
        self.assertNotIn('placeholder="120,000"', conversion)

    def test_estimate_conversion_payload_preserves_optional_vehicle_mileage(self):
        payload = pro_module.load_estimate_conversion_payload(
            json.dumps(
                {
                    "vehicle": {"year": "2016", "make": "Honda", "model": "Accord", "mileage": "177,000"},
                    "lineItems": [{"serviceText": "Front Brake Pads", "laborHours": 1, "laborRate": 120}],
                }
            )
        )
        blank_payload = pro_module.load_estimate_conversion_payload(
            json.dumps(
                {
                    "vehicle": {"year": "2016", "make": "Honda", "model": "Accord"},
                    "lineItems": [{"serviceText": "Front Brake Pads", "laborHours": 1, "laborRate": 120}],
                }
            )
        )

        self.assertEqual(payload["vehicle"]["mileage"], 177000)
        self.assertIsNone(blank_payload["vehicle"]["mileage"])

    def test_parts_sources_section_is_visible_on_workspace_cards(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")

        self.assertIn("Parts Sources", vehicle_detail)
        self.assertIn("item.parts_sources", vehicle_detail)

    def test_pro_customer_and_vehicle_forms_use_shared_input_formatters(self):
        customers = (ROOT / "templates" / "pro" / "customers.html").read_text(encoding="utf-8")
        customer_detail = (ROOT / "templates" / "pro" / "customer_detail.html").read_text(encoding="utf-8")
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        helper = (ROOT / "static" / "pro_form_helpers.js").read_text(encoding="utf-8")

        self.assertIn("/static/pro_form_helpers.js", customers)
        self.assertIn("/static/pro_form_helpers.js", customer_detail)
        self.assertIn("/static/pro_form_helpers.js", vehicle_detail)
        self.assertIn("data-pro-phone-input", customers)
        self.assertIn("data-pro-phone-input", customer_detail)
        self.assertIn("data-pro-mileage-input", customer_detail)
        self.assertIn('placeholder="Enter current mileage"', customer_detail)
        self.assertIn("data-pro-mileage-input", vehicle_detail)
        self.assertIn("function formatPhone", helper)
        self.assertIn("function formatMileage", helper)
        self.assertIn("normalizeMileageBeforeSubmit", helper)


if __name__ == "__main__":
    unittest.main()
