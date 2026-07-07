import os
import sqlite3
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from routers import pro as pro_module


class NonClosingConnection(sqlite3.Connection):
    def close(self):
        pass


class CalendarFoundationTests(unittest.TestCase):
    def memory_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    def test_calendar_schema_creation(self):
        conn = self.memory_conn()
        try:
            pro_module.ensure_calendar_schema(conn)
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            conn.close()

        self.assertIn("shop_availability", tables)
        self.assertIn("shop_closed_days", tables)
        self.assertIn("service_appointments", tables)

    def test_saving_shop_availability(self):
        conn = self.memory_conn()
        try:
            pro_module.save_shop_availability(
                conn,
                [
                    {"day_of_week": 0, "is_open": True, "start_time": "08:00", "end_time": "16:00"},
                    {"day_of_week": 5, "is_open": False, "start_time": "09:00", "end_time": "12:00"},
                ],
                appointment_length_minutes=45,
                buffer_minutes=15,
            )
            rows = pro_module.load_shop_availability(conn)
        finally:
            conn.close()

        monday = rows[0]
        self.assertTrue(monday["is_open"])
        self.assertEqual(monday["start_time"], "08:00")
        self.assertEqual(monday["appointment_length_minutes"], 45)
        self.assertEqual(monday["buffer_minutes"], 15)

    def test_creating_and_changing_service_appointment(self):
        conn = self.memory_conn()
        try:
            appointment_id = pro_module.create_service_appointment(
                conn,
                {
                    "customer_name": "Natalie King",
                    "customer_phone": "555-123-4567",
                    "vehicle_label": "2008 Toyota Sequoia",
                    "service_name": "Oil Change",
                    "requested_date": "2026-07-08",
                    "requested_time": "09:00",
                    "source": "manual",
                    "status": "Requested",
                },
            )
            pro_module.update_service_appointment_status(conn, appointment_id, "Confirmed")
            row = conn.execute("SELECT * FROM service_appointments WHERE id = ?", (appointment_id,)).fetchone()
        finally:
            conn.close()

        self.assertEqual(row["customer_name"], "Natalie King")
        self.assertEqual(row["status"], "Confirmed")

    def test_public_booking_route_returns_200_and_creates_requested_appointment(self):
        conn = sqlite3.connect(":memory:", factory=NonClosingConnection, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            with patch.object(pro_module, "crm_db_conn", lambda: conn), patch.dict(
                    os.environ,
                    {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""},
                ):
                client = TestClient(main.app, base_url="http://localhost")
                get_response = client.get("/book/torquemech-shop")
                post_response = client.post(
                    "/book/torquemech-shop",
                    data={
                        "customer_name": "Natalie King",
                        "customer_phone": "555-123-4567",
                        "customer_email": "natalie@example.com",
                        "vehicle_label": "2008 Toyota Sequoia",
                        "service_name": "Oil Change",
                        "requested_date": "2026-07-08",
                        "requested_time": "09:00",
                        "notes": "Morning preferred",
                    },
                    follow_redirects=False,
                )
                row = conn.execute("SELECT * FROM service_appointments").fetchone()
        finally:
            sqlite3.Connection.close(conn)

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(post_response.status_code, 303)
        self.assertEqual(row["status"], "Requested")
        self.assertEqual(row["source"], "customer_booking")

    def test_maintenance_reminder_prefers_builtin_booking_link(self):
        message = pro_module.build_maintenance_reminder_message(
            customer={"first_name": "Natalie"},
            vehicle={"year": 2008, "make": "TOYOTA", "model": "SEQUOIA", "mileage": 183777},
            record={
                "service_type": "Oil Change",
                "maintenance_status_key": "overdue",
                "next_due_mileage": 182000,
                "next_due_date": "2026-07-03",
            },
            sender_context={
                "shop_name": "Bryan from TorqueMech Auto",
                "booking_link": "http://127.0.0.1:8125/book/torquemech-shop",
                "scheduling_link": "https://calendly.com/torquemech/service",
            },
        )

        self.assertIn("Schedule your service here:\nhttp://127.0.0.1:8125/book/torquemech-shop", message)
        self.assertNotIn("calendly.com", message)

    def test_maintenance_reminder_without_links_keeps_reply_fallback(self):
        message = pro_module.build_maintenance_reminder_message(
            customer={"first_name": "Natalie"},
            vehicle={"year": 2008, "make": "TOYOTA", "model": "SEQUOIA"},
            record={"service_type": "Oil Change", "maintenance_status_key": "overdue"},
            sender_context={"shop_name": "Bryan from TorqueMech Auto"},
        )

        self.assertNotIn("Schedule your service here:", message)
        self.assertTrue(message.endswith("Reply here when you're ready to schedule."))


if __name__ == "__main__":
    unittest.main()
