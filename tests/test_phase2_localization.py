import base64
import re
import sqlite3
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from app.i18n import (
    LOCALE_REGISTRY,
    SUPPORTED_LANGUAGES,
    catalog_report,
    client_payload,
    locale_options,
    normalize_language,
    public_client_payload,
    t,
    translate_text,
)
from routers import pro as pro_module


class NonClosingConnection(sqlite3.Connection):
    def close(self):
        pass

    def close_for_cleanup(self):
        super().close()


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise AssertionError("csrf token not found")
    return match.group(1)


def decoded_pdf_stream_text(pdf_bytes: bytes) -> str:
    chunks = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", pdf_bytes, re.S):
        raw = match.group(1).strip()
        candidates = [raw]
        if raw.endswith(b"~>"):
            try:
                candidates.append(base64.a85decode(raw[:-2]))
            except (ValueError, base64.binascii.Error):
                pass
        for candidate in candidates:
            chunks.append(candidate.decode("latin-1", errors="ignore"))
            try:
                chunks.append(zlib.decompress(candidate).decode("latin-1", errors="ignore"))
            except zlib.error:
                pass
    return "\n".join(chunks)


class Phase2LocalizationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close_for_cleanup)

        test_tmp_root = Path(main.BASE_DIR) / "tmp" / "test_email_outboxes"
        test_tmp_root.mkdir(parents=True, exist_ok=True)
        self.outbox_path = test_tmp_root / f"{self._testMethodName}.jsonl"
        self.outbox_path.unlink(missing_ok=True)
        self.addCleanup(lambda: self.outbox_path.unlink(missing_ok=True))

        self.app_db_patch = patch.object(main, "app_db_conn", lambda row_factory=False: self.conn)
        self.crm_patch = patch.object(pro_module, "crm_db_conn", lambda: self.conn)
        self.env_patch = patch.dict(
            "os.environ",
            {
                "PRO_ENABLED": "true",
                "PRO_ACCESS_CODE": "",
                "PRO_QA_KEY": "",
                "TORQUEMECH_BOOTSTRAP_TOKEN": "boot-secret",
                "TORQUEMECH_CUSTOMER_ESTIMATE_LINK_SECRET": "phase2-localization-secret",
                "TORQUEMECH_EMAIL_TRANSPORT": "test",
                "TORQUEMECH_DEV_EMAIL_OUTBOX": str(self.outbox_path),
            },
        )
        self.app_db_patch.start()
        self.crm_patch.start()
        self.env_patch.start()
        self.addCleanup(self.app_db_patch.stop)
        self.addCleanup(self.crm_patch.stop)
        self.addCleanup(self.env_patch.stop)

        pro_module.ensure_auth_schema(self.conn)
        pro_module.ensure_shop_profile_schema(self.conn)
        pro_module.ensure_shop_subscription_schema(self.conn)
        pro_module.ensure_calendar_schema(self.conn)
        pro_module.ensure_customer_status_schema(self.conn)
        pro_module.ensure_findings_records_schema(self.conn)
        pro_module.ensure_repair_estimate_documents_schema(self.conn)
        pro_module.ensure_repair_records_schema(self.conn)
        pro_module.ensure_repair_checklist_schema(self.conn)
        pro_module.ensure_repair_completion_schema(self.conn)
        pro_module.ensure_invoices_schema(self.conn)

    def client(self):
        return TestClient(main.app, base_url="http://localhost")

    def bootstrap_owner(self, client):
        page = client.get("/admin/bootstrap")
        response = client.post(
            "/admin/bootstrap",
            data={
                "csrf_token": csrf_from(page.text),
                "bootstrap_token": "boot-secret",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "owner@example.com",
                "password": "correct-password",
                "confirm_password": "correct-password",
                "shop_name": "Alpha Shop",
                "terms": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

    def set_language(self, language):
        self.conn.execute(
            "UPDATE users SET language_preference = ? WHERE email = 'owner@example.com'",
            (language,),
        )
        self.conn.commit()

    def seed_workflow_records(self):
        self.conn.execute(
            """
            INSERT INTO customers (
              id, shop_id, first_name, last_name, phone, email,
              customer_status, notes, created_at, updated_at
            )
            VALUES (1, 1, 'Sam', 'Driver', '5552223333', 'sam@example.com',
                    'active', 'Prefers morning calls.',
                    '2026-08-01T10:00:00', '2026-08-01T10:00:00')
            """
        )
        self.conn.execute(
            """
            INSERT INTO customer_vehicles (
              id, shop_id, customer_id, year, make, model, engine, vin,
              mileage, notes, created_at, updated_at
            )
            VALUES (1, 1, 1, 2015, 'Honda', 'Accord', '2.4L', 'VIN123',
                    123456, 'Customer reported a rattle.',
                    '2026-08-01T10:00:00', '2026-08-01T10:00:00')
            """
        )
        self.conn.execute(
            """
            INSERT INTO repair_records (
              id, vehicle_id, customer_id, repair_name, repair_date, mileage,
              labor_hours, labor_rate, parts_cost, labor_cost, total_cost,
              status, notes, created_at
            )
            VALUES (1, 1, 1, 'Brake Pad Replacement', '2026-08-02', 123500,
                    1.5, 120, 90, 180, 270, 'Open',
                    'Use ceramic pads.', '2026-08-02T10:00:00')
            """
        )
        self.conn.commit()

    def assert_spanish_payload(self, response_text):
        self.assertIn('<html lang="es"', response_text)
        self.assertIn('data-language="es"', response_text)
        self.assertIn('"language": "es"', response_text)
        self.assertIn('"Customers": "Clientes"', response_text)
        self.assertIn('"Repair Workspace": "Espacio de reparacion"', response_text)

    def assert_locale_payload(self, response_text, language):
        self.assertIn(f'<html lang="{language}"', response_text)
        self.assertIn(f'data-language="{language}"', response_text)
        self.assertIn(f'"language": "{language}"', response_text)

    def test_translation_helpers_default_to_english_and_fallback(self):
        self.assertEqual(normalize_language("fr"), "en")
        self.assertEqual(normalize_language("en-US"), "en")
        self.assertEqual(t("ui.customers", "fr"), "Customers")
        self.assertEqual(t("missing.key", "es"), "missing.key")
        self.assertEqual(translate_text("Customers", "en"), "Customers")

    def test_locale_registry_contains_required_languages_and_dirs(self):
        self.assertEqual(SUPPORTED_LANGUAGES, ("en", "es", "vi", "zh-Hans"))
        self.assertEqual([item["code"] for item in locale_options()], ["en", "es", "vi", "zh-Hans"])
        self.assertEqual(LOCALE_REGISTRY["es"]["name"], "Español")
        self.assertEqual(LOCALE_REGISTRY["vi"]["name"], "Tiếng Việt")
        self.assertEqual(LOCALE_REGISTRY["zh-Hans"]["name"], "简体中文")
        self.assertTrue(all(item["dir"] == "ltr" for item in LOCALE_REGISTRY.values()))

    def test_catalog_completeness_report_has_no_missing_or_unknown_keys(self):
        report = catalog_report()
        for language, details in report.items():
            with self.subTest(language=language):
                self.assertEqual(details["missing"], [])
                self.assertEqual(details["unknown"], [])

    def test_vietnamese_and_chinese_catalogs_do_not_inherit_english_values(self):
        english_payload = client_payload("en")
        for language in ("vi", "zh-Hans"):
            payload = client_payload(language)
            with self.subTest(language=language, catalog="translations"):
                inherited = [
                    key
                    for key, english_value in payload["fallbackTranslations"].items()
                    if payload["translations"].get(key) == english_value
                ]
                self.assertEqual(inherited, [])
            with self.subTest(language=language, catalog="exactText"):
                inherited_exact = [
                    key
                    for key in client_payload("es")["exactText"]
                    if payload["exactText"].get(key) == key
                ]
                self.assertEqual(inherited_exact, [])
            self.assertEqual(english_payload["translations"], {})

    def test_public_payload_stays_restricted_while_localizing_public_labels(self):
        private_payload = client_payload("es")
        for language in ("es", "vi", "zh-Hans"):
            with self.subTest(language=language):
                payload = public_client_payload(language)

                self.assertNotIn("nav.command_center", payload["translations"])
                self.assertNotIn("Invoice Builder", payload["exactText"])
                self.assertNotEqual(payload["exactText"]["Prepared Estimate Review"], "Prepared Estimate Review")
                self.assertNotEqual(payload["exactText"]["Open Estimate PDF"], "Open Estimate PDF")

        self.assertIn("Invoice Builder", private_payload["exactText"])

    def test_translation_helpers_return_spanish_exact_text(self):
        payload = client_payload("es")

        self.assertEqual(payload["language"], "es")
        self.assertEqual(payload["translations"]["ui.customers"], "Clientes")
        self.assertEqual(payload["exactText"]["Customers"], "Clientes")
        self.assertEqual(translate_text("  Repair Workspace  ", "es"), "  Espacio de reparacion  ")

    def test_language_preference_persists_and_returns_spanish_payload(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")

        response = client.post(
            "/account/preferences",
            json={
                "csrf_token": csrf_from(page.text),
                "appearance_preference": "dark",
                "language_preference": "es",
            },
        )
        user = self.conn.execute("SELECT language_preference FROM users WHERE email = 'owner@example.com'").fetchone()
        reloaded = client.get("/pro/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(user["language_preference"], "es")
        self.assertEqual(response.json()["i18n"]["exactText"]["Invoice Builder"], "Constructor de facturas")
        self.assert_spanish_payload(reloaded.text)

    def test_language_preference_persists_for_vietnamese_and_chinese(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")

        for language, expected in [("vi", "Tiếng Việt"), ("zh-Hans", "简体中文")]:
            with self.subTest(language=language):
                response = client.post(
                    "/account/preferences",
                    json={
                        "csrf_token": csrf_from(page.text),
                        "appearance_preference": "system",
                        "language_preference": language,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["language"], language)
                self.assertEqual(response.json()["i18n"]["locale"]["dir"], "ltr")
                names = [item["name"] for item in response.json()["i18n"]["locales"]]
                self.assertIn(expected, names)
                reloaded = client.get("/account/settings")
                self.assert_locale_payload(reloaded.text, language)

    def test_estimate_pdf_system_text_stays_english_for_localized_languages(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")
        payload = {
            "year": 2018,
            "make": "Toyota",
            "model": "Camry",
            "customerName": "Maria Rivera",
            "businessName": "Alpha Shop",
            "mechanicName": "Ada Lovelace",
            "showGeneratedDate": False,
            "lineItems": [
                {
                    "serviceCode": "front_brake_pads_replacement",
                    "serviceText": "Front Brake Pads Replacement",
                    "displayServiceText": "Front Brake Pads Replacement",
                    "quantity": 1,
                    "laborHours": 1.5,
                    "partsPrice": 90,
                    "laborRate": 120,
                    "travelFee": 0,
                    "estimate": 270,
                    "status": "recommended",
                }
            ],
        }
        english_pdf_markers = [
            b"Repair Estimate",
            b"PREPARED BY",
            b"VEHICLE",
            b"Prepared for customer review.",
            b"Repair Services",
            b"Professional estimate line items with status, notes, and totals",
            b"ESTIMATE SUMMARY",
            b"Estimated Total",
            b"Customer estimate total",
        ]
        localized_system_markers = [
            "Presupuesto",
            "Cotizacion",
            "Factura",
            "Cliente",
            "Vehiculo",
            "Báo giá",
            "Hóa đơn",
            "Khách hàng",
            "客户",
            "车辆",
            "发票",
        ]

        for language in ("en", "es", "vi", "zh-Hans"):
            with self.subTest(language=language):
                preference = client.post(
                    "/account/preferences",
                    json={
                        "csrf_token": csrf_from(page.text),
                        "appearance_preference": "system",
                        "language_preference": language,
                    },
                )
                self.assertEqual(preference.status_code, 200)
                self.assertEqual(preference.json()["language"], language)

                response = client.post("/estimate/pdf_multi", json=payload)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "application/pdf")
                pdf_text = decoded_pdf_stream_text(response.content)
                for marker in english_pdf_markers:
                    self.assertIn(marker.decode("ascii"), pdf_text)
                for marker in localized_system_markers:
                    self.assertNotIn(marker, pdf_text)

    def test_phase2_pages_ship_spanish_catalog_without_translating_user_data(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.set_language("es")
        self.seed_workflow_records()

        paths = [
            "/pro/dashboard",
            "/pro/customers",
            "/pro/customers/1/vehicles/1",
            "/estimator",
            "/pro/approvals",
            "/pro/customers/1/vehicles/1/repairs/1",
            "/pro/customers/1/vehicles/1/invoices/new",
        ]
        for path in paths:
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assert_spanish_payload(response.text)

        customer_page = client.get("/pro/customers/1/vehicles/1")
        self.assertIn("Sam Driver", customer_page.text)
        self.assertIn("Honda", customer_page.text)
        self.assertIn("Customer reported a rattle.", customer_page.text)

    def test_project_page_sample_ships_localized_payload_for_supported_languages(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.seed_workflow_records()
        paths = [
            "/account/settings",
            "/pro/dashboard",
            "/pro/customers",
            "/pro/customers/1",
            "/pro/customers/1/vehicles/1",
            "/pro/customers/1/vehicles/1/repairs/1",
            "/pro/customers/1/vehicles/1/invoices/new",
            "/pro/approvals",
            "/pro/calendar",
            "/pro/shop-settings",
            "/estimator",
            "/quick-find",
            "/parts-center",
            "/repair-guides",
            "/repair-cost",
            "/login",
            "/signup",
            "/forgot-password",
        ]

        for language in ("es", "vi", "zh-Hans"):
            self.set_language(language)
            client.cookies.set("tm_language_preference", language)
            client.cookies.set("tm_appearance_preference", "system")
            for path in paths:
                with self.subTest(language=language, path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assert_locale_payload(response.text, language)
                    self.assertIn("static/i18n.js", response.text)
                    self.assertIn("data-theme=", response.text)

    def test_english_pages_keep_default_payload(self):
        client = self.client()
        self.bootstrap_owner(client)

        response = client.get("/pro/customers")

        self.assertEqual(response.status_code, 200)
        self.assertIn('<html lang="en"', response.text)
        self.assertIn('dir="ltr"', response.text)
        self.assertIn('data-language="en"', response.text)
        self.assertIn('"language": "en"', response.text)
        self.assertIn('"exactText": {}', response.text)
        self.assertIn("Customers", response.text)

    def test_public_customer_estimate_page_has_phase2_opt_out_class(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.set_language("es")

        token = pro_module.create_customer_estimate_review_token(
            {"id": 999, "customer_id": 1, "vehicle_id": 1},
            shop_id=1,
        )
        response = client.get(f"/customer/estimate/{token}")

        self.assertEqual(response.status_code, 404)
        self.assertIn("tm-public-estimate", response.text)
        self.assertIn("static/i18n.js", response.text)

    def test_estimator_spanish_payload_covers_modal_and_dynamic_statuses(self):
        payload = client_payload("es")
        translations = payload["translations"]

        self.assertEqual(translations["estimator.modal.create_customer_quote"], "Crear cotizacion para cliente")
        self.assertEqual(translations["estimator.modal.business_identity"], "Identidad del negocio")
        self.assertEqual(translations["estimator.modal.customer_message_placeholder"], "El mensaje al cliente aparecera aqui...")
        self.assertEqual(translations["estimator.status.signature_empty"], "La firma esta seleccionada, pero el cuadro de firma esta vacio. Pide al cliente que firme o elige la opcion de PDF sin firma.")
        self.assertIn("{count}", translations["estimator.status.saved_count"])
        self.assertIn("{url}", translations["estimator.modal.pdf_ready_html"])

    def test_estimator_modal_markup_uses_stable_translation_keys(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.set_language("es")

        response = client.get("/estimator")

        self.assertEqual(response.status_code, 200)
        self.assert_spanish_payload(response.text)
        self.assertIn('id="confirmModal"', response.text)
        self.assertIn('role="dialog"', response.text)
        self.assertIn('aria-modal="true"', response.text)
        self.assertIn('data-i18n="estimator.modal.create_customer_quote"', response.text)
        self.assertIn('data-i18n="estimator.modal.business_identity"', response.text)
        self.assertIn('data-i18n-placeholder="estimator.modal.customer_message_placeholder"', response.text)
        self.assertIn('data-i18n="estimator.modal.clear_signature"', response.text)
        self.assertIn('data-i18n="estimator.quantity"', response.text)

    def test_estimator_javascript_uses_translation_helpers_for_modal_regressions(self):
        app_js = (Path(main.BASE_DIR) / "static" / "app.js").read_text(encoding="utf-8")
        i18n_js = (Path(main.BASE_DIR) / "static" / "i18n.js").read_text(encoding="utf-8")

        self.assertIn('tmEstimatorText("estimator.status.signature_empty"', app_js)
        self.assertIn('tmEstimatorText("estimator.modal.prepared_summary"', app_js)
        self.assertIn('tmEstimatorText("estimator.modal.quote_message_copied"', app_js)
        self.assertIn('tmEstimatorText("estimator.status.review_saved_before_pdf"', app_js)
        self.assertIn('window.tmI18n?.apply(confirmModal)', app_js)
        self.assertIn('confirmModal.classList.add("is-open")', app_js)
        self.assertIn('confirmModal?.classList.remove("is-open")', app_js)
        self.assertIn('document.body.classList.remove("modal-open")', app_js)
        self.assertIn('[data-i18n-data-ready-label]', i18n_js)

    def test_estimator_modal_css_keeps_visible_centered_and_hidden_cleanly(self):
        style_css = (Path(main.BASE_DIR) / "static" / "style.css").read_text(encoding="utf-8")

        self.assertIn("body.tm-estimator-page #confirmModal.modal", style_css)
        self.assertIn("place-items: center", style_css)
        self.assertIn("body.tm-estimator-page #confirmModal.modal.hidden", style_css)
        self.assertIn("display: none !important", style_css)
        self.assertIn("max-height: calc(100dvh - 20px)", style_css)
