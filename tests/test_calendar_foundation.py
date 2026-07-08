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
        self.assertIn('placeholder="(555) 123-4567"', template)
        self.assertIn('placeholder="shop@example.com"', template)
        self.assertIn('placeholder="123 Main St"', template)
        self.assertIn('placeholder="City"', template)
        self.assertIn('placeholder="State"', template)
        self.assertIn('placeholder="ZIP code"', template)
        self.assertIn(".tm-shop-settings .tm-input::placeholder", template)
        self.assertIn('data-shop-address-input', template)
        self.assertIn("data-shop-phone-input", template)
        self.assertIn("formatShopPhone", template)
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
