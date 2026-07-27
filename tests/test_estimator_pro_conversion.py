import json
import sqlite3
import unittest
from urllib.parse import urlencode
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
        self.current_shop_patch = patch.object(pro_module, "current_shop_id", lambda conn, request=None: None)
        self.required_shop_patch = patch.object(pro_module, "required_current_shop_id", lambda conn, request: None)
        self.crm_patch.start()
        self.current_shop_patch.start()
        self.required_shop_patch.start()
        self.addCleanup(self.crm_patch.stop)
        self.addCleanup(self.current_shop_patch.stop)
        self.addCleanup(self.required_shop_patch.stop)
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

    def seed_customer_vehicle(self, *, customer_id=1, vehicle_id=1, first_name="Samm", last_name="", year=2023, make="Kia", model="Forte Coupe"):
        now = "2026-07-10T12:00:00"
        self.conn.execute(
            """
            INSERT INTO customers (id, first_name, last_name, phone, email, customer_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, '', 'active', ?, ?)
            """,
            (customer_id, first_name, last_name, f"555-010{customer_id}", now, now),
        )
        self.conn.execute(
            """
            INSERT INTO customer_vehicles (id, customer_id, year, make, model, mileage, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 88000, ?, ?)
            """,
            (vehicle_id, customer_id, year, make, model, now, now),
        )
        self.conn.commit()

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
        self.assertEqual(
            response.headers["location"],
            "/pro/customers/1/vehicles/1/repairs/1?converted=1&created=1",
        )
        self.assertEqual(duplicate_response.status_code, 303)
        self.assertEqual(
            duplicate_response.headers["location"],
            "/pro/customers/1/vehicles/1/repairs/1?converted=1&created=0",
        )

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

    def test_multiple_created_repairs_redirect_to_repair_workspace(self):
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        payload = self.conversion_payload()
        payload["lineItems"].append(
            {
                "serviceText": "Oil & Filter Change",
                "laborHours": 0.5,
                "laborRate": 120,
                "laborTotal": 60,
                "partsTotal": 45,
                "grandTotal": 105,
            }
        )

        response = client.post(
            "/pro/estimate-conversion/create",
            content=urlencode(
                {
                    "estimate_payload": json.dumps(payload),
                    "customer_mode": "new",
                    "new_customer_name": "Mike Johnson",
                    "vehicle_mode": "new",
                    "new_vehicle_year": "2016",
                    "new_vehicle_make": "Honda",
                    "new_vehicle_model": "Accord",
                    "service_index": ["0", "1"],
                },
                doseq=True,
            ),
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/pro/customers/1/vehicles/1?converted=1&created=2", response.headers["location"])
        self.assertIn("repair_ids=1%2C2#repair-workspace", response.headers["location"])

    def test_conversion_does_not_require_customer_pdf_download(self):
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

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            "/pro/customers/1/vehicles/1/repairs/1?converted=1&created=1",
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM repair_records").fetchone()[0], 1)

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

    def test_flat_rate_estimate_conversion_preserves_invoice_total(self):
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        payload = self.conversion_payload()
        payload["lineItems"] = [
            {
                "serviceText": "Left tail light",
                "pricingMode": "flat",
                "flatRatePrice": 75,
                "laborHours": 0,
                "laborRate": 0,
                "laborTotal": 75,
                "partsTotal": 99,
                "grandTotal": 174,
            }
        ]

        response = client.post(
            "/pro/estimate-conversion/create",
            data={
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
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        repair = dict(self.conn.execute("SELECT * FROM repair_records").fetchone())
        self.assertEqual(repair["repair_name"], "Left tail light")
        self.assertEqual(repair["pricing_mode"], "flat")
        self.assertEqual(repair["flat_rate_price"], 75)
        self.assertEqual(repair["labor_cost"], 75)
        self.assertEqual(repair["parts_cost"], 99)
        self.assertEqual(repair["total_cost"], 174)
        self.assertEqual(repair["approved_estimate_total"], 174)

        pro_module.upsert_repair_completion(
            self.conn,
            repair_record_id=repair["id"],
            form={
                "completion_date": "2026-06-25",
                "completion_mileage": "120000",
                "final_inspection_passed": "1",
            },
            completed_at="2026-06-25T12:00:00",
            now="2026-06-25T12:00:00",
        )
        self.conn.execute(
            "UPDATE repair_records SET status = 'Completed', completed_at = ? WHERE id = ?",
            ("2026-06-25T12:00:00", repair["id"]),
        )
        loaded_repair = pro_module.load_repair_record(self.conn, 1, 1, repair["id"])
        self.assertEqual(loaded_repair["labor_total"], 75)
        self.assertEqual(loaded_repair["parts_total"], 99)
        self.assertEqual(loaded_repair["grand_total"], 174)

        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=[loaded_repair],
            customer_id=1,
            vehicle_id=1,
            now="2026-06-25T12:30:00",
        )
        loaded_invoice = pro_module.load_invoice_record(self.conn, 1, 1, invoice["id"])

        self.assertEqual(loaded_invoice["labor_total"], 75)
        self.assertEqual(loaded_invoice["parts_total"], 99)
        self.assertEqual(loaded_invoice["grand_total"], 174)
        self.assertEqual(loaded_invoice["approved_estimate_total"], 174)
        self.assertEqual(loaded_invoice["estimate_final_difference"], 0)

    def test_phase32_representative_catalog_services_flow_from_estimate_to_invoice(self):
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        payload = self.conversion_payload()
        payload["lineItems"] = [
            {
                "serviceText": "Post-Windshield-Replacement ADAS Calibration",
                "pricingMode": "flat",
                "flatRatePrice": 240,
                "laborHours": 0,
                "laborRate": 0,
                "laborTotal": 240,
                "partsTotal": 0,
                "grandTotal": 240,
            },
            {
                "serviceText": "Tire Replacement (set of four)",
                "laborHours": 2.0,
                "laborRate": 150,
                "laborTotal": 300,
                "partsTotal": 480,
                "grandTotal": 780,
            },
            {
                "serviceText": "EGR Cooler Replacement (Diesel)",
                "laborHours": 3.2,
                "laborRate": 155,
                "laborTotal": 496,
                "partsTotal": 620,
                "grandTotal": 1116,
            },
            {
                "serviceText": "High-Voltage Battery State-of-Health Test",
                "laborHours": 1.0,
                "laborRate": 170,
                "laborTotal": 170,
                "partsTotal": 0,
                "grandTotal": 170,
            },
            {
                "serviceText": "Trailer-Hitch Installation Estimate",
                "laborHours": 1.0,
                "laborRate": 145,
                "laborTotal": 145,
                "partsTotal": 0,
                "grandTotal": 145,
            },
            {
                "serviceText": "Advanced Diagnostic Labor Extension",
                "laborHours": 1.5,
                "laborRate": 160,
                "laborTotal": 240,
                "partsTotal": 0,
                "grandTotal": 240,
            },
        ]

        response = client.post(
            "/pro/estimate-conversion/create",
            content=urlencode(
                {
                    "estimate_payload": json.dumps(payload),
                    "customer_mode": "new",
                    "new_customer_name": "Phase Thirtytwo",
                    "new_customer_phone": "555-3232",
                    "vehicle_mode": "new",
                    "new_vehicle_year": "2024",
                    "new_vehicle_make": "Ford",
                    "new_vehicle_model": "F-150 Hybrid",
                    "new_vehicle_mileage": "42,500",
                    "service_index": ["0", "1", "2", "3", "4", "5"],
                },
                doseq=True,
            ),
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/pro/customers/1/vehicles/1?converted=1&created=6", response.headers["location"])
        repairs = [dict(row) for row in self.conn.execute("SELECT * FROM repair_records ORDER BY id").fetchall()]
        self.assertEqual([repair["repair_name"] for repair in repairs], [item["serviceText"] for item in payload["lineItems"]])
        self.assertEqual(repairs[0]["pricing_mode"], "flat")
        self.assertEqual(repairs[0]["labor_cost"], 240)
        self.assertEqual(repairs[1]["labor_hours"], 2.0)
        self.assertEqual(repairs[1]["parts_cost"], 480)

        for repair in repairs:
            completion = client.post(
                f"/pro/customers/1/vehicles/1/repairs/{repair['id']}/completion",
                data={
                    "completion_date": "2026-07-22",
                    "completion_mileage": "42,750",
                    "completion_notes": f"Completed {repair['repair_name']}.",
                    "final_inspection_passed": "1",
                    "final_inspection_notes": "Final check passed.",
                },
                follow_redirects=False,
            )
            self.assertEqual(completion.status_code, 303)

        history_names = [
            row["service_name"]
            for row in self.conn.execute("SELECT service_name FROM service_history_records ORDER BY id").fetchall()
        ]
        self.assertEqual(history_names, [item["serviceText"] for item in payload["lineItems"]])

        vehicle_detail = client.get("/pro/customers/1/vehicles/1")
        self.assertEqual(vehicle_detail.status_code, 200)
        self.assertIn("Completed Repairs", vehicle_detail.text)
        for item in payload["lineItems"]:
            self.assertIn(item["serviceText"], vehicle_detail.text)

        invoice_response = client.post(
            "/pro/customers/1/vehicles/1/invoices",
            content=urlencode({"repair_record_id": [str(repair["id"]) for repair in repairs]}, doseq=True),
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        self.assertEqual(invoice_response.status_code, 303)
        self.assertEqual(invoice_response.headers["location"], "/pro/customers/1/vehicles/1/invoices/1")

        invoice = pro_module.load_invoice_record(self.conn, 1, 1, 1)
        self.assertEqual(invoice["service_count"], 6)
        self.assertEqual(invoice["labor_total"], 1591)
        self.assertEqual(invoice["parts_total"], 1100)
        self.assertEqual(invoice["grand_total"], 2691)
        self.assertEqual([item["service_title"] for item in invoice["items"]], [item["serviceText"] for item in payload["lineItems"]])

        invoice_detail = client.get("/pro/customers/1/vehicles/1/invoices/1")
        self.assertEqual(invoice_detail.status_code, 200)
        self.assertIn("Invoice TM-INV-0001", invoice_detail.text)
        self.assertIn("Post-Windshield-Replacement ADAS Calibration", invoice_detail.text)
        self.assertIn("$2,691.00", invoice_detail.text)

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
        self.assertEqual(
            response.headers["location"],
            "/pro/customers/1/vehicles/1/repairs/1?converted=1&created=1",
        )

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

    def test_estimate_conversion_auto_selects_valid_linked_customer_and_vehicle(self):
        self.seed_customer_vehicle()
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        payload = self.conversion_payload()
        payload.update({"source": "appointment", "customerId": "1", "vehicleId": "1", "appointmentId": "7"})
        payload["customer"] = {"name": "Samm", "phone": "555-0101"}
        payload["vehicle"] = {"year": "2023", "make": "Kia", "model": "Forte Coupe"}

        response = client.post("/pro/estimate-conversion", data={"estimate_payload": json.dumps(payload)})

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Customer and vehicle are already linked", html)
        self.assertIn("Samm", html)
        self.assertIn("2023 Kia Forte Coupe", html)
        self.assertIn('name="linked_customer_vehicle_locked" value="1"', html)
        self.assertIn("Change Customer or Vehicle", html)
        self.assertIn("data-linked-manual hidden", html)

    def test_estimate_conversion_hydrates_customer_vehicle_from_linked_appointment(self):
        self.seed_customer_vehicle(first_name="Avery", last_name="Stone", year=2020, make="Toyota", model="Tacoma")
        pro_module.ensure_calendar_schema(self.conn)
        appointment_id = pro_module.create_service_appointment(
            self.conn,
            {
                "customer_id": 1,
                "vehicle_id": 1,
                "customer_name": "Avery Stone",
                "customer_phone": "555-0101",
                "vehicle_label": "2020 Toyota Tacoma",
                "service_name": "Brake Inspection",
                "requested_date": "2026-07-15",
                "requested_time": "10:00",
                "status": "Confirmed",
            },
        )
        payload = self.conversion_payload()
        payload.update({"source": "appointment", "appointmentId": str(appointment_id)})

        app = FastAPI()
        app.include_router(pro_module.router)
        response = TestClient(app, base_url="http://localhost").post(
            "/pro/estimate-conversion",
            data={"estimate_payload": json.dumps(payload)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Customer and vehicle are already linked", response.text)
        self.assertIn("Avery Stone", response.text)
        self.assertIn("2020 Toyota Tacoma", response.text)
        self.assertIn('name="customer_id" value="1"', response.text)
        self.assertIn('name="vehicle_id" value="1"', response.text)
        self.assertIn('name="linked_customer_vehicle_locked" value="1"', response.text)

    def test_linked_estimate_conversion_creates_job_without_reselecting_records(self):
        self.seed_customer_vehicle()
        pro_module.ensure_calendar_schema(self.conn)
        appointment_id = pro_module.create_service_appointment(
            self.conn,
            {
                "customer_id": 1,
                "vehicle_id": 1,
                "customer_name": "Samm",
                "vehicle_label": "2023 Kia Forte Coupe",
                "service_name": "Brake Inspection",
                "requested_date": "2026-07-15",
                "requested_time": "10:00",
                "status": "Confirmed",
            },
        )
        payload = self.conversion_payload()
        payload.update(
            {
                "source": "appointment",
                "customerId": "1",
                "vehicleId": "1",
                "appointmentId": str(appointment_id),
                "estimateId": "44",
            }
        )
        payload["lineItems"][0]["serviceText"] = "Brake Inspection"
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        response = client.post(
            "/pro/estimate-conversion/create",
            data={
                "estimate_payload": json.dumps(payload),
                "linked_customer_vehicle_locked": "1",
                "customer_mode": "existing",
                "customer_id": "1",
                "vehicle_mode": "existing",
                "vehicle_id": "1",
                "service_index": "0",
            },
            follow_redirects=False,
        )
        duplicate = client.post(
            "/pro/estimate-conversion/create",
            data={
                "estimate_payload": json.dumps(payload),
                "linked_customer_vehicle_locked": "1",
                "customer_mode": "existing",
                "customer_id": "1",
                "vehicle_mode": "existing",
                "vehicle_id": "1",
                "service_index": "0",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            "/pro/customers/1/vehicles/1/repairs/1?converted=1&created=1",
        )
        self.assertEqual(duplicate.status_code, 303)
        self.assertIn("created=0", duplicate.headers["location"])
        repairs = [dict(row) for row in self.conn.execute("SELECT * FROM repair_records").fetchall()]
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["customer_id"], 1)
        self.assertEqual(repairs[0]["vehicle_id"], 1)
        self.assertEqual(repairs[0]["workflow_source_type"], "estimate")
        self.assertEqual(repairs[0]["workflow_source_id"], 44)
        appointment = dict(self.conn.execute("SELECT * FROM service_appointments WHERE id = ?", (appointment_id,)).fetchone())
        self.assertEqual(appointment["repair_id"], repairs[0]["id"])

    def test_appointment_estimate_context_converts_to_pro_job_and_preserves_links(self):
        self.seed_customer_vehicle(first_name="Riley", last_name="Cruz", year=2018, make="Subaru", model="Outback")
        pro_module.ensure_calendar_schema(self.conn)
        appointment_id = pro_module.create_service_appointment(
            self.conn,
            {
                "customer_id": 1,
                "vehicle_id": 1,
                "customer_name": "Riley Cruz",
                "vehicle_label": "2018 Subaru Outback",
                "service_name": "Wheel Bearing",
                "requested_date": "2026-07-16",
                "requested_time": "11:00",
                "status": "Confirmed",
            },
        )
        pro_module.ensure_repair_estimate_documents_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO repair_estimate_documents (
              id, customer_id, vehicle_id, estimate_date, customer_name, vehicle_label,
              related_title, estimate_total, approval_status, pdf_path, payload_json, created_at
            )
            VALUES (44, 1, 1, '2026-07-10', 'Riley Cruz', '2018 Subaru Outback',
                    'Wheel Bearing', 425, 'Prepared estimate', ?, ?, '2026-07-10T12:00:00')
            """,
            (
                "C:/tmp/estimate.pdf",
                json.dumps({"source": "appointment", "appointment_id": appointment_id}),
            ),
        )
        self.conn.execute(
            "UPDATE service_appointments SET estimate_id = 44 WHERE id = ?",
            (appointment_id,),
        )
        self.conn.commit()
        edit_url = pro_module.estimate_document_edit_url(
            1,
            1,
            {
                "id": 44,
                "finding_id": None,
                "payload_json": json.dumps({"source": "appointment", "appointment_id": appointment_id}),
            },
        )
        payload = self.conversion_payload()
        payload.update({"source": "appointment", "appointmentId": str(appointment_id), "estimateId": "44"})

        app = FastAPI()
        app.include_router(pro_module.router)
        response = TestClient(app, base_url="http://localhost").post(
            "/pro/estimate-conversion/create",
            data={
                "estimate_payload": json.dumps(payload),
                "linked_customer_vehicle_locked": "1",
                "service_index": "0",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            "/pro/customers/1/vehicles/1/repairs/1?converted=1&created=1",
        )
        self.assertIn("appointment_id=", edit_url)
        repair = dict(self.conn.execute("SELECT * FROM repair_records").fetchone())
        appointment = dict(self.conn.execute("SELECT * FROM service_appointments WHERE id = ?", (appointment_id,)).fetchone())
        self.assertEqual(repair["customer_id"], 1)
        self.assertEqual(repair["vehicle_id"], 1)
        self.assertEqual(repair["workflow_source_type"], "estimate")
        self.assertEqual(repair["workflow_source_id"], 44)
        self.assertEqual(appointment["customer_id"], 1)
        self.assertEqual(appointment["vehicle_id"], 1)
        self.assertEqual(appointment["estimate_id"], 44)
        self.assertEqual(appointment["repair_id"], repair["id"])

    def test_estimate_conversion_shows_open_pro_job_for_converted_estimate(self):
        self.seed_customer_vehicle()
        pro_module.ensure_repair_records_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO repair_records (
              id, vehicle_id, customer_id, repair_name, repair_date, workflow_source_type,
              workflow_source_id, status, created_at
            )
            VALUES (9, 1, 1, 'Brake Inspection', '2026-07-10', 'estimate', 44, 'Open', '2026-07-10T12:00:00')
            """
        )
        self.conn.commit()
        payload = self.conversion_payload()
        payload.update({"customerId": "1", "vehicleId": "1", "estimateId": "44"})
        app = FastAPI()
        app.include_router(pro_module.router)
        response = TestClient(app, base_url="http://localhost").post(
            "/pro/estimate-conversion",
            data={"estimate_payload": json.dumps(payload)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/pro/customers/1/vehicles/1/repairs/9"', response.text)
        self.assertIn("Open Repair Order", response.text)

    def test_estimate_conversion_missing_link_fallbacks_do_not_match_by_name(self):
        self.seed_customer_vehicle(customer_id=2, vehicle_id=2, first_name="Samm")
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        missing_customer_payload = self.conversion_payload()
        missing_customer_payload.update({"customerId": "99", "vehicleId": "2"})
        missing_customer_payload["customer"] = {"name": "Samm", "phone": ""}

        missing_customer = client.post(
            "/pro/estimate-conversion",
            data={"estimate_payload": json.dumps(missing_customer_payload)},
        )
        self.assertEqual(missing_customer.status_code, 200)
        self.assertIn("linked to this estimate no longer exists", missing_customer.text)
        self.assertNotIn('name="linked_customer_vehicle_locked" value="1"', missing_customer.text)

        mismatched_vehicle_payload = self.conversion_payload()
        mismatched_vehicle_payload.update({"customerId": "2", "vehicleId": "99"})
        mismatched_vehicle = client.post(
            "/pro/estimate-conversion",
            data={"estimate_payload": json.dumps(mismatched_vehicle_payload)},
        )
        self.assertEqual(mismatched_vehicle.status_code, 200)
        self.assertIn("linked vehicle is missing or does not belong", mismatched_vehicle.text)
        self.assertNotIn('name="linked_customer_vehicle_locked" value="1"', mismatched_vehicle.text)

    def test_estimate_conversion_missing_deleted_appointment_customer_falls_back_to_manual(self):
        self.seed_customer_vehicle()
        pro_module.ensure_calendar_schema(self.conn)
        appointment_id = pro_module.create_service_appointment(
            self.conn,
            {
                "customer_id": 1,
                "vehicle_id": 1,
                "customer_name": "Samm",
                "vehicle_label": "2023 Kia Forte Coupe",
                "service_name": "Brake Inspection",
                "requested_date": "2026-07-15",
                "requested_time": "10:00",
                "status": "Confirmed",
            },
        )
        self.conn.execute("DELETE FROM customers WHERE id = 1")
        self.conn.commit()
        payload = self.conversion_payload()
        payload.update({"source": "appointment", "appointmentId": str(appointment_id)})

        app = FastAPI()
        app.include_router(pro_module.router)
        response = TestClient(app, base_url="http://localhost").post(
            "/pro/estimate-conversion",
            data={"estimate_payload": json.dumps(payload)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("linked to this estimate no longer exists", response.text)
        self.assertNotIn('name="linked_customer_vehicle_locked" value="1"', response.text)
        self.assertIn("Select or create a customer", response.text)

    def test_legacy_estimate_without_ids_keeps_manual_selection_flow(self):
        app = FastAPI()
        app.include_router(pro_module.router)
        response = TestClient(app, base_url="http://localhost").post(
            "/pro/estimate-conversion",
            data={"estimate_payload": json.dumps(self.conversion_payload())},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Select or create a customer", response.text)
        self.assertIn("Select Existing Customer", response.text)
        self.assertNotIn('name="linked_customer_vehicle_locked" value="1"', response.text)

    def test_manual_estimate_conversion_reuses_duplicate_customer_and_vehicle(self):
        self.seed_customer_vehicle(first_name="Mike", last_name="Johnson", year=2016, make="Honda", model="Accord")
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        response = client.post(
            "/pro/estimate-conversion/create",
            data={
                "estimate_payload": json.dumps(self.conversion_payload()),
                "customer_mode": "new",
                "new_customer_name": "Mike Johnson",
                "new_customer_phone": "555-0101",
                "new_customer_email": "",
                "vehicle_mode": "new",
                "new_vehicle_year": "2016",
                "new_vehicle_make": "Honda",
                "new_vehicle_model": "Accord",
                "service_index": "0",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/pro/customers/1/vehicles/1", response.headers["location"])
        customer_count = self.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        vehicle_count = self.conn.execute("SELECT COUNT(*) FROM customer_vehicles").fetchone()[0]
        self.assertEqual(customer_count, 1)
        self.assertEqual(vehicle_count, 1)

    def test_appointment_conversion_new_mode_reuses_matching_customer_vehicle_by_name(self):
        self.seed_customer_vehicle(first_name="Casey", last_name="Lane", year=2016, make="Honda", model="Accord")
        app = FastAPI()
        app.include_router(pro_module.router)
        response = TestClient(app, base_url="http://localhost").post(
            "/pro/estimate-conversion/create",
            data={
                "estimate_payload": json.dumps(self.conversion_payload()),
                "customer_mode": "new",
                "new_customer_name": "Casey Lane",
                "new_customer_phone": "",
                "new_customer_email": "",
                "vehicle_mode": "new",
                "new_vehicle_year": "2016",
                "new_vehicle_make": "Honda",
                "new_vehicle_model": "Accord",
                "service_index": "0",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/pro/customers/1/vehicles/1", response.headers["location"])
        customer_count = self.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        vehicle_count = self.conn.execute("SELECT COUNT(*) FROM customer_vehicles").fetchone()[0]
        self.assertEqual(customer_count, 1)
        self.assertEqual(vehicle_count, 1)

    def test_manual_customer_vehicle_selection_overrides_linked_payload(self):
        self.seed_customer_vehicle(customer_id=1, vehicle_id=1, first_name="Linked", last_name="Customer", year=2020, make="Toyota", model="Tacoma")
        self.seed_customer_vehicle(customer_id=2, vehicle_id=2, first_name="Manual", last_name="Choice", year=2017, make="Ford", model="F-150")
        pro_module.ensure_calendar_schema(self.conn)
        appointment_id = pro_module.create_service_appointment(
            self.conn,
            {
                "customer_id": 1,
                "vehicle_id": 1,
                "customer_name": "Linked Customer",
                "vehicle_label": "2020 Toyota Tacoma",
                "service_name": "Brake Inspection",
                "requested_date": "2026-07-15",
                "requested_time": "10:00",
                "status": "Confirmed",
            },
        )
        payload = self.conversion_payload()
        payload.update({"source": "appointment", "appointmentId": str(appointment_id), "customerId": "1", "vehicleId": "1", "estimateId": "55"})

        app = FastAPI()
        app.include_router(pro_module.router)
        response = TestClient(app, base_url="http://localhost").post(
            "/pro/estimate-conversion/create",
            data={
                "estimate_payload": json.dumps(payload),
                "customer_mode": "existing",
                "customer_id": "2",
                "vehicle_mode": "existing",
                "vehicle_id": "2",
                "service_index": "0",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            "/pro/customers/2/vehicles/2/repairs/1?converted=1&created=1",
        )
        repair = dict(self.conn.execute("SELECT * FROM repair_records").fetchone())
        appointment = dict(self.conn.execute("SELECT * FROM service_appointments WHERE id = ?", (appointment_id,)).fetchone())
        self.assertEqual(repair["customer_id"], 2)
        self.assertEqual(repair["vehicle_id"], 2)
        self.assertIsNone(appointment["repair_id"])
        self.assertEqual(appointment["customer_id"], 1)
        self.assertEqual(appointment["vehicle_id"], 1)

    def test_complete_appointment_to_pro_job_flow_marks_converted_and_opens_existing_job(self):
        self.seed_customer_vehicle(first_name="Jamie", last_name="Stone", year=2019, make="Mazda", model="CX-5")
        pro_module.ensure_calendar_schema(self.conn)
        appointment_id = pro_module.create_service_appointment(
            self.conn,
            {
                "customer_id": 1,
                "vehicle_id": 1,
                "estimate_id": 77,
                "customer_name": "Jamie Stone",
                "vehicle_label": "2019 Mazda CX-5",
                "service_name": "Brake Inspection",
                "requested_date": "2026-07-20",
                "requested_time": "10:30",
                "status": "Confirmed",
            },
        )
        payload = self.conversion_payload()
        payload.update({"source": "appointment", "appointmentId": str(appointment_id), "estimateId": "77"})
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        response = client.post(
            "/pro/estimate-conversion/create",
            data={
                "estimate_payload": json.dumps(payload),
                "linked_customer_vehicle_locked": "1",
                "service_index": "0",
            },
            follow_redirects=False,
        )
        duplicate = client.post(
            "/pro/estimate-conversion/create",
            data={
                "estimate_payload": json.dumps(payload),
                "linked_customer_vehicle_locked": "1",
                "service_index": "0",
            },
            follow_redirects=False,
        )
        conversion_page = client.post(
            "/pro/estimate-conversion",
            data={"estimate_payload": json.dumps(payload)},
        )
        calendar = client.get("/pro/calendar")

        repairs = [dict(row) for row in self.conn.execute("SELECT * FROM repair_records").fetchall()]
        appointment = dict(self.conn.execute("SELECT * FROM service_appointments WHERE id = ?", (appointment_id,)).fetchone())
        self.assertEqual(response.status_code, 303)
        self.assertIn("created=1", response.headers["location"])
        self.assertEqual(duplicate.status_code, 303)
        self.assertIn("created=0", duplicate.headers["location"])
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["workflow_source_type"], "estimate")
        self.assertEqual(repairs[0]["workflow_source_id"], 77)
        self.assertEqual(appointment["status"], "Converted")
        self.assertEqual(appointment["repair_id"], repairs[0]["id"])
        self.assertIn("Open Repair Order", conversion_page.text)
        self.assertIn("Converted to Pro Job", calendar.text)
        self.assertIn(f'href="/pro/customers/1/vehicles/1/repairs/{repairs[0]["id"]}"', calendar.text)
        self.assertNotIn("Create Estimate", calendar.text)

    def test_cancelled_and_declined_appointments_cannot_convert_to_pro_job(self):
        self.seed_customer_vehicle()
        pro_module.ensure_calendar_schema(self.conn)
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        for status in ("Cancelled", "Declined"):
            appointment_id = pro_module.create_service_appointment(
                self.conn,
                {
                    "customer_id": 1,
                    "vehicle_id": 1,
                    "customer_name": "Samm",
                    "vehicle_label": "2023 Kia Forte Coupe",
                    "service_name": "Brake Inspection",
                    "requested_date": "2026-07-15",
                    "requested_time": "10:00",
                    "status": status,
                },
            )
            payload = self.conversion_payload()
            payload.update({"source": "appointment", "appointmentId": str(appointment_id), "estimateId": str(200 + appointment_id)})
            response = client.post(
                "/pro/estimate-conversion/create",
                data={
                    "estimate_payload": json.dumps(payload),
                    "linked_customer_vehicle_locked": "1",
                    "service_index": "0",
                },
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("Only confirmed appointments can be converted", response.text)
        repair_count = self.conn.execute("SELECT COUNT(*) FROM repair_records").fetchone()[0]
        self.assertEqual(repair_count, 0)

    def test_repair_completion_updates_service_history_vehicle_timeline_without_duplicates(self):
        self.seed_customer_vehicle(first_name="Morgan", last_name="Lee", year=2020, make="Toyota", model="RAV4")
        pro_module.ensure_calendar_schema(self.conn)
        appointment_id = pro_module.create_service_appointment(
            self.conn,
            {
                "customer_id": 1,
                "vehicle_id": 1,
                "estimate_id": 88,
                "customer_name": "Morgan Lee",
                "vehicle_label": "2020 Toyota RAV4",
                "service_name": "Oil Change",
                "requested_date": "2026-07-21",
                "requested_time": "09:30",
                "status": "Confirmed",
            },
        )
        payload = self.conversion_payload()
        payload["lineItems"] = [
            {
                "serviceText": "Oil Change",
                "laborHours": 0.5,
                "laborRate": 120,
                "laborTotal": 60,
                "partsTotal": 45,
                "grandTotal": 105,
            }
        ]
        payload.update({"source": "appointment", "appointmentId": str(appointment_id), "estimateId": "88"})
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")
        client.post(
            "/pro/estimate-conversion/create",
            data={
                "estimate_payload": json.dumps(payload),
                "linked_customer_vehicle_locked": "1",
                "service_index": "0",
            },
            follow_redirects=False,
        )
        repair = dict(self.conn.execute("SELECT * FROM repair_records").fetchone())

        for _ in range(2):
            completion = client.post(
                f"/pro/customers/1/vehicles/1/repairs/{repair['id']}/completion",
                data={
                    "completion_date": "2026-07-22",
                    "completion_mileage": "90123",
                    "completion_notes": "Completed from appointment workflow.",
                    "final_inspection_passed": "1",
                },
                follow_redirects=False,
            )
            self.assertEqual(completion.status_code, 303)

        history = [dict(row) for row in self.conn.execute("SELECT * FROM service_history_records").fetchall()]
        refreshed_repair = dict(self.conn.execute("SELECT * FROM repair_records WHERE id = ?", (repair["id"],)).fetchone())
        timeline = pro_module.build_vehicle_timeline(1, 1, {}, history, [], [], [], [])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["customer_id"], 1)
        self.assertEqual(history[0]["vehicle_id"], 1)
        self.assertEqual(history[0]["source_type"], "repair")
        self.assertEqual(history[0]["source_record_id"], repair["id"])
        self.assertEqual(refreshed_repair["status"], "Completed")
        self.assertEqual(refreshed_repair["mileage"], 90123)
        self.assertTrue(any("Oil Change" in str(group) for group in timeline))

    def test_calendar_shows_missing_linked_repair_fallback(self):
        self.seed_customer_vehicle()
        pro_module.ensure_calendar_schema(self.conn)
        appointment_id = pro_module.create_service_appointment(
            self.conn,
            {
                "customer_id": 1,
                "vehicle_id": 1,
                "repair_id": 999,
                "customer_name": "Samm",
                "vehicle_label": "2023 Kia Forte Coupe",
                "service_name": "Brake Inspection",
                "requested_date": "2026-07-15",
                "requested_time": "10:00",
                "status": "Converted",
            },
        )
        app = FastAPI()
        app.include_router(pro_module.router)
        response = TestClient(app, base_url="http://localhost").get("/pro/calendar")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Converted to Pro Job", response.text)
        self.assertIn("The linked Pro Job is unavailable", response.text)
        self.assertNotIn(f"/pro/calendar/{appointment_id}/convert", response.text)

    def test_finding_conversion_reuses_existing_repair_for_same_finding(self):
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
        self.assertEqual(response.headers["location"], "/pro/customers/1/vehicles/1/repairs/99?converted=1&created=0")
        repairs = [
            dict(row)
            for row in self.conn.execute("SELECT * FROM repair_records ORDER BY id ASC").fetchall()
        ]
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["id"], 99)
        self.assertEqual(repairs[0]["workflow_source_type"], "finding")
        self.assertEqual(repairs[0]["workflow_source_id"], 1)

        vehicle_detail = client.get("/pro/customers/1/vehicles/1")
        self.assertEqual(vehicle_detail.status_code, 200)
        self.assertIn("Open Repair", vehicle_detail.text)
        self.assertIn("Parts Sources", vehicle_detail.text)
        self.assertIn("Parts Tracking", vehicle_detail.text)

    def test_finding_create_redirect_carries_open_panel_state(self):
        self.seed_customer_vehicle(first_name="Natalie", last_name="Htut", year=2008, make="Toyota", model="Sequoia")
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        response = client.post(
            "/pro/customers/1/vehicles/1/findings",
            data={
                "request_type": "finding",
                "finding": "Oil seep at filter housing",
                "recommendation": "Oil change and inspect housing",
                "severity": "Medium",
                "status": "Open",
                "mileage": "150000",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("?finding_added=1", response.headers["location"])
        self.assertIn("finding_id=1", response.headers["location"])
        self.assertIn("finding_status=Open#recommendations-findings", response.headers["location"])

    def test_repair_detail_maintenance_tracking_toggle_is_idempotent(self):
        self.seed_customer_vehicle(first_name="Natalie", last_name="Htut", year=2008, make="Toyota", model="Sequoia")
        pro_module.ensure_repair_records_schema(self.conn)
        now = "2026-06-25T12:00:00"
        self.conn.execute(
            """
            INSERT INTO repair_records (
              id, vehicle_id, customer_id, repair_name, repair_date, mileage,
              labor_hours, labor_rate, parts_cost, labor_cost, total_cost,
              track_as_maintenance, status, notes, created_at
            )
            VALUES (7, 1, 1, 'Oil Change', '2026-06-25', 150000,
                    0.5, 100, 40, 50, 90, 0, 'Open', 'Synthetic oil', ?)
            """,
            (now,),
        )
        self.conn.commit()
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        for _ in range(2):
            response = client.post(
                "/pro/customers/1/vehicles/1/repairs/7/maintenance-tracking",
                data={"track_as_maintenance": "1"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)

        repair = dict(self.conn.execute("SELECT * FROM repair_records WHERE id = 7").fetchone())
        self.assertEqual(repair["track_as_maintenance"], 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM maintenance_records WHERE source_repair_record_id = 7"
            ).fetchone()[0],
            1,
        )
        maintenance_record = dict(
            self.conn.execute(
                "SELECT * FROM maintenance_records WHERE source_repair_record_id = 7"
            ).fetchone()
        )
        self.assertEqual(maintenance_record["service_type"], "Oil Change")
        self.assertEqual(maintenance_record["interval_miles"], 5000)
        self.assertEqual(maintenance_record["interval_months"], 6)
        self.assertIsNone(maintenance_record["mileage_performed"])
        self.assertEqual(maintenance_record["date_performed"], "")
        self.assertIsNone(maintenance_record["due_mileage"])
        self.assertEqual(maintenance_record["due_date"], "")

        response = client.post(
            "/pro/customers/1/vehicles/1/repairs/7/maintenance-tracking",
            data={},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        repair = dict(self.conn.execute("SELECT * FROM repair_records WHERE id = 7").fetchone())
        self.assertEqual(repair["track_as_maintenance"], 0)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM maintenance_records WHERE source_repair_record_id = 7"
            ).fetchone()[0],
            0,
        )

        response = client.post(
            "/pro/customers/1/vehicles/1/repairs/7/maintenance-tracking",
            data={"track_as_maintenance": "1"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM maintenance_records WHERE source_repair_record_id = 7"
            ).fetchone()[0],
            1,
        )

    def test_completed_tracked_repair_syncs_maintenance_due_values(self):
        self.seed_customer_vehicle(first_name="Natalie", last_name="Htut", year=2008, make="Toyota", model="Sequoia")
        pro_module.ensure_repair_records_schema(self.conn)
        now = "2026-06-25T12:00:00"
        self.conn.execute(
            """
            INSERT INTO repair_records (
              id, vehicle_id, customer_id, repair_name, repair_date, mileage,
              labor_hours, labor_rate, parts_cost, labor_cost, total_cost,
              track_as_maintenance, status, notes, created_at
            )
            VALUES (8, 1, 1, 'Oil & Filter Change', '2026-06-25', 150000,
                    0.5, 100, 40, 50, 90, 1, 'Open', 'Synthetic oil', ?)
            """,
            (now,),
        )
        self.conn.commit()
        app = FastAPI()
        app.include_router(pro_module.router)
        client = TestClient(app, base_url="http://localhost")

        enable = client.post(
            "/pro/customers/1/vehicles/1/repairs/8/maintenance-tracking",
            data={"track_as_maintenance": "1"},
            follow_redirects=False,
        )
        self.assertEqual(enable.status_code, 303)
        pending_record = dict(
            self.conn.execute(
                "SELECT * FROM maintenance_records WHERE source_repair_record_id = 8"
            ).fetchone()
        )
        self.assertEqual(pending_record["interval_miles"], 5000)
        self.assertIsNone(pending_record["mileage_performed"])
        self.assertEqual(pending_record["date_performed"], "")

        complete = client.post(
            "/pro/customers/1/vehicles/1/repairs/8/completion",
            data={
                "completion_date": "2026-07-01",
                "completion_mileage": "151000",
                "completion_notes": "Completed.",
                "final_inspection_passed": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(complete.status_code, 303)
        completed_record = dict(
            self.conn.execute(
                "SELECT * FROM maintenance_records WHERE source_repair_record_id = 8"
            ).fetchone()
        )
        self.assertEqual(completed_record["service_type"], "Oil Change")
        self.assertEqual(completed_record["date_performed"], "2026-07-01")
        self.assertEqual(completed_record["mileage_performed"], 151000)
        self.assertEqual(completed_record["due_mileage"], 156000)
        self.assertEqual(completed_record["due_date"], "2027-01-01")
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM maintenance_records WHERE source_repair_record_id = 8"
            ).fetchone()[0],
            1,
        )

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
