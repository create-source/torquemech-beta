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
              finding TEXT,
              status TEXT,
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


if __name__ == "__main__":
    unittest.main()
