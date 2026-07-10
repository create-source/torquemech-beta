import os
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from routers import pro as pro_module


class NonClosingConnection(sqlite3.Connection):
    def close(self):
        pass


class FakeEstimateDir:
    def mkdir(self, *args, **kwargs):
        return None

    def __truediv__(self, name):
        return FakeEstimatePath(name)


class FakeEstimatePath:
    def __init__(self, name):
        self.name = name

    def write_bytes(self, data):
        return len(data)

    def resolve(self):
        return f"C:/fake-estimates/{self.name}"


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

    def test_default_shop_availability_matches_foundation_schedule(self):
        rows = pro_module.default_shop_availability_rows()

        self.assertEqual(len(rows), 7)
        for day in rows[:5]:
            self.assertTrue(day["is_open"])
            self.assertEqual(day["start_time"], "09:00")
            self.assertEqual(day["end_time"], "17:00")
            self.assertEqual(day["appointment_length_minutes"], 60)
            self.assertEqual(day["buffer_minutes"], 0)
        self.assertFalse(rows[5]["is_open"])
        self.assertFalse(rows[6]["is_open"])

    def test_creating_and_changing_service_appointment(self):
        conn = self.memory_conn()
        try:
            appointment_id = pro_module.create_service_appointment(
                conn,
                {
                    "customer_name": "Natalie King",
                    "customer_phone": "(555)123-4567",
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
                        "customer_phone": "(555)123-4567",
                        "customer_email": "natalie@example.com",
                        "vehicle_label": "2008 Toyota Sequoia",
                        "service_name": "Oil Change",
                        "requested_date": "2026-07-13",
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

    def test_public_booking_rejects_times_outside_shop_schedule(self):
        conn = sqlite3.connect(":memory:", factory=NonClosingConnection, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            pro_module.save_shop_availability(
                conn,
                [
                    {"day_of_week": 0, "is_open": True, "start_time": "10:00", "end_time": "12:00"},
                ],
                appointment_length_minutes=60,
                buffer_minutes=0,
            )
            with patch.object(pro_module, "crm_db_conn", lambda: conn), patch.dict(
                    os.environ,
                    {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""},
                ):
                client = TestClient(main.app, base_url="http://localhost")
                response = client.post(
                    "/book/torquemech-shop",
                    data={
                        "customer_name": "Natalie King",
                        "customer_phone": "(555)123-4567",
                        "customer_email": "natalie@example.com",
                        "vehicle_label": "2008 Toyota Sequoia",
                        "service_name": "Oil Change",
                        "requested_date": "2026-07-13",
                        "requested_time": "09:30",
                        "notes": "",
                    },
                    follow_redirects=False,
                )
                row = conn.execute("SELECT * FROM service_appointments").fetchone()
        finally:
            sqlite3.Connection.close(conn)

        self.assertEqual(response.status_code, 400)
        self.assertIn("This time is outside the shop&#39;s business hours.", response.text)
        self.assertIsNone(row)

    def test_public_booking_rejects_closed_day_and_conflicting_time(self):
        conn = sqlite3.connect(":memory:", factory=NonClosingConnection, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            pro_module.save_shop_availability(
                conn,
                [
                    {"day_of_week": 0, "is_open": True, "start_time": "09:00", "end_time": "17:00"},
                    {"day_of_week": 1, "is_open": False, "start_time": "09:00", "end_time": "17:00"},
                ],
                appointment_length_minutes=60,
                buffer_minutes=15,
            )
            pro_module.create_service_appointment(
                conn,
                {
                    "customer_name": "Existing Customer",
                    "customer_phone": "5555550100",
                    "vehicle_label": "Existing Vehicle",
                    "service_name": "Existing Service",
                    "requested_date": "2026-07-13",
                    "requested_time": "10:00",
                    "status": "Confirmed",
                },
            )
            closed, closed_message = pro_module.is_closed_booking_day(conn, "2026-07-14")
            available, conflict_message = pro_module.is_booking_time_available(conn, "2026-07-13", "11:00", 60)
        finally:
            sqlite3.Connection.close(conn)

        self.assertTrue(closed)
        self.assertEqual(closed_message, "The shop is closed on this day. Please choose another day.")
        self.assertFalse(available)
        self.assertEqual(
            conflict_message,
            "This time is not available based on the shop's schedule. Please choose another available time.",
        )

    def test_public_booking_page_shows_saved_schedule_and_success_copy(self):
        conn = sqlite3.connect(":memory:", factory=NonClosingConnection, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            pro_module.save_shop_availability(
                conn,
                [{"day_of_week": 0, "is_open": True, "start_time": "08:30", "end_time": "16:30"}],
                appointment_length_minutes=90,
                buffer_minutes=15,
            )
            with patch.object(pro_module, "crm_db_conn", lambda: conn), patch.dict(
                    os.environ,
                    {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""},
                ):
                response = TestClient(main.app, base_url="http://localhost").get(
                    "/book/torquemech-shop?success=1"
                )
        finally:
            sqlite3.Connection.close(conn)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Available booking hours", response.text)
        self.assertIn("8:30 AM – 4:30 PM", response.text)
        self.assertIn(
            "Available arrival times use 90-minute scheduling blocks, with 15 minutes between booking windows.",
            response.text,
        )
        self.assertIn("This controls booking availability, not repair duration.", response.text)
        self.assertIn("Your appointment request has been sent.", response.text)

    def test_available_time_dropdown_excludes_pending_and_confirmed_slots(self):
        conn = self.memory_conn()
        try:
            pro_module.save_shop_availability(
                conn,
                [
                    {"day_of_week": 0, "is_open": True, "start_time": "09:00", "end_time": "12:00"},
                    {"day_of_week": 1, "is_open": False, "start_time": "09:00", "end_time": "12:00"},
                ],
                appointment_length_minutes=60,
                buffer_minutes=0,
            )
            for requested_time, status in [("09:00", "Requested"), ("10:00", "Confirmed")]:
                pro_module.create_service_appointment(
                    conn,
                    {
                        "customer_name": "Slot Customer",
                        "customer_phone": "5555550100",
                        "vehicle_label": "Test Vehicle",
                        "service_name": "Test Service",
                        "requested_date": "2026-07-13",
                        "requested_time": requested_time,
                        "status": status,
                    },
                )
            result = pro_module.available_booking_times(conn, "2026-07-13")
            closed_result = pro_module.available_booking_times(conn, "2026-07-14")
        finally:
            conn.close()

        self.assertEqual(result["state"], "available")
        self.assertEqual(result["times"], [{"value": "11:00", "label": "11:00 AM"}])
        self.assertEqual(closed_result["state"], "closed")
        self.assertEqual(
            closed_result["message"],
            "The shop is closed on this day. Please choose another day.",
        )

    def test_month_availability_disables_past_closed_and_fully_booked_days(self):
        conn = self.memory_conn()
        try:
            pro_module.save_shop_availability(
                conn,
                [
                    {"day_of_week": 0, "is_open": True, "start_time": "09:00", "end_time": "10:00"},
                    {"day_of_week": 1, "is_open": False, "start_time": "09:00", "end_time": "10:00"},
                ],
                appointment_length_minutes=60,
                buffer_minutes=0,
            )
            pro_module.create_service_appointment(
                conn,
                {
                    "customer_name": "Booked Customer",
                    "customer_phone": "5555550100",
                    "service_name": "Service",
                    "requested_date": "2026-07-13",
                    "requested_time": "09:00",
                    "status": "Requested",
                },
            )
            with patch.object(pro_module, "shop_today", lambda: pro_module.date(2026, 7, 8)):
                result = pro_module.booking_availability_for_month(conn, "2026-07")
        finally:
            conn.close()

        availability = {item["date"]: item["available"] for item in result["days"]}
        self.assertFalse(availability["2026-07-07"])
        self.assertFalse(availability["2026-07-13"])
        self.assertFalse(availability["2026-07-14"])
        self.assertTrue(availability["2026-07-20"])

    def test_booking_last_slot_makes_date_unavailable(self):
        conn = self.memory_conn()
        try:
            pro_module.save_shop_availability(
                conn,
                [{"day_of_week": 0, "is_open": True, "start_time": "09:00", "end_time": "10:00"}],
                appointment_length_minutes=60,
                buffer_minutes=0,
            )
            with patch.object(pro_module, "shop_today", lambda: pro_module.date(2026, 7, 8)):
                before = pro_module.available_booking_times(conn, "2026-07-13")
                pro_module.create_service_appointment(
                    conn,
                    {
                        "customer_name": "Last Slot",
                        "customer_phone": "5555550100",
                        "service_name": "Service",
                        "requested_date": "2026-07-13",
                        "requested_time": "09:00",
                        "status": "Confirmed",
                    },
                )
                after = pro_module.available_booking_times(conn, "2026-07-13")
        finally:
            conn.close()

        self.assertEqual(before["state"], "available")
        self.assertEqual(after["state"], "unavailable")
        self.assertEqual(
            after["message"],
            "No appointment times are available for this day. Please choose another day.",
        )

    def test_calendar_request_status_actions_and_friendly_notices(self):
        conn = sqlite3.connect(":memory:", factory=NonClosingConnection, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            appointment_id = pro_module.create_service_appointment(
                conn,
                {
                    "customer_name": "Booking Customer",
                    "customer_phone": "5555550100",
                    "customer_email": "customer@example.com",
                    "vehicle_label": "2020 Honda Civic",
                    "service_name": "Brake Inspection",
                    "requested_date": "2026-07-13",
                    "requested_time": "09:00",
                    "notes": "Please call first",
                    "status": "Requested",
                },
            )
            for index in range(5):
                pro_module.create_service_appointment(
                    conn,
                    {
                        "customer_name": f"Additional Customer {index + 1}",
                        "customer_phone": "5555550100",
                        "vehicle_label": "Test Vehicle",
                        "service_name": "Test Service",
                        "requested_date": "2026-07-14",
                        "requested_time": f"{10 + index}:00",
                        "status": "Requested",
                    },
                )
            with patch.object(pro_module, "crm_db_conn", lambda: conn), patch.dict(
                os.environ,
                {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""},
            ):
                client = TestClient(main.app, base_url="http://localhost")
                pending_page = client.get("/pro/calendar")
                confirm_response = client.post(
                    f"/pro/calendar/{appointment_id}/status",
                    data={"status": "Confirmed"},
                    follow_redirects=False,
                )
                confirmed_page = client.get("/pro/calendar?notice=confirmed")
                handled_response = client.post(
                    f"/pro/calendar/{appointment_id}/status",
                    data={"status": "Handled"},
                    follow_redirects=False,
                )
                row = conn.execute(
                    "SELECT status FROM service_appointments WHERE id = ?",
                    (appointment_id,),
                ).fetchone()
        finally:
            sqlite3.Connection.close(conn)

        self.assertIn("Pending Request", pending_page.text)
        self.assertIn("Pending Requests (6)", pending_page.text)
        self.assertIn('aria-label="Pending Requests" open', pending_page.text)
        self.assertIn('class="tm-calendar-item" hidden', pending_page.text)
        self.assertIn('data-show-more>Show More</button>', pending_page.text)
        self.assertLess(pending_page.text.index("Pending Requests (6)"), pending_page.text.index("Add Appointment"))
        self.assertIn("customer@example.com", pending_page.text)
        self.assertIn("2020 Honda Civic", pending_page.text)
        self.assertNotIn("Copy Confirmation Message", pending_page.text)
        self.assertNotIn("Copy Cancellation Message", pending_page.text)
        self.assertEqual(confirm_response.headers["location"], "/pro/calendar?notice=confirmed")
        self.assertIn("Appointment confirmed.", confirmed_page.text)
        self.assertIn("Confirmed Appointments", confirmed_page.text)
        self.assertIn("Copy Confirmation Message", confirmed_page.text)
        self.assertEqual(handled_response.headers["location"], "/pro/calendar?notice=handled")
        self.assertEqual(row["status"], "Handled")

    def test_confirmed_appointment_can_be_rescheduled_and_cancelled(self):
        conn = sqlite3.connect(":memory:", factory=NonClosingConnection, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            pro_module.save_shop_availability(
                conn,
                [{"day_of_week": 0, "is_open": True, "start_time": "09:00", "end_time": "12:00"}],
                appointment_length_minutes=60,
                buffer_minutes=0,
            )
            appointment_id = pro_module.create_service_appointment(
                conn,
                {
                    "customer_name": "Confirmed Customer",
                    "customer_phone": "5555550100",
                    "service_name": "Brake Service",
                    "requested_date": "2026-07-13",
                    "requested_time": "09:00",
                    "status": "Confirmed",
                },
            )
            with patch.object(pro_module, "crm_db_conn", lambda: conn), patch.object(
                pro_module, "shop_today", lambda: pro_module.date(2026, 7, 8)
            ), patch.dict(
                os.environ,
                {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""},
            ):
                client = TestClient(main.app, base_url="http://localhost")
                calendar_page = client.get("/pro/calendar")
                excluded_times = client.get(
                    f"/book/torquemech-shop/available-times"
                    f"?date=2026-07-13&exclude_appointment_id={appointment_id}"
                ).json()
                reschedule_response = client.post(
                    f"/pro/calendar/{appointment_id}/reschedule",
                    data={"requested_date": "2026-07-13", "requested_time": "10:00"},
                    follow_redirects=False,
                )
                old_slot = pro_module.is_booking_time_available(conn, "2026-07-13", "09:00")
                new_slot = pro_module.is_booking_time_available(conn, "2026-07-13", "10:00")
                rescheduled_page = client.get("/pro/calendar?notice=rescheduled")
                cancel_response = client.post(
                    f"/pro/calendar/{appointment_id}/cancel",
                    follow_redirects=False,
                )
                canceled_slot = pro_module.is_booking_time_available(conn, "2026-07-13", "10:00")
                canceled_page = client.get("/pro/calendar?notice=cancelled")
                row = conn.execute(
                    "SELECT requested_date, requested_time, status FROM service_appointments WHERE id = ?",
                    (appointment_id,),
                ).fetchone()
        finally:
            sqlite3.Connection.close(conn)

        self.assertIn("Reschedule", calendar_page.text)
        self.assertIn("Cancel Appointment", calendar_page.text)
        self.assertIn("Copy Confirmation Message", calendar_page.text)
        self.assertNotIn("Copy Cancellation Message", calendar_page.text)
        self.assertIn({"value": "09:00", "label": "9:00 AM"}, excluded_times["times"])
        self.assertEqual(reschedule_response.headers["location"], "/pro/calendar?notice=rescheduled")
        self.assertTrue(old_slot[0])
        self.assertFalse(new_slot[0])
        self.assertIn("Appointment rescheduled.", rescheduled_page.text)
        self.assertIn("10:00 AM", rescheduled_page.text)
        self.assertIn("Copy Reschedule Message", rescheduled_page.text)
        self.assertEqual(cancel_response.headers["location"], "/pro/calendar?notice=cancelled")
        self.assertTrue(canceled_slot[0])
        self.assertIn("Appointment canceled.", canceled_page.text)
        self.assertIn("Appointment History (1)", canceled_page.text)
        self.assertNotIn('aria-label="Appointment History" open', canceled_page.text)
        self.assertIn("Copy Message", canceled_page.text)
        self.assertEqual(row["status"], "Cancelled")
        self.assertEqual(row["requested_time"], "10:00")

    def test_declined_request_shows_declined_message_and_history(self):
        conn = sqlite3.connect(":memory:", factory=NonClosingConnection, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            appointment_id = pro_module.create_service_appointment(
                conn,
                {
                    "customer_name": "Decline Customer",
                    "customer_phone": "5555550100",
                    "customer_email": "decline@example.com",
                    "vehicle_label": "1998 Toyota Camry",
                    "service_name": "Oil Change",
                    "requested_date": "2026-07-22",
                    "requested_time": "16:00",
                    "status": "Requested",
                },
            )
            with patch.object(pro_module, "crm_db_conn", lambda: conn), patch.dict(
                os.environ,
                {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""},
            ):
                client = TestClient(main.app, base_url="http://localhost")
                pending_page = client.get("/pro/calendar")
                decline_response = client.post(
                    f"/pro/calendar/{appointment_id}/status",
                    data={"status": "Declined"},
                    follow_redirects=False,
                )
                declined_page = client.get("/pro/calendar?notice=declined")
                row = conn.execute(
                    "SELECT status FROM service_appointments WHERE id = ?",
                    (appointment_id,),
                ).fetchone()
        finally:
            sqlite3.Connection.close(conn)

        self.assertIn("Confirm Appointment", pending_page.text)
        self.assertIn("Decline Request", pending_page.text)
        self.assertNotIn("Copy Confirmation Message", pending_page.text)
        self.assertNotIn("Copy Cancellation Message", pending_page.text)
        self.assertEqual(decline_response.headers["location"], "/pro/calendar?notice=declined")
        self.assertEqual(row["status"], "Declined")
        self.assertIn("Request declined", declined_page.text)
        self.assertIn("Declined", declined_page.text)
        self.assertIn("Appointment History (1)", declined_page.text)
        self.assertNotIn('aria-label="Appointment History" open', declined_page.text)
        self.assertIn("Copy Message", declined_page.text)
        self.assertIn(
            "We\u2019re unable to accept your appointment request for your 1998 Toyota Camry "
            "regarding Oil Change on 07/22/2026 at 4:00 PM.",
            declined_page.text,
        )
        self.assertNotIn("has been canceled", declined_page.text)

    def test_calendar_review_groups_counts_active_and_history_records(self):
        grouped = pro_module.group_booking_review_appointments(
            [
                {"id": 1, "status": "Requested", "requested_date": "2026-07-22", "requested_time": "16:00"},
                {"id": 2, "status": "Confirmed", "requested_date": "2026-07-13", "requested_time": "09:00"},
                {"id": 3, "status": "Rescheduled", "requested_date": "2026-07-14", "requested_time": "10:00"},
                {"id": 4, "status": "Cancelled", "requested_date": "2026-07-15", "requested_time": "11:00"},
                {"id": 5, "status": "Declined", "requested_date": "2026-07-16", "requested_time": "12:00"},
                {"id": 6, "status": "Handled", "requested_date": "2026-07-17", "requested_time": "13:00"},
                {"id": 7, "status": "Confirmed", "requested_date": "2026-07-01", "requested_time": "14:00"},
            ],
            pro_module.date(2026, 7, 10),
        )

        self.assertEqual([item["id"] for item in grouped["pending"]], [1])
        self.assertEqual([item["id"] for item in grouped["confirmed"]], [2, 3])
        self.assertEqual(len(grouped["history"]), 4)
        self.assertIn("Past Appointment", {item.get("display_status") for item in grouped["history"]})
        self.assertIn("Handled", {item.get("display_status") for item in grouped["history"]})

    def test_reschedule_rejects_conflicting_slot_server_side(self):
        conn = self.memory_conn()
        try:
            pro_module.save_shop_availability(
                conn,
                [{"day_of_week": 0, "is_open": True, "start_time": "09:00", "end_time": "12:00"}],
                appointment_length_minutes=60,
                buffer_minutes=0,
            )
            moving_id = pro_module.create_service_appointment(
                conn,
                {
                    "customer_name": "Moving",
                    "customer_phone": "5555550100",
                    "service_name": "Service",
                    "requested_date": "2026-07-13",
                    "requested_time": "09:00",
                    "status": "Confirmed",
                },
            )
            pro_module.create_service_appointment(
                conn,
                {
                    "customer_name": "Blocking",
                    "customer_phone": "5555550101",
                    "service_name": "Service",
                    "requested_date": "2026-07-13",
                    "requested_time": "10:00",
                    "status": "Requested",
                },
            )
            with patch.object(pro_module, "shop_today", lambda: pro_module.date(2026, 7, 8)):
                with self.assertRaises(Exception):
                    pro_module.reschedule_service_appointment(
                        conn, moving_id, "2026-07-13", "10:00"
                    )
            row = conn.execute(
                "SELECT requested_time FROM service_appointments WHERE id = ?",
                (moving_id,),
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(row["requested_time"], "09:00")

    def test_public_booking_confirmation_uses_saved_shop_contact_details(self):
        conn = sqlite3.connect(":memory:", factory=NonClosingConnection, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            pro_module.save_shop_settings(
                conn,
                {
                    "shop_phone": "(555) 123-4567",
                    "shop_email": "support@shop.com",
                },
            )
            with patch.object(pro_module, "crm_db_conn", lambda: conn), patch.dict(
                os.environ,
                {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""},
            ):
                client = TestClient(main.app, base_url="http://localhost")
                both_response = client.get("/book/torquemech-shop?success=1")
                pro_module.save_shop_settings(conn, {"shop_phone": "", "shop_email": ""})
                neither_response = client.get("/book/torquemech-shop?success=1")
        finally:
            sqlite3.Connection.close(conn)

        self.assertIn("Need to reschedule or cancel? Please contact the shop directly at", both_response.text)
        self.assertIn("(555)123-4567", both_response.text)
        self.assertIn("support@shop.com", both_response.text)
        self.assertIn('href="tel:5551234567"', both_response.text)
        self.assertIn("Need to reschedule or cancel? Please contact the shop directly.", neither_response.text)
        contact_copy = neither_response.text.split('class="tm-book-success-contact">', 1)[1].split("</p>", 1)[0]
        self.assertNotIn('href="tel:', contact_copy)
        self.assertNotIn('href="mailto:', contact_copy)

    def test_public_booking_uses_drop_off_time_language(self):
        template = (main.BASE_DIR / "templates" / "booking.html").read_text(encoding="utf-8")
        picker_script = (main.BASE_DIR / "static" / "available_date_picker.js").read_text(encoding="utf-8")

        self.assertIn("Preferred Drop-Off / Appointment Time", template)
        self.assertIn("Select an available drop-off time", template)
        self.assertIn(
            "This is your preferred drop-off or appointment time. Repair duration depends on the service, "
            "inspection, parts availability, and shop schedule.",
            template,
        )
        self.assertIn("Select an available drop-off time", picker_script)

    def test_appointment_customer_copy_messages_include_schedule_and_contact_context(self):
        messages = pro_module.appointment_customer_messages(
            {
                "customer_name": "Natalie King",
                "service_name": "Brake Inspection",
                "requested_date": "2026-07-13",
                "requested_time": "10:00",
            },
            {
                "shop_name": "TorqueMech Auto",
                "shop_phone": "5592223333",
                "shop_email": "service@torquemech.test",
            },
        )

        for message in messages.values():
            self.assertIn("Natalie King", message)
            self.assertIn("TorqueMech Auto", message)
            self.assertIn("Brake Inspection", message)
            self.assertIn("(559) 222-3333", message)
            self.assertIn("service@torquemech.test", message)
            self.assertIn("\n\n", message)
        duration_note = (
            "Please note that repair duration may vary depending on the service, inspection findings, "
            "parts availability, and shop schedule."
        )
        for key in ("confirmation_message", "reschedule_message"):
            self.assertIn("07/13/2026", messages[key])
            self.assertIn("10:00 AM", messages[key])
            self.assertIn(duration_note, messages[key])
        self.assertIn("07/13/2026", messages["cancellation_message"])
        self.assertIn("10:00 AM", messages["cancellation_message"])
        self.assertNotIn(duration_note, messages["cancellation_message"])
        self.assertNotIn(duration_note, messages["declined_message"])
        self.assertIn("on 07/13/2026 at 10:00 AM", messages["declined_message"])
        self.assertIn("has been confirmed", messages["confirmation_message"])
        self.assertIn("new drop-off / appointment time", messages["reschedule_message"])
        self.assertIn("has been cancelled", messages["cancellation_message"])

        fallback = pro_module.appointment_customer_messages(
            {
                "customer_name": "",
                "service_name": "",
                "requested_date": "",
                "requested_time": "",
            },
            {},
        )
        self.assertIn("Hi there, this is your mechanic.", fallback["confirmation_message"])
        self.assertIn("please contact the shop directly.", fallback["confirmation_message"])

    def test_calendar_conversion_links_existing_customer_and_vehicle(self):
        conn = sqlite3.connect(":memory:", factory=NonClosingConnection, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            pro_module.ensure_customer_status_schema(conn)
            now = "2026-07-09T12:00:00"
            customer_id = conn.execute(
                """
                INSERT INTO customers (first_name, last_name, phone, email, customer_status, notes, created_at, updated_at)
                VALUES ('Natalie', 'King', '5552223333', 'natalie@example.com', 'active', '', ?, ?)
                """,
                (now, now),
            ).lastrowid
            vehicle_id = conn.execute(
                """
                INSERT INTO customer_vehicles (customer_id, year, make, model, created_at, updated_at)
                VALUES (?, 1998, 'Toyota', 'Camry', ?, ?)
                """,
                (customer_id, now, now),
            ).lastrowid
            appointment_id = pro_module.create_service_appointment(
                conn,
                {
                    "customer_name": "Natalie King",
                    "customer_phone": "5552223333",
                    "customer_email": "natalie@example.com",
                    "vehicle_label": "1998 Toyota Camry",
                    "service_name": "Oil Change",
                    "requested_date": "2026-07-22",
                    "requested_time": "16:00",
                    "status": "Confirmed",
                },
            )
            with patch.object(pro_module, "crm_db_conn", lambda: conn), patch.dict(
                os.environ,
                {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""},
            ):
                client = TestClient(main.app, base_url="http://localhost")
                response = client.post(
                    f"/pro/calendar/{appointment_id}/convert",
                    data={
                        "customer_mode": "existing",
                        "customer_id": str(customer_id),
                        "vehicle_mode": "existing",
                        "vehicle_id": str(vehicle_id),
                        "conversion_action": "save",
                    },
                    follow_redirects=False,
                )
                page = client.get("/pro/calendar?notice=linked")
                row = conn.execute(
                    "SELECT customer_id, vehicle_id, status FROM service_appointments WHERE id = ?",
                    (appointment_id,),
                ).fetchone()
        finally:
            sqlite3.Connection.close(conn)

        self.assertEqual(response.headers["location"], "/pro/calendar?notice=linked")
        self.assertEqual(row["customer_id"], customer_id)
        self.assertEqual(row["vehicle_id"], vehicle_id)
        self.assertEqual(row["status"], "Confirmed")
        self.assertIn("Linked to customer", page.text)
        self.assertIn("Open Customer", page.text)
        self.assertIn("Create Estimate", page.text)
        self.assertNotIn("Add to Customer / Start Job", page.text)

    def test_calendar_conversion_creates_new_customer_vehicle_and_estimator_handoff(self):
        conn = sqlite3.connect(":memory:", factory=NonClosingConnection, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            pro_module.ensure_customer_status_schema(conn)
            appointment_id = pro_module.create_service_appointment(
                conn,
                {
                    "customer_name": "Sam Driver",
                    "customer_phone": "(555) 111-2222",
                    "customer_email": "sam@example.com",
                    "vehicle_label": "2008 Toyota Sequoia",
                    "service_name": "Water Pump Replacement",
                    "requested_date": "2026-07-23",
                    "requested_time": "09:00",
                    "notes": "Coolant leak near front of engine",
                    "status": "Confirmed",
                },
            )
            with patch.object(pro_module, "crm_db_conn", lambda: conn), patch.dict(
                os.environ,
                {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""},
            ):
                client = TestClient(main.app, base_url="http://localhost")
                response = client.post(
                    f"/pro/calendar/{appointment_id}/convert",
                    data={
                        "customer_mode": "new",
                        "new_customer_name": "Sam Driver",
                        "new_customer_phone": "(555) 111-2222",
                        "new_customer_email": "sam@example.com",
                        "new_vehicle_year": "2008",
                        "new_vehicle_make": "Toyota",
                        "new_vehicle_model": "Sequoia",
                        "conversion_action": "estimate",
                    },
                    follow_redirects=False,
                )
                appointment = conn.execute(
                    "SELECT * FROM service_appointments WHERE id = ?",
                    (appointment_id,),
                ).fetchone()
                customers = conn.execute("SELECT * FROM customers").fetchall()
                vehicles = conn.execute("SELECT * FROM customer_vehicles").fetchall()
                second_customer_id, second_vehicle_id, _ = pro_module.link_appointment_customer_vehicle(
                    conn,
                    appointment_id,
                    {
                        "customer_mode": "new",
                        "new_customer_name": "Sam Driver",
                        "new_customer_phone": "(555) 111-2222",
                        "new_customer_email": "sam@example.com",
                        "new_vehicle_year": "2008",
                        "new_vehicle_make": "Toyota",
                        "new_vehicle_model": "Sequoia",
                    },
                )
                customer_count = conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"]
                vehicle_count = conn.execute("SELECT COUNT(*) AS count FROM customer_vehicles").fetchone()["count"]
        finally:
            sqlite3.Connection.close(conn)

        self.assertEqual(len(customers), 1)
        self.assertEqual(len(vehicles), 1)
        self.assertEqual(appointment["customer_id"], customers[0]["id"])
        self.assertEqual(appointment["vehicle_id"], vehicles[0]["id"])
        self.assertIn("/estimator?", response.headers["location"])
        self.assertIn("source=appointment", response.headers["location"])
        self.assertIn(f"appointment_id={appointment_id}", response.headers["location"])
        self.assertIn(f"customer_id={customers[0]['id']}", response.headers["location"])
        self.assertIn(f"vehicle_id={vehicles[0]['id']}", response.headers["location"])
        self.assertIn("service_text=Water+Pump+Replacement", response.headers["location"])
        self.assertEqual(second_customer_id, customers[0]["id"])
        self.assertEqual(second_vehicle_id, vehicles[0]["id"])
        self.assertEqual(customer_count, 1)
        self.assertEqual(vehicle_count, 1)

    def test_calendar_estimate_save_links_back_to_appointment(self):
        conn = sqlite3.connect(":memory:", factory=NonClosingConnection, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            pro_module.ensure_customer_status_schema(conn)
            pro_module.ensure_calendar_schema(conn)
            now = "2026-07-09T12:00:00"
            customer_id = conn.execute(
                """
                INSERT INTO customers (first_name, last_name, phone, email, customer_status, notes, created_at, updated_at)
                VALUES ('Sam', 'Driver', '5551112222', 'sam@example.com', 'active', '', ?, ?)
                """,
                (now, now),
            ).lastrowid
            vehicle_id = conn.execute(
                """
                INSERT INTO customer_vehicles (customer_id, year, make, model, created_at, updated_at)
                VALUES (?, 2008, 'Toyota', 'Sequoia', ?, ?)
                """,
                (customer_id, now, now),
            ).lastrowid
            appointment_id = pro_module.create_service_appointment(
                conn,
                {
                    "customer_id": customer_id,
                    "vehicle_id": vehicle_id,
                    "customer_name": "Sam Driver",
                    "customer_phone": "5551112222",
                    "vehicle_label": "2008 Toyota Sequoia",
                    "service_name": "Water Pump Replacement",
                    "requested_date": "2026-07-23",
                    "requested_time": "09:00",
                    "status": "Confirmed",
                },
            )
            with patch.object(pro_module, "crm_db_conn", lambda: conn), patch.object(
                pro_module, "ESTIMATE_PDF_DIR", FakeEstimateDir()
            ):
                result = pro_module.record_estimate_pdf_document(
                    pdf_bytes=b"%PDF-1.4 test",
                    customer_id=customer_id,
                    vehicle_id=vehicle_id,
                    customer_name="Sam Driver",
                    vehicle_label="2008 Toyota Sequoia",
                    related_title="Water Pump Replacement",
                    estimate_total=500,
                    payload={"source": "appointment", "appointment_id": appointment_id},
                )
                row = conn.execute(
                    "SELECT estimate_id FROM service_appointments WHERE id = ?",
                    (appointment_id,),
                ).fetchone()
        finally:
            sqlite3.Connection.close(conn)

        self.assertIsNotNone(result)
        self.assertEqual(row["estimate_id"], result["id"])

    def test_public_booking_stores_structured_appointment_vehicle_fields(self):
        conn = sqlite3.connect(":memory:", factory=NonClosingConnection, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            pro_module.save_shop_availability(
                conn,
                [{"day_of_week": 2, "is_open": True, "start_time": "09:00", "end_time": "17:00"}],
                appointment_length_minutes=60,
                buffer_minutes=0,
            )
            with patch.object(pro_module, "crm_db_conn", lambda: conn), patch.object(
                pro_module, "shop_today", lambda: pro_module.date(2026, 7, 8)
            ), patch.dict(
                os.environ,
                {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""},
            ):
                client = TestClient(main.app, base_url="http://localhost")
                response = client.post(
                    "/book/torquemech-shop",
                    data={
                        "customer_name": "Casey Coupe",
                        "customer_phone": "5552224444",
                        "customer_email": "casey@example.com",
                        "vehicle_year": "2023",
                        "vehicle_make": "Kia",
                        "vehicle_model": "Forte Coupe",
                        "service_name": "Brake Inspection",
                        "requested_date": "2026-07-22",
                        "requested_time": "09:00",
                        "appointment_length_minutes": "60",
                    },
                    follow_redirects=False,
                )
                row = conn.execute("SELECT * FROM service_appointments").fetchone()
        finally:
            sqlite3.Connection.close(conn)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(row["vehicle_year"], "2023")
        self.assertEqual(row["vehicle_make"], "Kia")
        self.assertEqual(row["vehicle_model"], "Forte Coupe")
        self.assertEqual(row["vehicle_label"], "2023 Kia Forte Coupe")

    def test_appointment_vehicle_parts_prefers_structured_and_conservative_legacy_parse(self):
        structured = pro_module.appointment_vehicle_parts(
            {
                "vehicle_label": "2023/Kia/Coupe",
                "vehicle_year": "2023",
                "vehicle_make": "Kia",
                "vehicle_model": "Forte Coupe",
            }
        )
        malformed = pro_module.appointment_vehicle_parts({"vehicle_label": "2023/Kia/Coupe"})
        reliable_slash = pro_module.appointment_vehicle_parts({"vehicle_label": "2023/Kia/Forte Coupe"})

        self.assertEqual(structured, {"year": "2023", "make": "Kia", "model": "Forte Coupe"})
        self.assertEqual(malformed, {"year": "2023", "make": "Kia", "model": ""})
        self.assertEqual(reliable_slash, {"year": "2023", "make": "Kia", "model": "Forte Coupe"})
        self.assertEqual(
            pro_module.appointment_estimator_href(
                {
                    "id": 7,
                    "customer_id": 1,
                    "vehicle_id": 2,
                    "vehicle_year": "2023",
                    "vehicle_make": "Kia",
                    "vehicle_model": "Forte Coupe",
                    "service_name": "Brake Inspection",
                }
            ),
            "/estimator?source=appointment&appointment_id=7&customer_id=1&vehicle_id=2&year=2023&make=Kia&model=Forte+Coupe&displayModel=Forte+Coupe&service_text=Brake+Inspection&recommended_repair=Brake+Inspection&notes=Source%3A+Appointment+%237",
        )

    def test_calendar_conversion_controls_messages_and_clear_behavior_are_rendered(self):
        template = (main.BASE_DIR / "templates" / "pro" / "calendar.html").read_text(encoding="utf-8")
        app_js = (main.BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
        helper = (main.BASE_DIR / "static" / "pro_form_helpers.js").read_text(encoding="utf-8")

        for field_id in ("new_vehicle_year_", "new_vehicle_make_", "new_vehicle_model_", "new_vehicle_mileage_"):
            self.assertIn(field_id, template)
        self.assertIn("data-appointment-clearable", template)
        self.assertIn("data-clear-dependent=\"#new_vehicle_model_", template)
        self.assertIn("data-pro-mileage-input data-appointment-clearable", template)
        self.assertIn("data-copy-source=\"confirmation\"", template)
        self.assertIn("data-copy-source=\"reschedule\"", template)
        self.assertIn("data-reset-appointment-message", template)
        self.assertIn('"value" in (source || {}) ? source.value.trim()', template)
        self.assertIn("source.value = source.dataset.defaultMessage", template)
        self.assertIn("button.innerHTML = \"&times;\"", template)
        self.assertIn("dependent.value = \"\"", template)
        self.assertIn("const yearClearButton", app_js)
        self.assertIn("vehicle-year-clear", app_js)
        self.assertIn("vehicle.make = \"\";", app_js)
        self.assertIn("vehicle.model = \"\";", app_js)
        self.assertIn("input.value = digitsOnly(input.value);", helper)
        self.assertIn("return Number(digits).toLocaleString();", helper)

    def test_booking_date_has_one_picker_and_no_custom_clear_button(self):
        booking_template = (main.BASE_DIR / "templates" / "booking.html").read_text(encoding="utf-8")
        helper = (main.BASE_DIR / "static" / "pro_form_helpers.js").read_text(encoding="utf-8")

        self.assertIn('data-pro-date-clear="off"', booking_template)
        self.assertIn('data-tm-date-clear="off"', booking_template)
        self.assertIn('input.dataset.tmDateEnhanced = "1"', helper)
        self.assertIn('input.closest(".tm-date-input-wrap")', helper)
        self.assertIn('if (input.dataset.proDateClear !== "off")', helper)
        self.assertIn('wrapper.dataset.noClear = "1"', helper)

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

    def test_production_booking_link_is_canonical_https_url(self):
        expected = "https://torquemech.com/book/torquemech-shop"
        request = SimpleNamespace(
            url=SimpleNamespace(hostname="www.torquemech.com"),
            base_url="http://www.torquemech.com/",
        )
        template = (main.BASE_DIR / "templates" / "pro" / "shop_settings.html").read_text(encoding="utf-8")

        self.assertEqual(pro_module.build_shop_booking_link({}, request), expected)
        self.assertIn('href="{{ profile.booking_link }}" target="_blank"', template)
        self.assertIn("Send this link to customers so they can request an appointment.", template)
        self.assertIn(">Open Booking Page</a>", template)

    def test_shop_settings_save_profile_pricing_and_reuse_shop_name(self):
        conn = self.memory_conn()
        try:
            profile = pro_module.save_shop_settings(
                conn,
                {
                    "shop_name": "Htut Auto Care",
                    "shop_phone": "559-222-3333",
                    "shop_email": "service@htut.example",
                    "shop_address": "742 Cedar Ave",
                    "shop_city": "Fresno",
                    "shop_state": "CA",
                    "shop_zip": "93701",
                    "default_labor_rate": "$135.50",
                    "use_tax_rate": "1",
                    "tax_rate": "8.250",
                    "shop_supplies_fee": "12.95",
                    "external_scheduling_link": "https://calendly.com/htut-auto/service",
                },
            )
            loaded = pro_module.load_shop_profile_context(conn)
        finally:
            conn.close()

        self.assertEqual(loaded["shop_name"], "Htut Auto Care")
        self.assertEqual(loaded["shop_phone"], "5592223333")
        self.assertEqual(loaded["shop_city"], "Fresno")
        self.assertEqual(loaded["default_labor_rate"], 135.50)
        self.assertEqual(loaded["labor_rate_default"], 135.50)
        self.assertEqual(loaded["tax_rate"], 8.25)
        self.assertEqual(loaded["tax_rate_default"], 8.25)
        self.assertEqual(loaded["shop_supplies_fee"], 12.95)
        self.assertEqual(loaded["external_scheduling_link"], "https://calendly.com/htut-auto/service")
        self.assertEqual(profile["shop_name"], "Htut Auto Care")

        message = pro_module.build_maintenance_reminder_message(
            customer={"first_name": "Natalie"},
            vehicle={"year": 2008, "make": "TOYOTA", "model": "SEQUOIA"},
            record={"service_type": "Oil Change", "maintenance_status_key": "overdue"},
            sender_context=loaded,
        )

        self.assertIn("Hi Natalie, this is Htut Auto Care.", message)
        self.assertIn("Schedule your service here:\nhttps://calendly.com/htut-auto/service", message)

    def test_shop_settings_phone_normalization_trims_to_ten_digits(self):
        conn = self.memory_conn()
        try:
            pro_module.save_shop_settings(
                conn,
                {
                    "shop_name": "Htut Auto Care",
                    "shop_phone": "6673359567222",
                },
            )
            loaded = pro_module.load_shop_profile_context(conn)
        finally:
            conn.close()

        self.assertEqual(pro_module.clean_shop_phone("6673359567222"), "6673359567")
        self.assertEqual(loaded["shop_phone"], "6673359567")

    def test_shop_settings_unchecked_tax_saves_zero_and_phone_digits(self):
        conn = self.memory_conn()
        try:
            pro_module.save_shop_settings(
                conn,
                {
                    "shop_name": "Htut Auto Care",
                    "shop_phone": "(559) 222-3333",
                    "default_labor_rate": "135.50",
                    "tax_rate": "8.250",
                    "shop_supplies_fee": "12.95",
                },
            )
            loaded = pro_module.load_shop_profile_context(conn)
        finally:
            conn.close()

        self.assertEqual(loaded["shop_phone"], "5592223333")
        self.assertEqual(loaded["tax_rate"], 0.0)
        self.assertEqual(loaded["tax_rate_default"], 0.0)

    def test_shop_settings_form_uses_placeholders_mask_zip_and_tax_toggle(self):
        template = (main.BASE_DIR / "templates" / "pro" / "shop_settings.html").read_text(encoding="utf-8")

        self.assertIn('placeholder="Your shop name"', template)
        self.assertIn('placeholder="(***)***-****"', template)
        self.assertIn('placeholder="shop@example.com"', template)
        self.assertIn('placeholder="123 Main St"', template)
        self.assertIn('placeholder="City"', template)
        self.assertIn('placeholder="State"', template)
        self.assertIn('placeholder="ZIP code"', template)
        self.assertIn(".tm-shop-settings .tm-input::placeholder", template)
        self.assertIn('data-shop-address-input', template)
        self.assertIn("data-shop-phone-input", template)
        self.assertNotIn("formatShopPhone", template)
        self.assertIn('"93701": { city: "Fresno", state: "CA" }', template)
        self.assertIn('"92648": { city: "Huntington Beach", state: "CA" }', template)
        self.assertIn('"21201": { city: "Baltimore", state: "MD" }', template)
        self.assertIn('id="use_tax_rate"', template)
        self.assertIn("data-tax-rate-field", template)
        self.assertIn("const parseFullAddress", template)
        self.assertIn("addressInput?.addEventListener(\"paste\"", template)
        self.assertIn('zipInput?.addEventListener("paste"', template)
        self.assertIn("initializeZipState();", template)
        self.assertIn("if (zipChanged) {", template)
        self.assertIn("phoneInput.value = digitsOnly(phoneInput.value).slice(0, 10)", template)

    def test_shop_schedule_page_uses_foundation_controls_and_helper_text(self):
        template = (main.BASE_DIR / "templates" / "pro" / "shop_schedule.html").read_text(encoding="utf-8")
        settings_template = (main.BASE_DIR / "templates" / "pro" / "shop_settings.html").read_text(encoding="utf-8")

        helper = "Set the days and times customers can request appointments through your TorqueMech booking link."
        self.assertIn(helper, template)
        self.assertIn(helper, settings_template)
        self.assertIn('id="appointment_length_minutes"', template)
        self.assertIn('id="buffer_minutes"', template)
        self.assertIn("data-schedule-day", template)
        self.assertIn("data-schedule-open", template)
        self.assertIn("data-schedule-time", template)

    def test_shop_schedule_route_saves_and_renders_persisted_settings(self):
        conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
        conn.row_factory = sqlite3.Row
        try:
            with patch.dict(os.environ, {"PRO_ENABLED": "true"}):
                with patch.object(pro_module, "crm_db_conn", return_value=conn):
                    client = TestClient(main.app, base_url="http://localhost")
                    post_response = client.post(
                        "/pro/shop-schedule",
                        data={
                            "appointment_length_minutes": "90",
                            "buffer_minutes": "15",
                            "is_open_0": "1",
                            "start_time_0": "08:30",
                            "end_time_0": "16:30",
                            "start_time_1": "09:00",
                            "end_time_1": "17:00",
                            "is_open_2": "1",
                            "start_time_2": "10:00",
                            "end_time_2": "15:00",
                            "is_open_3": "1",
                            "start_time_3": "09:00",
                            "end_time_3": "17:00",
                            "is_open_4": "1",
                            "start_time_4": "09:00",
                            "end_time_4": "17:00",
                            "start_time_5": "09:00",
                            "end_time_5": "17:00",
                            "start_time_6": "09:00",
                            "end_time_6": "17:00",
                        },
                        follow_redirects=False,
                    )
                    response = client.get("/pro/shop-schedule")
        finally:
            sqlite3.Connection.close(conn)

        self.assertEqual(post_response.status_code, 303)
        self.assertEqual(post_response.headers["location"], "/pro/shop-schedule?saved=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn('<option value="90" selected>90 minutes</option>', response.text)
        self.assertIn('<option value="15" selected>15 minutes</option>', response.text)
        self.assertIn('id="start_time_0" name="start_time_0" type="time" value="08:30"', response.text)
        self.assertIn('id="end_time_0" name="end_time_0" type="time" value="16:30"', response.text)
        self.assertIn('id="start_time_2" name="start_time_2" type="time" value="10:00"', response.text)
        self.assertIn('id="end_time_2" name="end_time_2" type="time" value="15:00"', response.text)

    def test_shop_settings_page_does_not_render_seeded_demo_values_as_inputs(self):
        conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
        conn.row_factory = sqlite3.Row
        pro_module.ensure_shop_profile_schema(conn)
        conn.execute(
            """
            INSERT INTO shop_profile (
              id, shop_name, phone, email, address, shop_phone, shop_email, shop_address,
              shop_city, shop_state, shop_zip, website, scheduling_link,
              labor_rate_default, tax_rate_default, warranty_note,
              quote_expiration_days, custom_footer_note, updated_at
            )
            VALUES (1, 'Flow Test Autoeedddd', '555-1212', 'test@example.com', '1 Test St',
                    '(555) 222-3333', 'service@shop.com', '123 Main St',
                    'Fresno', 'CA', '93701', 'https://example.com', '', 100, 0,
                    'Test warranty', 15, 'Test footer', '2026-07-05T06:01:16')
            """,
        )
        conn.commit()

        with patch.dict(os.environ, {"PRO_ENABLED": "true"}):
            with patch.object(pro_module, "crm_db_conn", return_value=conn):
                response = TestClient(main.app, base_url="http://localhost").get("/pro/shop-settings")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('value="Flow Test Autoeedddd"', response.text)
        self.assertNotIn('value="Flow Test Auto', response.text)
        self.assertNotIn('value="555-1212"', response.text)
        self.assertNotIn('value="5552223333"', response.text)
        self.assertNotIn('value="(555) 222-3333"', response.text)
        self.assertNotIn('value="test@example.com"', response.text)
        self.assertNotIn('value="service@shop.com"', response.text)
        self.assertNotIn('value="1 Test St"', response.text)
        self.assertNotIn('value="123 Main St"', response.text)
        self.assertIn('placeholder="Your shop name"', response.text)

    def test_shop_settings_posted_blanks_clear_saved_contact_values(self):
        conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
        conn.row_factory = sqlite3.Row
        pro_module.ensure_shop_profile_schema(conn)
        conn.execute(
            """
            INSERT INTO shop_profile (
              id, shop_name, phone, email, address, shop_phone, shop_email, shop_address,
              shop_city, shop_state, shop_zip, external_scheduling_link,
              labor_rate_default, default_labor_rate, tax_rate_default, tax_rate,
              shop_supplies_fee, updated_at
            )
            VALUES (1, 'Htut Auto Care', '5592223333', 'old@example.com', '742 Cedar Ave',
                    '5592223333', 'old@example.com', '742 Cedar Ave',
                    'Fresno', 'CA', '93701', 'https://calendly.com/old',
                    135, 135, 8.25, 8.25, 12.95, '2026-07-05T06:01:16')
            """,
        )
        conn.commit()

        with patch.dict(os.environ, {"PRO_ENABLED": "true"}):
            with patch.object(pro_module, "crm_db_conn", return_value=conn):
                client = TestClient(main.app, base_url="http://localhost")
                post_response = client.post(
                    "/pro/shop-settings",
                    data={
                        "shop_name": "",
                        "shop_phone": "",
                        "shop_email": "",
                        "shop_address": "",
                        "shop_city": "",
                        "shop_state": "",
                        "shop_zip": "",
                        "default_labor_rate": "135",
                        "shop_supplies_fee": "",
                        "tax_rate": "8.25",
                        "external_scheduling_link": "",
                    },
                    follow_redirects=False,
                )
                response = client.get("/pro/shop-settings")

        self.assertEqual(post_response.status_code, 303)
        self.assertEqual(post_response.headers["location"], "/pro/shop-settings?saved=1")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('value="5592223333"', response.text)
        self.assertNotIn('value="old@example.com"', response.text)
        self.assertNotIn('value="742 Cedar Ave"', response.text)
        self.assertNotIn('value="https://calendly.com/old"', response.text)
        self.assertIn('placeholder="(***)***-****"', response.text)
        self.assertIn('placeholder="shop@example.com"', response.text)
        self.assertIn('placeholder="123 Main St"', response.text)
        self.assertIn('data-tax-rate-field hidden', response.text)
        conn.close()

    def test_shop_settings_zip_lookup_cases_are_present(self):
        template = (main.BASE_DIR / "templates" / "pro" / "shop_settings.html").read_text(encoding="utf-8")

        self.assertIn('"93701": { city: "Fresno", state: "CA" }', template)
        self.assertIn('"92648": { city: "Huntington Beach", state: "CA" }', template)
        self.assertIn('"21201": { city: "Baltimore", state: "MD" }', template)
        self.assertLess(template.index('"92648"'), template.index("const applyZip"))
        self.assertLess(template.index('"21201"'), template.index("const applyZip"))
        self.assertIn("if (zipChanged) {\n      cityManuallyEdited = false;\n      stateManuallyEdited = false;", template)

    def test_shop_settings_tax_toggle_reflects_saved_rate(self):
        conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
        conn.row_factory = sqlite3.Row
        pro_module.ensure_shop_profile_schema(conn)
        conn.execute(
            """
            INSERT INTO shop_profile (id, shop_name, tax_rate_default, tax_rate, updated_at)
            VALUES (1, 'Htut Auto Care', 0, 0, '2026-07-05T06:01:16')
            """,
        )
        conn.commit()

        with patch.dict(os.environ, {"PRO_ENABLED": "true"}):
            with patch.object(pro_module, "crm_db_conn", return_value=conn):
                blank_response = TestClient(main.app, base_url="http://localhost").get("/pro/shop-settings")

        self.assertEqual(blank_response.status_code, 200)
        self.assertIn('data-tax-rate-field hidden', blank_response.text)
        self.assertNotIn('id="use_tax_rate" name="use_tax_rate" type="checkbox" value="1" checked', blank_response.text)

        conn.execute("UPDATE shop_profile SET tax_rate_default = 8.25, tax_rate = 8.25 WHERE id = 1")
        conn.commit()
        with patch.dict(os.environ, {"PRO_ENABLED": "true"}):
            with patch.object(pro_module, "crm_db_conn", return_value=conn):
                taxable_response = TestClient(main.app, base_url="http://localhost").get("/pro/shop-settings")

        self.assertEqual(taxable_response.status_code, 200)
        self.assertIn('id="use_tax_rate" name="use_tax_rate" type="checkbox" value="1" checked', taxable_response.text)
        self.assertIn('value="8.250"', taxable_response.text)
        conn.close()

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
