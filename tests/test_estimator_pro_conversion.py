import json
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


class EstimatorProConversionTests(unittest.TestCase):
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
        self.create_minimal_pro_schema()

    def create_minimal_pro_schema(self):
        conn = self.conn
        conn.executescript(
            """
            CREATE TABLE customers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              shop_id INTEGER,
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
              shop_id INTEGER,
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
            CREATE TABLE service_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_id INTEGER NOT NULL,
              vehicle_id INTEGER NOT NULL,
              shop_id INTEGER,
              service_title TEXT,
              service_date TEXT,
              mileage_at_service INTEGER,
              service_notes TEXT,
              labor_amount REAL,
              parts_amount REAL,
              estimate_total REAL,
              actual_total REAL,
              created_at TEXT,
              updated_at TEXT
            );
            CREATE TABLE maintenance_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_id INTEGER NOT NULL,
              vehicle_id INTEGER NOT NULL,
              shop_id INTEGER,
              service_type TEXT,
              date_performed TEXT,
              mileage_performed INTEGER,
              interval_miles INTEGER,
              interval_months INTEGER,
              due_mileage INTEGER,
              due_date TEXT,
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE findings_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              vehicle_id INTEGER NOT NULL,
              customer_id INTEGER,
              request_type TEXT,
              finding TEXT,
              recommendation TEXT,
              status TEXT,
              mileage INTEGER,
              finding_date TEXT,
              labor_description TEXT,
              labor_hours REAL,
              labor_rate REAL,
              labor_amount REAL,
              labor_reason TEXT,
              parts_cost REAL,
              created_at TEXT
            );
            """
        )
        conn.commit()

    def conversion_payload(self):
        return {
            "source": "estimator",
            "vehicle": {
                "year": "2016",
                "make": "Honda",
                "model": "Accord",
            },
            "notes": "Customer requested quote.",
            "lineItems": [
                {
                    "serviceText": "Replace front brake pads",
                    "laborHours": 1.2,
                    "laborRate": 125,
                    "laborTotal": 150,
                    "partsTotal": 115,
                    "grandTotal": 265,
                }
            ],
        }

    def finding_conversion_payload(self):
        payload = self.conversion_payload()
        payload.update(
            {
                "source": "finding",
                "customerId": "1",
                "vehicleId": "1",
                "findingId": "1",
                "sourceContext": {
                    "source": "finding",
                    "customerName": "Natalie Htut",
                    "problemFound": "Rotors are worn",
                    "recommendedRepair": "Front Rotors Replacement",
                },
                "vehicle": {
                    "year": "2008",
                    "make": "Toyota",
                    "model": "Sequoia",
                },
                "lineItems": [
                    {
                        "serviceText": "Front Rotors Replacement",
                        "laborHours": 1.5,
                        "laborRate": 140,
                        "laborTotal": 210,
                        "partsTotal": 180,
                        "grandTotal": 390,
                    }
                ],
            }
        )
        return payload

    def test_conversion_creates_repair_record_without_finding_or_completed_history(self):
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        form_data = {
            "estimate_payload": json.dumps(self.conversion_payload()),
            "customer_mode": "new",
            "new_customer_name": "Mike Johnson",
            "new_customer_phone": "555-222-1111",
            "new_customer_email": "mike@test.com",
            "vehicle_mode": "new",
            "new_vehicle_year": "2016",
            "new_vehicle_make": "Honda",
            "new_vehicle_model": "Accord",
            "new_vehicle_mileage": "120,000",
            "service_index": "0",
        }

        response = client.post(
            "/pro/estimate-conversion/create",
            data=form_data,
            follow_redirects=False,
        )
        duplicate_form_data = {
            "estimate_payload": json.dumps(self.conversion_payload()),
            "customer_mode": "existing",
            "customer_id": "1",
            "vehicle_mode": "existing",
            "vehicle_id": "1",
            "service_index": "0",
        }
        duplicate_response = client.post(
            "/pro/estimate-conversion/create",
            data=duplicate_form_data,
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("?converted=1&created=1#repair-workspace", response.headers["location"])
        self.assertEqual(duplicate_response.status_code, 303)
        self.assertIn("?converted=1&created=0#repair-workspace", duplicate_response.headers["location"])

        conn = self.conn
        repairs = [dict(row) for row in conn.execute("SELECT * FROM repair_records").fetchall()]
        pro_module.ensure_service_history_records_schema(conn)
        findings_count = conn.execute("SELECT COUNT(*) FROM findings_records").fetchone()[0]
        history_count = conn.execute("SELECT COUNT(*) FROM service_history_records").fetchone()[0]

        self.assertEqual(len(repairs), 1)
        repair = repairs[0]
        self.assertEqual(repair["repair_name"], "Replace front brake pads")
        self.assertEqual(repair["status"], "Open")
        self.assertEqual(repair["mileage"], 120000)
        self.assertEqual(repair["labor_hours"], 1.2)
        self.assertEqual(repair["labor_rate"], 125)
        self.assertEqual(repair["labor_cost"], 150)
        self.assertEqual(repair["parts_cost"], 115)
        self.assertEqual(repair["total_cost"], 265)
        self.assertEqual(repair["workflow_source_type"], "estimate")
        self.assertIn("Source: Estimate", repair["notes"])
        self.assertNotIn("Source: Estimator Quote", repair["notes"])
        self.assertEqual(findings_count, 0)
        self.assertEqual(history_count, 0)

    def test_conversion_preserves_custom_service_parts_search_term(self):
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        payload = self.conversion_payload()
        payload["lineItems"] = [
            {
                "serviceText": "Radio Antenna Replacement",
                "partsSearchTerm": "radio antenna",
                "laborHours": 0.8,
                "laborRate": 125,
                "laborTotal": 100,
                "partsTotal": 45,
                "grandTotal": 145,
            }
        ]
        response = client.post(
            "/pro/estimate-conversion/create",
            data={
                "estimate_payload": json.dumps(payload),
                "customer_mode": "new",
                "new_customer_name": "Mike Johnson",
                "vehicle_mode": "new",
                "new_vehicle_year": "2021",
                "new_vehicle_make": "Kia",
                "new_vehicle_model": "Forte",
                "service_index": "0",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        repair = dict(self.conn.execute("SELECT * FROM repair_records").fetchone())
        self.assertEqual(repair["repair_name"], "Radio Antenna Replacement")
        self.assertEqual(repair["parts_search_term"], "radio antenna")

    def test_quantity_estimate_conversion_preserves_display_name_and_parts_total(self):
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        payload = self.conversion_payload()
        payload["lineItems"] = [
            {
                "serviceText": "Ignition Coil Replacement (each)",
                "displayServiceText": "Ignition Coil Replacement (each) × 4",
                "quantity": 4,
                "laborHours": 1.1,
                "laborRate": 125,
                "laborTotal": 137.5,
                "partsUnitCost": 45,
                "partsTotal": 180,
                "grandTotal": 317.5,
            }
        ]
        form_data = {
            "estimate_payload": json.dumps(payload),
            "customer_mode": "new",
            "new_customer_name": "Mike Johnson",
            "new_customer_phone": "555-222-1111",
            "new_customer_email": "mike@test.com",
            "vehicle_mode": "new",
            "new_vehicle_year": "2016",
            "new_vehicle_make": "Honda",
            "new_vehicle_model": "Accord",
            "service_index": "0",
        }

        response = client.post(
            "/pro/estimate-conversion/create",
            data=form_data,
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        repair = dict(self.conn.execute("SELECT * FROM repair_records").fetchone())
        self.assertEqual(repair["repair_name"], "Ignition Coil Replacement (each) × 4")
        self.assertEqual(repair["labor_hours"], 1.1)
        self.assertEqual(repair["parts_cost"], 180)
        self.assertEqual(repair["total_cost"], 317.5)

    def test_quantity_labor_per_item_conversion_calculates_final_totals(self):
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        payload = self.conversion_payload()
        payload["lineItems"] = [
            {
                "serviceText": "Ignition Coil Replacement (each)",
                "quantity": 4,
                "laborHoursInput": 1,
                "laborCalculationMode": "per_item",
                "laborRate": 90,
                "partsUnitCost": 45,
            }
        ]
        form_data = {
            "estimate_payload": json.dumps(payload),
            "customer_mode": "new",
            "new_customer_name": "Mike Johnson",
            "new_customer_phone": "555-222-1111",
            "new_customer_email": "mike@test.com",
            "vehicle_mode": "new",
            "new_vehicle_year": "2016",
            "new_vehicle_make": "Honda",
            "new_vehicle_model": "Accord",
            "service_index": "0",
        }

        response = client.post(
            "/pro/estimate-conversion/create",
            data=form_data,
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        repair = dict(self.conn.execute("SELECT * FROM repair_records").fetchone())
        self.assertEqual(repair["repair_name"], "Ignition Coil Replacement (each) × 4")
        self.assertEqual(repair["labor_hours"], 4)
        self.assertEqual(repair["labor_rate"], 90)
        self.assertEqual(repair["labor_cost"], 360)
        self.assertEqual(repair["parts_cost"], 180)
        self.assertEqual(repair["total_cost"], 540)

    def test_quantity_default_labor_mode_does_not_multiply_labor(self):
        payload = pro_module.load_estimate_conversion_payload(
            json.dumps(
                {
                    "vehicle": {"year": "2016", "make": "Honda", "model": "Accord"},
                    "lineItems": [
                        {
                            "serviceText": "Ignition Coil Replacement (each)",
                            "quantity": 4,
                            "laborHours": 1,
                            "laborRate": 90,
                            "partsUnitCost": 45,
                        }
                    ],
                }
            )
        )

        item = payload["lineItems"][0]
        self.assertEqual(item["service_name"], "Ignition Coil Replacement (each) × 4")
        self.assertEqual(item["labor_hours"], 1)
        self.assertEqual(item["labor_total"], 90)
        self.assertEqual(item["parts_total"], 180)
        self.assertEqual(item["grand_total"], 270)

    def test_quantity_payload_clamps_invalid_quantity_to_one(self):
        payload = pro_module.load_estimate_conversion_payload(
            json.dumps(
                {
                    "vehicle": {"year": "2016", "make": "Honda", "model": "Accord"},
                    "lineItems": [
                        {
                            "serviceText": "Ignition Coil Replacement (each)",
                            "quantity": 0,
                            "laborHours": 1,
                            "laborRate": 90,
                            "partsUnitCost": 45,
                        }
                    ],
                }
            )
        )

        item = payload["lineItems"][0]
        self.assertEqual(item["quantity"], 1)
        self.assertEqual(item["parts_total"], 45)
        self.assertEqual(item["labor_hours"], 1)
        self.assertEqual(item["grand_total"], 135)

    def test_finding_estimate_conversion_creates_source_finding_repair_job(self):
        now = "2026-06-25T12:00:00"
        self.conn.execute(
            """
            INSERT INTO customers (id, first_name, last_name, phone, email, created_at, updated_at)
            VALUES (1, 'Natalie', 'Htut', '555-0100', '', ?, ?)
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
        self.conn.execute(
            """
            INSERT INTO findings_records (id, vehicle_id, customer_id, finding, status, created_at)
            VALUES (1, 1, 1, 'Rotors are worn', 'Open', ?)
            """,
            (now,),
        )
        self.conn.commit()

        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        form_data = {
            "estimate_payload": json.dumps(self.finding_conversion_payload()),
            "customer_mode": "existing",
            "customer_id": "1",
            "vehicle_mode": "existing",
            "vehicle_id": "1",
            "service_index": "0",
        }

        response = client.post(
            "/pro/estimate-conversion/create",
            data=form_data,
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("?converted=1&created=1#repair-workspace", response.headers["location"])

        repair = dict(self.conn.execute("SELECT * FROM repair_records").fetchone())
        finding = dict(self.conn.execute("SELECT * FROM findings_records WHERE id = 1").fetchone())

        self.assertEqual(repair["repair_name"], "Front Rotors Replacement")
        self.assertEqual(repair["workflow_source_type"], "finding")
        self.assertEqual(repair["workflow_source_id"], 1)
        self.assertEqual(repair["labor_hours"], 1.5)
        self.assertEqual(repair["labor_rate"], 140)
        self.assertEqual(repair["labor_cost"], 210)
        self.assertEqual(repair["parts_cost"], 180)
        self.assertEqual(repair["total_cost"], 390)
        self.assertIn("Source: Finding", repair["notes"])
        self.assertEqual(finding["status"], "Approved")
        self.assertEqual(finding["linked_repair_record_id"], repair["id"])
        self.assertEqual(finding["repair_work_status"], "ready")

    def test_finding_conversion_creates_selected_service_even_when_old_finding_repair_exists(self):
        now = "2026-06-25T12:00:00"
        self.conn.execute(
            """
            INSERT INTO customers (id, first_name, last_name, phone, email, created_at, updated_at)
            VALUES (1, 'Natalie', 'Htut', '555-0100', '', ?, ?)
            """,
            (now, now),
        )
        self.conn.execute(
            """
            INSERT INTO customer_vehicles (id, customer_id, year, make, model, mileage, created_at, updated_at)
            VALUES (1, 1, 2021, 'Kia', 'Forte', 81000, ?, ?)
            """,
            (now, now),
        )
        pro_module.ensure_findings_records_schema(self.conn)
        pro_module.ensure_repair_records_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO findings_records (
              id, vehicle_id, customer_id, finding, recommendation, status,
              linked_repair_record_id, repair_work_status, created_at
            )
            VALUES (1, 1, 1, 'Coolant leak', 'Inspect cooling system', 'Approved', 99, 'ready', ?)
            """,
            (now,),
        )
        self.conn.execute(
            """
            INSERT INTO repair_records (
              id, vehicle_id, customer_id, repair_name, repair_date, mileage,
              labor_hours, labor_rate, parts_cost, labor_cost, total_cost,
              workflow_source_type, workflow_source_id, status, notes, created_at
            )
            VALUES (99, 1, 1, 'tire', '2026-06-24', 81000, 0.5, 90, 0, 45, 45,
                    'finding', 1, 'Open', 'Source: Finding', ?)
            """,
            (now,),
        )
        self.conn.commit()
        payload = self.finding_conversion_payload()
        payload["vehicle"] = {"year": "2021", "make": "Kia", "model": "Forte"}
        payload["sourceContext"]["problemFound"] = "Coolant leak"
        payload["sourceContext"]["recommendedRepair"] = "Coolant Reservoir Replacement"
        payload["lineItems"] = [
            {
                "serviceText": "Coolant Reservoir Replacement",
                "laborHours": 2.5,
                "laborRate": 90,
                "laborTotal": 225,
                "partsTotal": 0,
                "grandTotal": 225,
            }
        ]

        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        response = client.post(
            "/pro/estimate-conversion/create",
            data={
                "estimate_payload": json.dumps(payload),
                "customer_mode": "existing",
                "customer_id": "1",
                "vehicle_mode": "existing",
                "vehicle_id": "1",
                "service_index": "0",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("?converted=1&created=1#repair-workspace", response.headers["location"])
        repairs = [
            dict(row)
            for row in self.conn.execute("SELECT * FROM repair_records ORDER BY id ASC").fetchall()
        ]
        self.assertEqual([repair["repair_name"] for repair in repairs], ["tire", "Coolant Reservoir Replacement"])
        converted = repairs[1]
        self.assertEqual(converted["workflow_source_type"], "finding")
        self.assertEqual(converted["workflow_source_id"], 1)
        self.assertEqual(converted["labor_hours"], 2.5)
        self.assertEqual(converted["labor_rate"], 90)
        self.assertEqual(converted["labor_cost"], 225)
        self.assertEqual(converted["parts_cost"], 0)
        self.assertEqual(converted["total_cost"], 225)

        vehicle_detail = client.get("/pro/customers/1/vehicles/1")
        self.assertEqual(vehicle_detail.status_code, 200)
        self.assertIn("Coolant Reservoir Replacement", vehicle_detail.text)
        self.assertIn("Parts Sources", vehicle_detail.text)
        self.assertIn("Parts Tracking", vehicle_detail.text)

    def test_finding_estimator_href_carries_encoded_context(self):
        href = pro_module.build_finding_estimator_href(
            {"id": 7, "first_name": "Natalie", "last_name": "Htut"},
            {"id": 12, "year": 2008, "make": "TOYOTA", "model": "SEQUOIA"},
            {
                "id": 44,
                "finding": "Rotors are worn",
                "recommendation": "Front Rotors Replacement",
            },
        )

        self.assertTrue(href.startswith("/estimator?"))
        self.assertIn("customer_id=7", href)
        self.assertIn("vehicle_id=12", href)
        self.assertIn("finding_id=44", href)
        self.assertIn("source=finding", href)
        self.assertIn("recommended_repair=Front+Rotors+Replacement", href)
        self.assertIn("problem_found=Rotors+are+worn", href)


if __name__ == "__main__":
    unittest.main()
