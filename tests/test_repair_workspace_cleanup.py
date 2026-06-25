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
                "labor_amount": 90,
                "parts_cost": 180,
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
        self.assertEqual(finding_item["labor_total"], 90)
        self.assertEqual(finding_item["parts_total"], 180)
        self.assertEqual(finding_item["grand_total"], 270)
        self.assertTrue(finding_item["has_pricing"])
        self.assertIn("O'Reilly", [source["label"] for source in by_title["Rotate tires"]["parts_sources"]])

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

    def test_approved_finding_creates_source_finding_repair_job_with_estimate_totals(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        pro_module.ensure_findings_records_schema(conn)
        pro_module.ensure_repair_records_schema(conn)
        now = "2026-06-24T12:00:00"
        cur = conn.execute(
            """
            INSERT INTO findings_records (
              customer_id, vehicle_id, request_type, finding, recommendation,
              labor_description, labor_hours, labor_rate, labor_amount, parts_cost,
              labor_reason, severity, status, repair_work_status,
              repair_work_updated_at, mileage, finding_date, created_at
            )
            VALUES (?, ?, 'labor', ?, ?, ?, ?, ?, ?, ?, ?, 'Medium', 'Approved', '', '', ?, ?, ?)
            """,
            (
                1,
                1,
                "Rotors are worn",
                "Front Rotors Replacement",
                "Front Rotors Replacement",
                1.4,
                125,
                175,
                210,
                "Rotor thickness below spec.",
                177000,
                "2026-06-24",
                now,
            ),
        )

        repair_id = pro_module.ensure_repair_record_for_approved_finding(
            conn,
            customer_id=1,
            vehicle_id=1,
            finding_id=int(cur.lastrowid),
            now=now,
        )
        repair = dict(conn.execute("SELECT * FROM repair_records WHERE id = ?", (repair_id,)).fetchone())

        self.assertEqual(repair["repair_name"], "Front Rotors Replacement")
        self.assertEqual(repair["workflow_source_type"], "finding")
        self.assertEqual(repair["status"], "Open")
        self.assertEqual(repair["parts_cost"], 210)
        self.assertEqual(repair["labor_cost"], 175)
        self.assertEqual(repair["total_cost"], 385)

    def test_completed_repair_items_include_totals_source_and_after_photos(self):
        items = pro_module.build_completed_repair_work_items(
            [
                {
                    "id": 30,
                    "customer_id": 1,
                    "vehicle_id": 1,
                    "repair_name": "Replace alternator",
                    "status": "Completed",
                    "workflow_source_type": "estimate",
                    "completed_at": "2026-06-24T12:00:00",
                    "labor_hours": 1.5,
                    "labor_rate": 120,
                    "parts_cost": 220,
                    "completion": {"after_repair_photo_urls": ["/static/uploads/after.jpg"]},
                },
                {
                    "id": 31,
                    "customer_id": 1,
                    "vehicle_id": 1,
                    "repair_name": "Open repair",
                    "status": "Open",
                },
            ]
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_label"], "Source: Estimate")
        self.assertEqual(items[0]["repair_work_status_label"], "Completed")
        self.assertEqual(items[0]["labor_total"], 180)
        self.assertEqual(items[0]["parts_total"], 220)
        self.assertEqual(items[0]["grand_total"], 400)
        self.assertEqual(items[0]["after_repair_photo_urls"], ["/static/uploads/after.jpg"])

    def test_completed_repairs_render_in_vehicle_timeline_repaired_services(self):
        timeline = pro_module.build_vehicle_timeline(
            1,
            1,
            {"id": 1},
            [
                {
                    "id": 40,
                    "source_type": "repair",
                    "source_record_id": 30,
                    "service_date": "2026-06-24",
                    "service_name": "Replace alternator",
                    "mileage": 177000,
                    "total_cost": 400,
                    "created_at": "2026-06-24T12:00:00",
                }
            ],
            [],
            [],
            [],
            [],
            repair_completion_events=[
                {
                    "id": 50,
                    "repair_record_id": 30,
                    "repair_name": "Replace alternator",
                    "completed_at": "2026-06-24T13:00:00",
                    "created_at": "2026-06-24T13:00:00",
                    "workflow_source_type": "estimate",
                    "after_repair_photo_paths": json.dumps(["/static/uploads/after.jpg"]),
                }
            ],
        )

        repaired = timeline[0]
        self.assertEqual(repaired["title"], "Repaired Services")
        self.assertEqual(repaired["records"][0]["service_name"], "Replace alternator")
        self.assertEqual(repaired["records"][0]["source_label"], "Source: Estimate")
        self.assertEqual(repaired["records"][0]["total"], 400)
        self.assertEqual(repaired["records"][0]["photo_count"], 1)
        self.assertEqual(repaired["records"][0]["action_label"], "Open Repair Record")

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
        self.assertIn("Upload up to 5 photos.", vehicle_detail)
        self.assertIn("Upload up to 5 photos.", repair_detail)
        self.assertIn('accept="image/png,image/jpeg,image/webp"', vehicle_detail)
        self.assertIn('accept="image/png,image/jpeg,image/webp"', repair_detail)

    def test_photo_upload_helpers_limit_before_and_after_photos_to_five(self):
        uploads = [
            {"filename": f"photo-{idx}.jpg", "content_type": "image/jpeg", "content": b"img"}
            for idx in range(6)
        ]

        with self.assertRaises(pro_module.HTTPException) as raised:
            pro_module.save_image_upload_paths(
                uploads,
                max_files=pro_module.PHOTO_UPLOAD_MAX_FILES,
                allowed_extensions=pro_module.PHOTO_UPLOAD_ALLOWED_EXTENSIONS,
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Upload up to 5 photos", raised.exception.detail)

    def test_photo_upload_helpers_restrict_completion_photo_types(self):
        with self.assertRaises(pro_module.HTTPException):
            pro_module.save_image_upload_paths(
                [{"filename": "photo.svg", "content_type": "image/svg+xml", "content": b"<svg></svg>"}],
                max_files=pro_module.PHOTO_UPLOAD_MAX_FILES,
                allowed_extensions=pro_module.PHOTO_UPLOAD_ALLOWED_EXTENSIONS,
            )

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
        self.assertIn("Not Invoiced", repair_detail)
        self.assertIn("Invoiced: {{ invoice.invoice_number }}", repair_detail)
        self.assertNotIn("Generate Invoice", repair_detail)
        self.assertIn("Final Inspection Comments", repair_detail)
        self.assertIn('name="final_inspection_passed"', repair_detail)

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
        self.assertIn('aria-label="Expandable additional finding status groups"', vehicle_detail)
        self.assertIn('class="tm-history-summary-card tm-findings-status-card"', vehicle_detail)
        self.assertIn('data-finding-status-group="{{ group.label }}"', vehicle_detail)
        self.assertIn("Create Estimate / Recommended Repair", vehicle_detail)
        self.assertIn("Customer Decision / Update Status", vehicle_detail)
        self.assertIn("Edit Finding", vehicle_detail)
        self.assertIn("build_finding_estimator_href(customer, vehicle, item)", vehicle_detail)
        self.assertNotIn("Save Estimate / Recommended Repair", vehicle_detail)
        self.assertIn("Document problems found during inspection or during a repair. Approved recommended repairs become repair jobs.", vehicle_detail)
        self.assertIn("+ Add Finding / Recommended Work", vehicle_detail)
        self.assertIn("Declined / Deferred", vehicle_detail)
        self.assertIn('"statuses": ["Open"]', vehicle_detail)
        self.assertIn('"statuses": ["Approved"]', vehicle_detail)
        self.assertIn('"statuses": ["Declined", "Deferred"]', vehicle_detail)
        self.assertNotIn('tm-findings-visible-list" aria-label="Saved additional findings and recommended work"', vehicle_detail)
        self.assertNotIn("Completed / Repaired Services", vehicle_detail)
        self.assertNotIn("completed_repair_work_items", vehicle_detail)
        self.assertIn("Vehicle Timeline", vehicle_detail)
        self.assertIn("item.action_label", vehicle_detail)
        self.assertIn("item.photo_count", vehicle_detail)
        self.assertIn("item.source_label", vehicle_detail)
        self.assertNotIn('<h2 style="margin:4px 0 0;">Inspection Findings</h2>', vehicle_detail)

    def test_finding_detail_has_estimate_and_customer_decision_actions(self):
        finding_detail = (ROOT / "templates" / "pro" / "finding_detail.html").read_text(encoding="utf-8")

        self.assertIn("Recommended Repair Estimate", finding_detail)
        self.assertIn("Create Estimate / Recommended Repair", finding_detail)
        self.assertIn("Update Estimate", finding_detail)
        self.assertIn("Customer Decision", finding_detail)
        self.assertIn("Update Customer Decision", finding_detail)
        self.assertIn("Open Source: Finding Repair Job", finding_detail)

    def test_repair_completion_persists_uploaded_after_photos(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        now = "2026-06-24T12:00:00"

        saved = pro_module.upsert_repair_completion(
            conn,
            repair_record_id=77,
            form={"completion_notes": "Done", "final_inspection_passed": "1", "final_inspection_notes": "QA pass"},
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
        self.assertEqual(saved["final_inspection_passed"], 0)
        self.assertEqual(saved["final_inspection_notes"], "QA pass")

    def test_final_inspection_passed_and_legacy_comments_are_preserved(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        now = "2026-06-24T12:00:00"

        saved = pro_module.upsert_repair_completion(
            conn,
            repair_record_id=78,
            form={"final_inspection_passed": "1", "final_inspection_notes": "Old QA notes still visible."},
            completed_at=now,
            now=now,
        )

        self.assertEqual(saved["final_inspection_passed"], 1)
        self.assertEqual(saved["final_inspection_notes"], "Old QA notes still visible.")

    def test_repair_completion_total_after_photos_cannot_exceed_five(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        now = "2026-06-24T12:00:00"

        pro_module.upsert_repair_completion(
            conn,
            repair_record_id=77,
            form={},
            completed_at=now,
            now=now,
            after_repair_photo_paths=[f"/static/uploads/after-{idx}.jpg" for idx in range(5)],
        )

        with self.assertRaises(pro_module.HTTPException):
            pro_module.upsert_repair_completion(
                conn,
                repair_record_id=77,
                form={},
                completed_at=now,
                now=now,
                after_repair_photo_paths=["/static/uploads/after-extra.jpg"],
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

    def test_mileage_formats_with_commas_and_parses_clean(self):
        self.assertEqual(pro_module.format_mileage(120000), "120,000")
        self.assertEqual(pro_module.format_mileage("177000"), "177,000")
        self.assertEqual(pro_module.optional_int({"mileage": "177,000"}, "mileage"), 177000)

    def test_parts_sources_section_is_visible_on_workspace_cards(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")

        self.assertIn("Parts Sources", vehicle_detail)
        self.assertIn("item.parts_sources", vehicle_detail)
        self.assertIn("O'Reilly", pro_module.DEFAULT_PARTS_SOURCE_LABELS)

    def test_pro_customer_and_vehicle_forms_use_shared_input_formatters(self):
        customers = (ROOT / "templates" / "pro" / "customers.html").read_text(encoding="utf-8")
        customer_detail = (ROOT / "templates" / "pro" / "customer_detail.html").read_text(encoding="utf-8")
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        repair_edit = (ROOT / "templates" / "pro" / "repair_edit.html").read_text(encoding="utf-8")
        finding_edit = (ROOT / "templates" / "pro" / "finding_edit.html").read_text(encoding="utf-8")
        maintenance_detail = (ROOT / "templates" / "pro" / "maintenance_detail.html").read_text(encoding="utf-8")
        helper = (ROOT / "static" / "pro_form_helpers.js").read_text(encoding="utf-8")

        self.assertIn("/static/pro_form_helpers.js", customers)
        self.assertIn("/static/pro_form_helpers.js", customer_detail)
        self.assertIn("/static/pro_form_helpers.js", vehicle_detail)
        self.assertIn("data-pro-phone-input", customers)
        self.assertIn("data-pro-phone-input", customer_detail)
        self.assertIn("data-pro-mileage-input", customer_detail)
        self.assertIn('placeholder="Enter current mileage"', customer_detail)
        self.assertIn("data-pro-mileage-input", vehicle_detail)
        self.assertIn("data-pro-mileage-input", repair_edit)
        self.assertIn("data-pro-mileage-input", finding_edit)
        self.assertIn("data-pro-mileage-input", maintenance_detail)
        self.assertIn("/static/pro_form_helpers.js", repair_edit)
        self.assertIn("/static/pro_form_helpers.js", finding_edit)
        self.assertIn("/static/pro_form_helpers.js", maintenance_detail)
        self.assertIn("function formatPhone", helper)
        self.assertIn("function formatMileage", helper)
        self.assertIn("normalizeMileageBeforeSubmit", helper)


if __name__ == "__main__":
    unittest.main()
