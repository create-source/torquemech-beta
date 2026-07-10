import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.pro as pro_module


class NonClosingConnection(sqlite3.Connection):
    def close(self):
        pass

    def close_for_cleanup(self):
        super().close()


class MultiServiceInvoiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(
            ":memory:",
            check_same_thread=False,
            factory=NonClosingConnection,
        )
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close_for_cleanup)
        self.crm_patch = patch.object(pro_module, "crm_db_conn", return_value=self.conn)
        self.crm_patch.start()
        self.addCleanup(self.crm_patch.stop)
        self.create_schema()
        self.seed_customer_vehicle()

    def create_schema(self):
        self.conn.executescript(
            """
            CREATE TABLE customers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              first_name TEXT,
              last_name TEXT,
              phone TEXT,
              email TEXT,
              customer_status TEXT NOT NULL DEFAULT 'active',
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE customer_vehicles (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_id INTEGER NOT NULL,
              year INTEGER,
              make TEXT,
              model TEXT,
              engine TEXT,
              vin TEXT,
              license_plate TEXT,
              mileage INTEGER,
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE repair_completions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repair_record_id INTEGER NOT NULL UNIQUE,
              completion_notes TEXT,
              final_inspection_passed INTEGER NOT NULL DEFAULT 0,
              final_inspection_notes TEXT,
              after_repair_photo_paths TEXT,
              completed_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE findings_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              vehicle_id INTEGER NOT NULL,
              customer_id INTEGER,
              finding TEXT,
              recommendation TEXT,
              status TEXT,
              mileage INTEGER,
              finding_date TEXT,
              created_at TEXT
            );
            """
        )
        pro_module.ensure_repair_records_schema(self.conn)
        pro_module.ensure_invoices_schema(self.conn)

    def seed_customer_vehicle(self):
        now = "2026-06-25T12:00:00"
        self.conn.execute(
            """
            INSERT INTO customers (id, first_name, last_name, phone, email, created_at, updated_at)
            VALUES (1, 'Natalie', 'Htut', '555-0100', 'natalie@test.com', ?, ?)
            """,
            (now, now),
        )
        self.conn.execute(
            """
            INSERT INTO customer_vehicles (id, customer_id, year, make, model, mileage, created_at, updated_at)
            VALUES (1, 1, 2008, 'Toyota', 'Sequoia', 150000, ?, ?)
            """,
            (now, now),
        )
        self.conn.commit()

    def insert_repair(self, repair_id, name, labor_hours, labor_rate, parts, source="estimate", status="Completed", notes=None):
        now = "2026-06-25T12:00:00"
        labor_total = round(labor_hours * labor_rate, 2)
        self.conn.execute(
            """
            INSERT INTO repair_records (
              id, vehicle_id, customer_id, repair_name, repair_date, mileage,
              labor_hours, labor_rate, parts_cost, labor_cost, total_cost,
              workflow_source_type, status, completed_at, notes, created_at
            )
            VALUES (?, 1, 1, ?, '2026-06-25', 150000, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repair_id,
                name,
                labor_hours,
                labor_rate,
                parts,
                labor_total,
                labor_total + parts,
                source,
                status,
                now,
                notes if notes is not None else f"Source: {source.title()}",
                now,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO repair_completions (
              repair_record_id, completion_notes, final_inspection_passed, final_inspection_notes,
              after_repair_photo_paths, completed_at, created_at, updated_at
            )
            VALUES (?, 'Done', 1, 'Final check passed', '[]', ?, ?, ?)
            """,
            (repair_id, now, now, now),
        )
        self.conn.commit()

    def final_invoice_pdf(self, invoice, shop_profile=None):
        loaded = pro_module.load_invoice_record(self.conn, 1, 1, invoice["id"])
        customer = pro_module.row_to_dict(
            self.conn.execute("SELECT * FROM customers WHERE id = 1").fetchone()
        )
        vehicle = pro_module.row_to_dict(
            self.conn.execute("SELECT * FROM customer_vehicles WHERE id = 1").fetchone()
        )
        return pro_module.build_invoice_pdf_bytes(
            invoice=loaded,
            customer=customer,
            vehicle=vehicle,
            shop_name=(shop_profile or {}).get("shop_name") or "TorqueMech Auto",
            shop_profile=shop_profile or {
                "shop_name": "TorqueMech Auto",
                "shop_address": "123 Service Way, Fresno, CA",
                "shop_phone": "555-0199",
                "shop_email": "service@torquemech.test",
            },
        )

    def test_create_invoice_from_multiple_repair_jobs(self):
        self.insert_repair(10, "Front Brake Pads Replacement", 1.2, 125, 115)
        self.insert_repair(11, "Front Brake Rotors Replacement", 1.5, 140, 180, "finding")
        repairs = [
            pro_module.load_repair_record(self.conn, 1, 1, 10),
            pro_module.load_repair_record(self.conn, 1, 1, 11),
        ]

        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=repairs,
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T12:30:00",
        )
        self.conn.commit()

        self.assertEqual(invoice["service_count"], 2)
        self.assertEqual(invoice["invoice_number"], "TM-INV-0001")
        self.assertEqual(invoice["labor_total"], 360)
        self.assertEqual(invoice["parts_total"], 295)
        self.assertEqual(invoice["grand_total"], 655)
        self.assertEqual([item["service_title"] for item in invoice["items"]], [
            "Front Brake Pads Replacement",
            "Front Brake Rotors Replacement",
        ])
        self.assertEqual(pro_module.load_invoice_for_repair(self.conn, 10)["id"], invoice["id"])
        self.assertEqual(pro_module.load_invoice_for_repair(self.conn, 11)["id"], invoice["id"])

    def test_invoice_detail_and_pdf_support_multiple_services(self):
        self.test_create_invoice_from_multiple_repair_jobs()
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        detail = client.get("/pro/customers/1/vehicles/1/invoices/1")
        pdf = client.get("/pro/customers/1/vehicles/1/invoices/1/pdf?show_labor_hours=1&show_labor_rate=1")

        self.assertEqual(detail.status_code, 200)
        self.assertIn("Invoice TM-INV-0001", detail.text)
        self.assertIn("<strong>TM-INV-0001</strong>", detail.text)
        self.assertIn("Completed Services", detail.text)
        self.assertIn("Front Brake Pads Replacement", detail.text)
        self.assertIn("Front Brake Rotors Replacement", detail.text)
        self.assertIn("Final Inspection", detail.text)
        self.assertIn("Passed", detail.text)
        self.assertEqual(detail.text.count("Final Inspection"), 1)
        self.assertNotIn("Source: Finding", detail.text)
        self.assertNotIn("Recommended Repair: Replace", detail.text)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn(b"TM-INV-0001", pdf.content)
        self.assertNotIn(b"Source: Finding", pdf.content)

    def test_invoice_shows_tracked_parts_in_final_total_and_estimate_difference(self):
        self.insert_repair(60, "Radio Antenna Replacement", 1.0, 120, 0, source="finding")
        self.conn.execute("UPDATE repair_records SET workflow_source_id = 7 WHERE id = 60")
        pro_module.ensure_repair_estimate_documents_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO repair_estimate_documents (
              id, customer_id, vehicle_id, finding_id, estimate_date,
              customer_name, vehicle_label, related_title, estimate_total,
              approval_status, pdf_path, invoice_id, payload_json, created_at
            )
            VALUES (
              12, 1, 1, 7, '2026-06-25',
              'Natalie Htut', '2008 Toyota Sequoia', 'Radio Antenna Replacement', 120,
              'Signed customer approval', 'estimate.pdf', NULL, '{}', '2026-06-25T12:05:00'
            )
            """
        )
        pro_module.create_repair_job_part(
            self.conn,
            60,
            {
                "part_name": "Radio Antenna",
                "qty": "1",
                "unit_cost": "45",
                "vendor": "OEM",
                "part_number": "ANT-1",
                "status": "Installed",
            },
            "2026-06-25T12:10:00",
        )
        repair = pro_module.load_repair_record(self.conn, 1, 1, 60)

        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=[repair],
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T12:30:00",
        )
        self.conn.commit()
        loaded = pro_module.load_invoice_record(self.conn, 1, 1, invoice["id"])

        self.assertEqual(loaded["labor_total"], 120)
        self.assertEqual(loaded["parts_total"], 45)
        self.assertEqual(loaded["grand_total"], 165)
        self.assertEqual(loaded["approved_estimate_total"], 120)
        self.assertEqual(loaded["estimate_final_difference"], 45)
        self.assertEqual(loaded["items"][0]["tracked_parts_total"], 45)
        self.assertEqual(loaded["items"][0]["tracked_parts"][0]["part_name"], "Radio Antenna")

        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        detail = client.get("/pro/customers/1/vehicles/1/invoices/1")
        pdf = client.get("/pro/customers/1/vehicles/1/invoices/1/pdf")

        self.assertEqual(detail.status_code, 200)
        self.assertIn("Tracked Parts", detail.text)
        self.assertIn("Radio Antenna", detail.text)
        self.assertIn("added to final total", detail.text)
        self.assertIn("<span>Parts</span><strong>$45.00</strong>", detail.text)
        self.assertIn("Approved Estimate Total", detail.text)
        self.assertIn("Final Invoice Total", detail.text)
        self.assertIn("Additional Approved Amount", detail.text)
        self.assertEqual(pdf.status_code, 200)
        self.assertIn(b"Radio Antenna", pdf.content)
        self.assertIn(b"Approved Estimate Total", pdf.content)
        self.assertIn(b"Invoice Total", pdf.content)

    def test_invoice_estimate_difference_is_zero_when_final_matches_approval(self):
        self.insert_repair(62, "Brake Inspection", 1.0, 120, 20, source="finding")
        self.conn.execute("UPDATE repair_records SET workflow_source_id = 8 WHERE id = 62")
        pro_module.ensure_repair_estimate_documents_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO repair_estimate_documents (
              id, customer_id, vehicle_id, finding_id, estimate_date,
              customer_name, vehicle_label, related_title, estimate_total,
              approval_status, pdf_path, invoice_id, payload_json, created_at
            )
            VALUES (
              13, 1, 1, 8, '2026-06-25',
              'Natalie Htut', '2008 Toyota Sequoia', 'Brake Inspection', 140,
              'Signed customer approval', 'estimate.pdf', NULL, '{}', '2026-06-25T12:05:00'
            )
            """
        )
        repair = pro_module.load_repair_record(self.conn, 1, 1, 62)

        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=[repair],
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T12:30:00",
        )
        loaded = pro_module.load_invoice_record(self.conn, 1, 1, invoice["id"])

        self.assertEqual(loaded["grand_total"], 140)
        self.assertEqual(loaded["approved_estimate_total"], 140)
        self.assertEqual(loaded["estimate_final_difference"], 0)

    def test_parts_tracking_add_part_default_uses_parts_search_term(self):
        self.insert_repair(61, "Coolant Drain & Refill", 1.0, 120, 0, status="Open")
        self.conn.execute(
            "UPDATE repair_records SET parts_search_term = 'engine coolant' WHERE id = 61"
        )
        self.conn.commit()
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        detail = client.get("/pro/customers/1/vehicles/1/repairs/61")

        self.assertEqual(detail.status_code, 200)
        self.assertIn('name="part_name" type="text" maxlength="180" value="Engine Coolant"', detail.text)
        self.assertNotIn("And Refill", detail.text)

    def test_parts_tracking_add_part_default_does_not_use_leftover_fragments(self):
        self.insert_repair(62, "Coolant Drain & Refill", 1.0, 120, 0, status="Open")
        self.conn.execute(
            "UPDATE repair_records SET parts_search_term = 'And Refill' WHERE id = 62"
        )
        self.insert_repair(63, "Mystery Calibration Replacement", 1.0, 120, 0, status="Open")
        self.conn.commit()
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        coolant_detail = client.get("/pro/customers/1/vehicles/1/repairs/62")
        unknown_detail = client.get("/pro/customers/1/vehicles/1/repairs/63")

        self.assertEqual(coolant_detail.status_code, 200)
        self.assertIn('name="part_name" type="text" maxlength="180" value="Engine Coolant"', coolant_detail.text)
        self.assertNotIn('value="And Refill"', coolant_detail.text)
        self.assertNotIn('value="Drain &amp; Refill"', coolant_detail.text)
        self.assertEqual(unknown_detail.status_code, 200)
        self.assertIn('name="part_name" type="text" maxlength="180" value=""', unknown_detail.text)
        self.assertNotIn('value="Replacement"', unknown_detail.text)
        self.assertNotIn('value="Service"', unknown_detail.text)

    def test_invoice_notes_hide_internal_generated_text(self):
        self.conn.execute(
            """
            INSERT INTO findings_records (
              id, vehicle_id, customer_id, finding, recommendation, status, mileage, finding_date, created_at
            )
            VALUES (99, 1, 1, 'Cylinder misfire', 'Replace', 'Completed', 150000, '2026-06-25', '2026-06-25T12:00:00')
            """
        )
        self.insert_repair(
            12,
            "Ignition Coil Replacement",
            1.0,
            150,
            45,
            "finding",
            notes="Source: Finding Recommended Repair: Replace Recommended Repair: Replace",
        )
        self.conn.execute("UPDATE repair_records SET workflow_source_id = 99 WHERE id = 12")
        repair = pro_module.load_repair_record(self.conn, 1, 1, 12)

        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=[repair],
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T12:30:00",
        )

        self.assertEqual(invoice["items"][0]["repair_notes"], "")
        self.assertNotIn("Source: Finding", str(invoice))
        self.assertNotIn("Recommended Repair", str(invoice))

    def test_quantity_service_title_is_not_duplicated_on_invoice_and_cards(self):
        quantity_marker = chr(215)
        duplicated_title = f"Spark Plug Replacement {quantity_marker} 4 {quantity_marker} 4"
        expected_title = f"Spark Plug Replacement {quantity_marker} 4"
        self.insert_repair(13, duplicated_title, 1.8, 125, 48)
        repair = pro_module.load_repair_record(self.conn, 1, 1, 13)

        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=[repair],
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T12:30:00",
        )
        self.conn.commit()

        self.assertEqual(invoice["items"][0]["service_title"], expected_title)
        self.assertNotIn(f"{quantity_marker} 4 {quantity_marker} 4", invoice["items"][0]["service_title"])
        self.assertEqual(invoice["labor_total"], 225)
        self.assertEqual(invoice["parts_total"], 48)
        self.assertEqual(invoice["grand_total"], 273)

        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        detail = client.get("/pro/customers/1/vehicles/1/invoices/1")
        vehicle_detail = client.get("/pro/customers/1/vehicles/1")

        self.assertEqual(detail.status_code, 200)
        self.assertIn(expected_title, detail.text)
        self.assertNotIn(f"{quantity_marker} 4 {quantity_marker} 4", detail.text)
        self.assertEqual(vehicle_detail.status_code, 200)
        self.assertIn(expected_title, vehicle_detail.text)
        self.assertNotIn(f"{quantity_marker} 4 {quantity_marker} 4", vehicle_detail.text)

    def test_empty_repair_workspace_hides_timeline_and_invoice_header_actions(self):
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        vehicle_detail = client.get("/pro/customers/1/vehicles/1")

        self.assertEqual(vehicle_detail.status_code, 200)
        self.assertIn('id="vehicle-timeline"', vehicle_detail.text)
        self.assertNotIn('href="#vehicle-timeline">Vehicle Timeline</a>', vehicle_detail.text)
        workspace_start = vehicle_detail.text.index('id="repair-workspace"')
        workspace_end = vehicle_detail.text.index('id="recommendations-findings"')
        workspace_html = vehicle_detail.text[workspace_start:workspace_end]
        self.assertNotIn("Create Final Invoice", workspace_html)

    def test_old_single_job_invoice_still_loads_as_one_service(self):
        self.insert_repair(20, "Alternator Replacement", 1.5, 120, 220)
        self.conn.execute(
            """
            INSERT INTO invoices (
              id, invoice_number, repair_record_id, customer_id, vehicle_id,
              labor_total, parts_total, grand_total, created_at
            )
            VALUES (7, 'INV-20260625-0007', 20, 1, 1, 180, 220, 400, '2026-06-25T13:00:00')
            """
        )
        self.conn.commit()

        invoice = pro_module.load_invoice_record(self.conn, 1, 1, 7)

        self.assertEqual(invoice["service_count"], 1)
        self.assertEqual(invoice["invoice_number"], "INV-20260625-0007")
        self.assertEqual(invoice["items"][0]["service_title"], "Alternator Replacement")
        self.assertEqual(invoice["grand_total"], 400)

    def test_invoice_number_continues_from_existing_tm_inv_numbers(self):
        self.insert_repair(21, "Battery Replacement", 0.5, 120, 185)
        self.insert_repair(22, "Starter Replacement", 1.5, 120, 225)
        self.conn.execute(
            """
            INSERT INTO invoices (
              id, invoice_number, repair_record_id, customer_id, vehicle_id,
              labor_total, parts_total, grand_total, created_at
            )
            VALUES (8, 'TM-INV-0007', 21, 1, 1, 60, 185, 245, '2026-06-25T13:00:00')
            """
        )
        repair = pro_module.load_repair_record(self.conn, 1, 1, 22)

        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=[repair],
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T14:00:00",
        )

        self.assertEqual(invoice["invoice_number"], "TM-INV-0008")

    def test_invoice_number_uses_invoice_id_when_only_date_legacy_numbers_exist(self):
        self.insert_repair(23, "Alternator Replacement", 1.5, 120, 220)
        self.insert_repair(24, "Serpentine Belt Replacement", 0.8, 120, 65)
        self.conn.execute(
            """
            INSERT INTO invoices (
              id, invoice_number, repair_record_id, customer_id, vehicle_id,
              labor_total, parts_total, grand_total, created_at
            )
            VALUES (7, 'INV-20260625-0007', 23, 1, 1, 180, 220, 400, '2026-06-25T13:00:00')
            """
        )
        repair = pro_module.load_repair_record(self.conn, 1, 1, 24)

        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=[repair],
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T14:00:00",
        )

        self.assertEqual(invoice["invoice_number"], "TM-INV-0008")

    def test_repair_completion_redirects_to_repair_workspace(self):
        self.insert_repair(30, "Water Pump Replacement", 2.0, 120, 180)
        self.conn.execute("UPDATE repair_records SET status = 'Open', completed_at = NULL WHERE id = 30")
        self.conn.commit()
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        response = client.post(
            "/pro/customers/1/vehicles/1/repairs/30/completion",
            data={
                "completion_date": "",
                "completion_mileage": "151,000",
                "technician_notes": "Road tested.",
                "completion_notes": "Ready.",
                "final_inspection_passed": "1",
                "final_inspection_notes": "Passed.",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/pro/customers/1/vehicles/1#repair-workspace")
        completion = pro_module.load_repair_completion(self.conn, 30)
        repair = pro_module.load_repair_record(self.conn, 1, 1, 30)
        self.assertEqual(completion["completion_date"], "")
        self.assertEqual(completion["completion_mileage"], 151000)
        self.assertEqual(repair["mileage"], 151000)
        self.assertEqual(repair["status"], "Completed")

    def test_repair_completion_validates_required_fields(self):
        self.insert_repair(31, "Brake Fluid Flush", 1.0, 120, 20, status="Open")
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        response = client.post(
            "/pro/customers/1/vehicles/1/repairs/31/completion",
            data={
                "completion_date": "",
                "completion_mileage": "",
                "technician_notes": "",
                "completion_notes": "",
                "final_inspection_notes": "",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Completion mileage is required", response.text)
        self.assertIn("Completion notes are required", response.text)
        self.assertIn("Final inspection must be marked passed", response.text)
        repair = pro_module.load_repair_record(self.conn, 1, 1, 31)
        self.assertEqual(repair["status"], "Open")

    def test_repair_completion_updates_linked_appointment_service_history_and_timeline(self):
        pro_module.ensure_calendar_schema(self.conn)
        self.insert_repair(32, "Coolant Flush", 1.0, 120, 60, status="Open")
        self.conn.execute(
            """
            INSERT INTO service_appointments (
              id, customer_id, vehicle_id, repair_id, customer_name, customer_phone,
              service_name, requested_date, requested_time, status, created_at, updated_at
            )
            VALUES (
              9, 1, 1, 32, 'Natalie Htut', '555-0100', 'Coolant Flush',
              '2026-06-25', '09:00', 'Converted', '2026-06-25T08:00:00', '2026-06-25T08:00:00'
            )
            """
        )
        self.conn.commit()
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        response = client.post(
            "/pro/customers/1/vehicles/1/repairs/32/completion",
            data={
                "completion_date": "2026-06-25",
                "completion_mileage": "151,500",
                "technician_notes": "Pressure tested.",
                "completion_notes": "Coolant flush completed.",
                "final_inspection_passed": "1",
                "final_inspection_notes": "No leaks.",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

        appointment = dict(self.conn.execute("SELECT * FROM service_appointments WHERE id = 9").fetchone())
        self.assertEqual(appointment["status"], "Completed")
        history = self.conn.execute(
            "SELECT * FROM service_history_records WHERE source_type = 'repair' AND source_record_id = 32"
        ).fetchall()
        self.assertEqual(len(history), 1)
        vehicle_detail = client.get("/pro/customers/1/vehicles/1")
        self.assertEqual(vehicle_detail.status_code, 200)
        self.assertIn("Completed Repairs", vehicle_detail.text)
        self.assertIn("Coolant Flush", vehicle_detail.text)

    def test_full_pro_finding_to_completion_to_invoice_workflow(self):
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        finding_response = client.post(
            "/pro/customers/1/vehicles/1/findings",
            data={
                "request_type": "labor",
                "finding": "Front brake vibration",
                "recommendation": "Replace front brake pads",
                "labor_description": "Front Brake Pads Replacement",
                "labor_hours": "1.2",
                "labor_rate": "125",
                "parts_cost": "115",
                "labor_reason": "Pads below service limit.",
                "severity": "Medium",
                "status": "Approved",
                "mileage": "150,000",
            },
            follow_redirects=False,
        )

        self.assertEqual(finding_response.status_code, 303)
        repair = dict(self.conn.execute("SELECT * FROM repair_records ORDER BY id DESC LIMIT 1").fetchone())
        self.assertEqual(repair["status"], "Open")
        self.assertEqual(repair["workflow_source_type"], "finding")

        repair_detail = client.get(f"/pro/customers/1/vehicles/1/repairs/{repair['id']}")
        self.assertEqual(repair_detail.status_code, 200)
        self.assertIn("Status Approved", repair_detail.text)

        completion_response = client.post(
            f"/pro/customers/1/vehicles/1/repairs/{repair['id']}/completion",
            data={
                "completion_date": "",
                "completion_mileage": "151,250",
                "technician_notes": "Road tested.",
                "completion_notes": "Brake vibration resolved.",
                "final_inspection_passed": "1",
                "final_inspection_notes": "Final check passed.",
            },
            follow_redirects=False,
        )
        self.assertEqual(completion_response.status_code, 303)
        self.assertEqual(completion_response.headers["location"], "/pro/customers/1/vehicles/1#repair-workspace")
        completion = pro_module.load_repair_completion(self.conn, repair["id"])
        self.assertEqual(completion["completion_date"], "")

        repairs = [dict(row) for row in self.conn.execute("SELECT * FROM repair_records").fetchall()]
        findings = [dict(row) for row in self.conn.execute("SELECT * FROM findings_records").fetchall()]
        repair_work_items = pro_module.build_repair_work_items({"id": 1, "mileage": 151250}, findings, [], repairs)
        groups = pro_module.build_repair_workspace_groups({"id": 1}, repair_work_items, repairs)
        self.assertEqual(groups["active"], [])
        self.assertEqual(groups["recently_completed"][0]["workspace_status_label"], "Completed")

        invoice_response = client.post(
            "/pro/customers/1/vehicles/1/invoices",
            data={"repair_record_id": str(repair["id"])},
            follow_redirects=False,
        )
        self.assertEqual(invoice_response.status_code, 303)
        self.assertEqual(invoice_response.headers["location"], "/pro/customers/1/vehicles/1/invoices/1")

        invoice_detail = client.get("/pro/customers/1/vehicles/1/invoices/1")
        invoice_pdf = client.get("/pro/customers/1/vehicles/1/invoices/1/pdf")
        vehicle_detail = client.get("/pro/customers/1/vehicles/1")

        self.assertEqual(invoice_detail.status_code, 200)
        self.assertIn("Invoice TM-INV-0001", invoice_detail.text)
        self.assertIn("Download PDF", invoice_detail.text)
        self.assertEqual(invoice_pdf.status_code, 200)
        self.assertEqual(vehicle_detail.status_code, 200)
        self.assertIn("Completed Repairs", vehicle_detail.text)
        self.assertIn("Front Brake Pads Replacement", vehicle_detail.text)
        self.assertIn("Invoice TM-INV-0001", vehicle_detail.text)
        self.assertIn("Open Final Invoice", vehicle_detail.text)

    def test_duplicate_invoice_actions_reuse_existing_invoice(self):
        self.insert_repair(70, "Water Pump Replacement", 2.0, 120, 180)
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        first = client.post(
            "/pro/customers/1/vehicles/1/invoices",
            data={"repair_record_id": "70"},
            follow_redirects=False,
        )
        second = client.post(
            "/pro/customers/1/vehicles/1/invoices",
            data={"repair_record_id": "70"},
            follow_redirects=False,
        )

        self.assertEqual(first.status_code, 303)
        self.assertEqual(second.status_code, 303)
        self.assertEqual(first.headers["location"], "/pro/customers/1/vehicles/1/invoices/1")
        self.assertEqual(second.headers["location"], "/pro/customers/1/vehicles/1/invoices/1")
        invoice_count = self.conn.execute("SELECT COUNT(*) AS count FROM invoices").fetchone()["count"]
        self.assertEqual(invoice_count, 1)
        builder = client.get("/pro/customers/1/vehicles/1/invoices/new")
        self.assertIn("Open Final Invoice", builder.text.replace("View Invoice", "Open Final Invoice"))

    def test_final_invoice_uses_stable_configured_totals_and_warranty(self):
        pro_module.save_shop_settings(
            self.conn,
            {
                "shop_name": "TorqueMech Auto",
                "use_tax_rate": "1",
                "tax_rate": "0.08",
                "shop_supplies_fee": "12.50",
                "warranty_note": "12 month warranty on listed labor.",
            },
        )
        self.insert_repair(71, "Alternator Replacement", 1.5, 120, 220)
        repair = pro_module.load_repair_record(self.conn, 1, 1, 71)

        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=[repair],
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T14:00:00",
        )

        self.assertEqual(invoice["invoice_number"], "TM-INV-0001")
        self.assertEqual(invoice["labor_total"], 180)
        self.assertEqual(invoice["parts_total"], 220)
        self.assertEqual(invoice["shop_supplies_fee"], 12.5)
        self.assertEqual(invoice["tax_total"], 33)
        self.assertEqual(invoice["grand_total"], 445.5)
        self.assertEqual(invoice["payment_status"], "Unpaid")
        self.assertIn("12 month warranty", invoice["warranty_text"])

    def test_final_invoice_pdf_contains_customer_vehicle_and_no_estimate_language(self):
        self.conn.execute(
            """
            UPDATE customer_vehicles
            SET vin = '5TDBY64A08S123456',
                license_plate = '7TMQ123',
                mileage = 151250
            WHERE id = 1
            """
        )
        self.insert_repair(81, "Battery and Charging System Service", 1.25, 135, 180, notes="Customer-facing completion note.")
        repair = pro_module.load_repair_record(self.conn, 1, 1, 81)
        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=[repair],
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T15:00:00",
        )

        pdf = self.final_invoice_pdf(invoice)

        self.assertIn(b"Final Invoice", pdf)
        self.assertIn(b"TM-INV-0001", pdf)
        self.assertIn(b"Natalie Htut", pdf)
        self.assertIn(b"555-0100 | natalie@test.com", pdf)
        self.assertIn(b"2008 Toyota Sequoia", pdf)
        self.assertIn(b"5TDBY64A08S123456 | 7TMQ123", pdf)
        self.assertIn(b"Battery and Charging System Service", pdf)
        self.assertIn(b"Customer-facing completion note.", pdf)
        self.assertIn(b"Final customer invoice", pdf)
        self.assertNotIn(b"Estimated Total", pdf)
        self.assertNotIn(b"Final pricing may vary", pdf)
        self.assertNotIn(b"Prepared for customer review", pdf)
        self.assertNotIn(b"Status: Recommended", pdf)
        self.assertNotIn(b"No payment is collected", pdf)

    def test_final_invoice_pdf_accounting_totals_and_payment_statuses(self):
        pro_module.save_shop_settings(
            self.conn,
            {
                "shop_name": "TorqueMech Auto",
                "use_tax_rate": "1",
                "tax_rate": "0.08",
                "shop_supplies_fee": "12.50",
                "warranty_note": "12 month warranty on listed labor.",
            },
        )
        self.insert_repair(82, "Front Brake Service", 2.0, 150, 320)
        repair = pro_module.load_repair_record(self.conn, 1, 1, 82)
        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=[repair],
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T15:00:00",
        )
        self.conn.execute(
            """
            UPDATE invoices
            SET discount_total = 25,
                amount_paid = 200,
                payment_status = 'Partially Paid'
            WHERE id = ?
            """,
            (invoice["id"],),
        )
        self.conn.commit()

        partial_pdf = self.final_invoice_pdf(invoice)
        self.assertIn(b"Labor Subtotal", partial_pdf)
        self.assertIn(b"$300.00", partial_pdf)
        self.assertIn(b"Parts Subtotal", partial_pdf)
        self.assertIn(b"$320.00", partial_pdf)
        self.assertIn(b"Fees / Shop Supplies", partial_pdf)
        self.assertIn(b"$12.50", partial_pdf)
        self.assertIn(b"Tax", partial_pdf)
        self.assertIn(b"$50.60", partial_pdf)
        self.assertIn(b"Discount", partial_pdf)
        self.assertIn(b"$-25.00", partial_pdf)
        self.assertIn(b"Partially Paid", partial_pdf)
        self.assertIn(b"Amount Paid", partial_pdf)
        self.assertIn(b"Balance Due", partial_pdf)
        self.assertIn(b"12 month warranty on listed labor.", partial_pdf)

        self.conn.execute(
            "UPDATE invoices SET amount_paid = grand_total, payment_status = 'Paid in Full' WHERE id = ?",
            (invoice["id"],),
        )
        self.conn.commit()
        paid_pdf = self.final_invoice_pdf(invoice)
        self.assertIn(b"Paid in Full", paid_pdf)
        self.assertNotIn(b"Balance Due) Tj", paid_pdf)

        self.conn.execute(
            "UPDATE invoices SET amount_paid = 0, payment_status = 'Unpaid' WHERE id = ?",
            (invoice["id"],),
        )
        self.conn.commit()
        unpaid_pdf = self.final_invoice_pdf(invoice)
        self.assertIn(b"Payment Status: Unpaid", unpaid_pdf)
        self.assertIn(b"Balance Due", unpaid_pdf)

    def test_final_invoice_pdf_includes_additional_approved_parts_and_excludes_declined(self):
        self.insert_repair(83, "Cooling System Repair", 2.4, 145, 90)
        self.insert_repair(84, "Declined Cabin Filter", 0.3, 120, 45, status="Declined")
        pro_module.create_repair_job_part(
            self.conn,
            83,
            {
                "part_name": "Upper Radiator Hose",
                "qty": "2",
                "unit_cost": "32.50",
                "status": "approved",
                "notes": "Approved during repair.",
            },
            "2026-06-25T15:05:00",
        )
        self.conn.commit()
        repair = pro_module.load_repair_record(self.conn, 1, 1, 83)
        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=[repair],
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T15:10:00",
        )

        pdf = self.final_invoice_pdf(invoice)

        self.assertIn(b"Cooling System Repair", pdf)
        self.assertIn(b"Upper Radiator Hose", pdf)
        self.assertIn(b"$155.00", pdf)
        self.assertNotIn(b"Declined Cabin Filter", pdf)

    def test_final_invoice_pdf_paginates_long_invoices(self):
        repairs = []
        for repair_id in range(100, 136):
            self.insert_repair(repair_id, f"Approved Service Line {repair_id}", 0.7, 110, 35)
            repairs.append(pro_module.load_repair_record(self.conn, 1, 1, repair_id))
        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=repairs,
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T16:00:00",
        )

        pdf = self.final_invoice_pdf(invoice)

        self.assertIn(b"Approved Service Line 100", pdf)
        self.assertIn(b"Approved Service Line 135", pdf)
        self.assertIn(b"Page 2", pdf)
        self.assertGreaterEqual(pdf.count(b"Service / Repair Description"), 2)

    def test_missing_linked_record_fallback_and_already_invoiced_collapsed(self):
        invoice_template = (pro_module.TEMPLATES_DIR / "pro" / "invoice_detail.html").read_text(encoding="utf-8")
        builder_template = (pro_module.TEMPLATES_DIR / "pro" / "invoice_builder.html").read_text(encoding="utf-8")

        self.assertIn("Customer unavailable", invoice_template)
        self.assertIn("Vehicle unavailable", invoice_template)
        self.assertIn("Shop information not configured", invoice_template)
        self.assertIn("No warranty text configured.", invoice_template)
        self.assertIn('<details class="tm-invoice-builder-panel" aria-label="Already Invoiced"', builder_template)
        self.assertNotIn('<details class="tm-invoice-builder-panel" aria-label="Already Invoiced" open', builder_template)

    def test_approved_finding_without_estimate_keeps_create_estimate_cta(self):
        pro_module.ensure_findings_records_schema(self.conn)
        self.insert_repair(
            35,
            "Water Pump Replacement",
            1.8,
            125,
            160,
            source="finding",
            status="Open",
        )
        self.conn.execute(
            """
            UPDATE repair_records
            SET workflow_source_id = 77
            WHERE id = 35
            """
        )
        self.conn.execute(
            """
            INSERT INTO findings_records (
              id, vehicle_id, customer_id, finding, recommendation, request_type,
              severity, status, repair_work_status, linked_repair_record_id,
              mileage, finding_date, created_at
            )
            VALUES (
              77, 1, 1, 'Coolant leak at water pump', 'Water Pump Replacement',
              'finding', 'Medium', 'Approved', 'ready', 35, 150000,
              '2026-06-25', '2026-06-25T12:00:00'
            )
            """
        )
        self.conn.commit()

        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        vehicle_detail = client.get("/pro/customers/1/vehicles/1")

        self.assertEqual(vehicle_detail.status_code, 200)
        self.assertIn("Coolant leak at water pump", vehicle_detail.text)
        self.assertIn("finding_id=77", vehicle_detail.text)
        self.assertIn("Create Estimate", vehicle_detail.text)
        self.assertIn("Open Repair", vehicle_detail.text)
        self.assertNotIn("Open Source: Finding Repair Job", vehicle_detail.text)

    def test_invoice_creation_rejects_open_declined_deferred_and_approved_work(self):
        statuses = ["Open", "Approved", "Declined", "Deferred"]
        for idx, status in enumerate(statuses, start=40):
            self.insert_repair(idx, f"{status} Repair", 1.0, 100, 10, status=status)
            repair = pro_module.load_repair_record(self.conn, 1, 1, idx)
            with self.assertRaises(pro_module.HTTPException):
                pro_module.create_invoice_for_repairs(
                    self.conn,
                    repairs=[repair],
                    customer_id=1,
                    vehicle_id=1,
                    now="2026-06-25T12:30:00",
                )


if __name__ == "__main__":
    unittest.main()
