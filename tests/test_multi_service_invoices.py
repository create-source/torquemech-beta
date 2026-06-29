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
