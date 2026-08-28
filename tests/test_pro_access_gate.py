import os
import json
import re
import sqlite3
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
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


def auth_test_conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
    conn.row_factory = sqlite3.Row
    pro_module.ensure_auth_schema(conn)
    pro_module.ensure_shop_profile_schema(conn)
    pro_module.ensure_shop_subscription_schema(conn)
    now = "2026-07-12T00:00:00"
    conn.execute(
        """
        INSERT INTO users (
          email, password_hash, first_name, last_name, is_active,
          email_verified_at, created_at, updated_at
        )
        VALUES ('owner@example.com', ?, 'Test', 'Owner', 1, ?, ?, ?)
        """,
        (pro_module.hash_password("correct-password"), now, now, now),
    )
    user_id = int(conn.execute("SELECT id FROM users WHERE email = 'owner@example.com'").fetchone()["id"])
    pro_module.create_shop_profile_for_user(conn, user_id, "Access Test Shop")
    conn.commit()
    return conn, user_id


def insert_subscription(conn, shop_id: int, status: str, **fields):
    values = {
        "trial_started_at": None,
        "trial_ends_at": None,
        "current_period_started_at": None,
        "current_period_ends_at": None,
        "cancel_at_period_end": 0,
        "canceled_at": None,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "stripe_price_id": None,
        **fields,
    }
    now = "2026-07-12T00:00:00"
    conn.execute(
        """
        INSERT INTO shop_subscriptions (
          shop_id, plan_code, status, trial_started_at, trial_ends_at,
          current_period_started_at, current_period_ends_at, cancel_at_period_end, canceled_at,
          access_grace_ends_at, stripe_customer_id, stripe_subscription_id,
          stripe_price_id, created_at, updated_at
        )
        VALUES (?, 'pro_solo', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
        """,
        (
            shop_id,
            status,
            values["trial_started_at"],
            values["trial_ends_at"],
            values["current_period_started_at"],
            values["current_period_ends_at"],
            values["cancel_at_period_end"],
            values["canceled_at"],
            values["stripe_customer_id"],
            values["stripe_subscription_id"],
            values["stripe_price_id"],
            now,
            now,
        ),
    )
    conn.commit()


def authenticated_client(conn, user_id, base_url="https://torquemech.com"):
    now = "2026-07-12T00:00:00"
    session_id = f"access-test-session-{user_id}"
    conn.execute(
        """
        INSERT INTO auth_sessions (session_id, data_json, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, json.dumps({pro_module.AUTH_SESSION_USER_KEY: user_id}), now, now),
    )
    conn.commit()
    client = TestClient(main.app, base_url=base_url)
    client.cookies.set(main.SESSION_COOKIE_NAME, session_id)
    return client


def assert_shared_home_navigation(testcase, html: str, expected_href: str):
    testcase.assertIn(f'<a class="tm-brand" href="{expected_href}" aria-label="TorqueMech Home">', html)
    testcase.assertIn(f'<a class="tm-menu__item" href="{expected_href}" data-i18n="nav.home">Home</a>', html)


def assert_marketing_home_logo(testcase, html: str, expected_href: str):
    testcase.assertRegex(html, rf'<a class="tm-pro-brand" href="{re.escape(expected_href)}" aria-label="TorqueMech home">')


class ProAccessGateTests(unittest.TestCase):
    def test_public_pro_routes_are_blocked_when_pro_is_not_enabled(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")

            pro_response = client.get("/pro")
            customers_response = client.get("/pro/customers")

        self.assertEqual(pro_response.status_code, 403)
        self.assertIn("TorqueMech Pro is in private development.", pro_response.text)
        self.assertEqual(customers_response.status_code, 403)
        self.assertIn("TorqueMech Pro is in private development.", customers_response.text)

    def test_public_homepage_does_not_link_to_pro(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('href="/pro', response.text)
        self.assertNotIn("Pro Dashboard", response.text)

    def test_localhost_bypasses_gate_for_development(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="http://localhost")
            response = client.get("/pro")

        self.assertNotEqual(response.status_code, 403)

    def test_access_code_unlocks_public_pro_access(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "shop-test", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")

            locked_response = client.get("/pro")
            bad_code_response = client.post("/pro", data={"pro_access_code": "wrong"})
            good_code_response = client.post("/pro", data={"pro_access_code": "shop-test"}, follow_redirects=False)
            unlocked_response = client.get("/pro")

        self.assertEqual(locked_response.status_code, 403)
        self.assertIn("Access code", locked_response.text)
        self.assertEqual(bad_code_response.status_code, 403)
        self.assertEqual(good_code_response.status_code, 303)
        self.assertNotEqual(unlocked_response.status_code, 403)

    def test_qa_key_unlocks_public_pro_access_and_sets_cookie(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": "qa-secret"}):
            client = TestClient(main.app, base_url="https://torquemech.com")

            locked_response = client.get("/pro")
            bad_key_response = client.get("/pro?qa_key=wrong")
            good_key_response = client.get("/pro?qa_key=qa-secret", follow_redirects=False)
            persisted_response = client.get("/pro/customers", follow_redirects=False)

        self.assertEqual(locked_response.status_code, 403)
        self.assertIn("TorqueMech Pro is in private development.", locked_response.text)
        self.assertEqual(bad_key_response.status_code, 403)
        self.assertIn("TorqueMech Pro is in private development.", bad_key_response.text)
        self.assertEqual(good_key_response.status_code, 303)
        self.assertEqual(good_key_response.headers["location"], "/login?next=%2Fpro%3Fqa_key%3Dqa-secret")
        self.assertIn(main.PRO_QA_ACCESS_COOKIE, good_key_response.cookies)
        self.assertNotIn("qa-secret", good_key_response.text)
        self.assertNotIn("qa-secret", good_key_response.headers.get("set-cookie", ""))
        self.assertEqual(persisted_response.status_code, 303)
        self.assertEqual(persisted_response.headers["location"], "/login?next=%2Fpro%2Fcustomers")

    def test_qa_key_cookie_does_not_store_raw_key(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": "qa-secret"}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            response = client.get("/pro?qa_key=qa-secret", follow_redirects=False)

        cookie_value = response.cookies.get(main.PRO_QA_ACCESS_COOKIE)
        self.assertTrue(cookie_value)
        self.assertNotEqual(cookie_value, "qa-secret")

    def test_qa_gate_logs_only_boolean_diagnostics(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": "qa-secret"}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            with self.assertLogs("torquemech.pro_gate", level="WARNING") as logs:
                response = client.get("/pro?qa_key=qa-secret", follow_redirects=False)

        joined_logs = "\n".join(logs.output)
        self.assertNotEqual(response.status_code, 403)
        self.assertIn("pro_qa_key_present=True", joined_logs)
        self.assertIn("qa_key_param_present=True", joined_logs)
        self.assertIn("qa_key_matched=True", joined_logs)
        self.assertIn("access_allowed=True", joined_logs)
        self.assertNotIn("qa-secret", joined_logs)

    def test_unauthenticated_user_is_redirected_to_login_with_safe_next(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "true", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            response = client.get("/pro/calendar", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login?next=%2Fpro%2Fcalendar")

    def test_authenticated_user_can_access_pro_routes(self):
        conn, user_id = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "true", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = authenticated_client(conn, user_id)
            response = client.get("/pro/shop-settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Access Test Shop", response.text)

    def test_subscribed_user_logo_and_menu_home_link_to_dashboard(self):
        conn, user_id = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        shop_id = int(conn.execute("SELECT id FROM shop_profile WHERE owner_user_id = ?", (user_id,)).fetchone()["id"])
        insert_subscription(conn, shop_id, "active", current_period_ends_at="2026-08-12T00:00:00+00:00")
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "true", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = authenticated_client(conn, user_id)
            response = client.get("/")
            estimator_response = client.get("/estimator")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(estimator_response.status_code, 200)
        assert_marketing_home_logo(self, response.text, "/pro/dashboard")
        assert_shared_home_navigation(self, estimator_response.text, "/pro/dashboard")

    def test_free_trial_user_logo_and_menu_home_link_to_dashboard(self):
        conn, user_id = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        shop_id = int(conn.execute("SELECT id FROM shop_profile WHERE owner_user_id = ?", (user_id,)).fetchone()["id"])
        insert_subscription(
            conn,
            shop_id,
            "trialing",
            trial_started_at="2026-07-12T00:00:00+00:00",
            trial_ends_at="2026-07-26T00:00:00+00:00",
        )
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "true", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = authenticated_client(conn, user_id)
            response = client.get("/")
            estimator_response = client.get("/estimator")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(estimator_response.status_code, 200)
        assert_marketing_home_logo(self, response.text, "/pro/dashboard")
        assert_shared_home_navigation(self, estimator_response.text, "/pro/dashboard")

    def test_logged_out_visitor_logo_and_menu_home_link_to_public_home(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "true", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            response = client.get("/")
            estimator_response = client.get("/estimator")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(estimator_response.status_code, 200)
        assert_marketing_home_logo(self, response.text, "/")
        assert_shared_home_navigation(self, estimator_response.text, "/")

    def test_login_next_accepts_safe_url_and_rejects_external_url(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "true", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            safe_page = client.get("/login?next=/pro/calendar")
            safe_response = client.post(
                "/login",
                data={
                    "csrf_token": csrf_from(safe_page.text),
                    "email": "owner@example.com",
                    "password": "correct-password",
                    "next": "/pro/calendar",
                },
                follow_redirects=False,
            )
            client = TestClient(main.app, base_url="https://torquemech.com")
            unsafe_page = client.get("/login?next=https://evil.example/pro")
            unsafe_response = client.post(
                "/login",
                data={
                    "csrf_token": csrf_from(unsafe_page.text),
                    "email": "owner@example.com",
                    "password": "correct-password",
                    "next": "https://evil.example/pro",
                },
                follow_redirects=False,
            )

        self.assertEqual(safe_response.status_code, 303)
        self.assertEqual(safe_response.headers["location"], "/pro/calendar")
        self.assertEqual(unsafe_response.status_code, 303)
        self.assertEqual(unsafe_response.headers["location"], "/pro/dashboard")

    def test_public_booking_route_remains_public(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "true", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            booking_response = client.get("/book/access-test-shop")
            approval_response = client.get("/pro/customers/1/vehicles/1/approvals/1", follow_redirects=False)

        self.assertEqual(booking_response.status_code, 200)
        self.assertIn("Schedule service", booking_response.text)
        self.assertEqual(approval_response.status_code, 303)
        self.assertEqual(
            approval_response.headers["location"],
            "/login?next=%2Fpro%2Fcustomers%2F1%2Fvehicles%2F1%2Fapprovals%2F1",
        )

    def test_demo_shop_index_is_public_without_login(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            response = TestClient(main.app, base_url="https://torquemech.com").get("/pro/demo")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Demo workspace — changes cannot be saved.", response.text)

    def test_demo_shop_header_and_signup_links_are_public_ctas(self):
        response = TestClient(main.app, base_url="https://torquemech.com").get("/pro/demo")

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/login">Log In</a>', response.text)
        self.assertIn('href="/signup">Sign Up</a>', response.text)
        self.assertIn('href="/signup">Create My Shop</a>', response.text)
        self.assertIn('href="/signup">Start 14-Day Free Trial</a>', response.text)
        self.assertNotIn("In a real shop account, these records link directly", response.text)

    def test_demo_shop_detail_toolbar_is_compact_and_one_row(self):
        response = TestClient(main.app, base_url="https://torquemech.com").get(
            "/pro/demo/2018-honda-accord-front-brake-service"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="tm-demo-detail-toolbar"', response.text)
        self.assertIn('class="tm-demo-detail-page__notice"', response.text)
        self.assertIn("flex: 1 1 auto", response.text)
        self.assertIn("tm-demo-detail-trial", response.text)
        self.assertIn("flex: 0 0 142px", response.text)
        self.assertIn("flex-basis: 130px", response.text)
        toolbar_css = response.text.split(".tm-demo-detail-toolbar {", 1)[1].split("}", 1)[0]
        self.assertNotIn("flex-direction: column", toolbar_css)

    def test_demo_shop_footer_is_contained_and_responsive(self):
        response = TestClient(main.app, base_url="https://torquemech.com").get("/pro/demo")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="tm-demo-footer tm-demo-wrap"', response.text)
        self.assertIn("tm-demo-footer__row--primary", response.text)
        self.assertIn("tm-demo-footer__row--support", response.text)
        self.assertIn("tm-demo-footer__links", response.text)
        self.assertIn("tm-demo-footer__support", response.text)
        self.assertIn("overflow-wrap: anywhere", response.text)
        self.assertIn("body > .tm-footer", response.text)

    def test_demo_shop_rows_are_clickable(self):
        response = TestClient(main.app, base_url="https://torquemech.com").get("/pro/demo")

        self.assertEqual(response.status_code, 200)
        for slug in (
            "2018-honda-accord-front-brake-service",
            "2016-ford-f-150-cooling-system-concern",
            "2020-toyota-camry-alternator-replacement",
            "2014-chevrolet-silverado-misfire-diagnosis",
            "appointment-maria-lopez",
            "appointment-daniel-kim",
            "appointment-jordan-reed",
            "deferred-tire-replacement",
            "oil-service-due-soon",
            "control-arm-estimate-follow-up",
        ):
            self.assertIn(f'class="tm-demo-item" href="/pro/demo/{slug}"', response.text)

    def test_demo_shop_detail_routes_are_focused_and_public_without_login(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            index_response = client.get("/pro/demo")
            detail_paths = sorted(set(re.findall(r'href="(/pro/demo/[^".]+)"', index_response.text)))
            detail_responses = {path: client.get(path) for path in detail_paths}

        self.assertEqual(len(detail_paths), 10)
        for path, response in detail_responses.items():
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("Demo only", response.text)
            self.assertIn("changes cannot be saved.", response.text)
            self.assertIn("Back to Demo", response.text)
            self.assertIn("Start Free Trial", response.text)
            self.assertNotIn("Welcome back", response.text)
            self.assertNotIn("Create My Shop", response.text)
            self.assertNotIn("Command Center", response.text)
            self.assertNotIn("Open repairs", response.text)
            self.assertNotIn("Follow-up opportunities", response.text)
            self.assertNotIn("Start 14-Day Free Trial", response.text)
        self.assertIn("2018 Honda Accord - Front brake service", detail_responses["/pro/demo/2018-honda-accord-front-brake-service"].text)
        self.assertIn("Brake pedal pulse and front-end squeal", detail_responses["/pro/demo/2018-honda-accord-front-brake-service"].text)
        self.assertIn("$642.80", detail_responses["/pro/demo/2018-honda-accord-front-brake-service"].text)
        self.assertNotIn("2016 Ford F-150 - Cooling-system concern", detail_responses["/pro/demo/2018-honda-accord-front-brake-service"].text)
        self.assertNotIn("Maria Lopez", detail_responses["/pro/demo/2018-honda-accord-front-brake-service"].text)
        self.assertIn("2016 Ford F-150 - Cooling-system concern", detail_responses["/pro/demo/2016-ford-f-150-cooling-system-concern"].text)
        self.assertIn("2020 Toyota Camry - Alternator replacement", detail_responses["/pro/demo/2020-toyota-camry-alternator-replacement"].text)
        self.assertIn("2014 Chevrolet Silverado - Misfire diagnosis", detail_responses["/pro/demo/2014-chevrolet-silverado-misfire-diagnosis"].text)
        self.assertIn("Date/time", detail_responses["/pro/demo/appointment-maria-lopez"].text)
        self.assertIn("Today, 9:00 AM", detail_responses["/pro/demo/appointment-maria-lopez"].text)
        self.assertNotIn("Daniel Kim", detail_responses["/pro/demo/appointment-maria-lopez"].text)
        self.assertIn("Deferred tire replacement", detail_responses["/pro/demo/deferred-tire-replacement"].text)
        self.assertIn("Suggested next step", detail_responses["/pro/demo/deferred-tire-replacement"].text)
        self.assertNotIn("Oil service due soon", detail_responses["/pro/demo/deferred-tire-replacement"].text)
        self.assertIn("Oil service due soon", detail_responses["/pro/demo/oil-service-due-soon"].text)
        self.assertIn("Control arm estimate", detail_responses["/pro/demo/control-arm-estimate-follow-up"].text)

    def test_demo_shop_repair_actions_match_workflow_stage(self):
        client = TestClient(main.app, base_url="https://torquemech.com")
        honda = client.get("/pro/demo/2018-honda-accord-front-brake-service").text
        ford = client.get("/pro/demo/2016-ford-f-150-cooling-system-concern").text
        toyota = client.get("/pro/demo/2020-toyota-camry-alternator-replacement").text
        chevrolet = client.get("/pro/demo/2014-chevrolet-silverado-misfire-diagnosis").text

        self.assertIn("View Approved Estimate", honda)
        self.assertIn("Open Repair Handoff Preview", honda)
        self.assertIn("/pro/demo/2018-honda-accord-front-brake-service/approved-estimate.pdf", honda)
        self.assertIn("/pro/demo/2018-honda-accord-front-brake-service/repair-handoff", honda)
        self.assertNotIn("Generate Estimate PDF", honda)
        self.assertNotIn("Generate Final Invoice PDF", honda)
        self.assertIn("Generate Estimate PDF", ford)
        self.assertNotIn("Generate Final Invoice PDF", ford)
        self.assertNotIn("View Approved Estimate", ford)
        self.assertIn("Generate Final Invoice PDF", toyota)
        self.assertNotIn("Generate Estimate PDF", toyota)
        self.assertIn("Open Repair Workspace Preview", chevrolet)
        self.assertNotIn("Generate Estimate PDF", chevrolet)
        self.assertNotIn("Generate Final Invoice PDF", chevrolet)

    def test_demo_shop_detail_actions_have_no_hash_only_links(self):
        client = TestClient(main.app, base_url="https://torquemech.com")
        for path in (
            "/pro/demo/2018-honda-accord-front-brake-service",
            "/pro/demo/2016-ford-f-150-cooling-system-concern",
            "/pro/demo/2020-toyota-camry-alternator-replacement",
            "/pro/demo/2014-chevrolet-silverado-misfire-diagnosis",
            "/pro/demo/appointment-maria-lopez",
            "/pro/demo/appointment-daniel-kim",
            "/pro/demo/appointment-jordan-reed",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            actions = re.findall(r'<a class="tm-demo-btn tm-demo-btn--[^"]+" href="([^"]+)">', response.text)
            self.assertTrue(actions, path)
            for href in actions:
                if href == "/pro/demo" or href == "/signup":
                    continue
                self.assertFalse(href.startswith("#"), f"{path} has nonfunctional action {href}")

    def test_demo_shop_preview_actions_return_meaningful_content(self):
        client = TestClient(main.app, base_url="https://torquemech.com")

        handoff = client.get("/pro/demo/2018-honda-accord-front-brake-service/repair-handoff")
        maria = client.get("/pro/demo/appointment-maria-lopez/appointment-summary")
        unknown = client.get("/pro/demo/2018-honda-accord-front-brake-service/not-real")

        self.assertEqual(handoff.status_code, 200)
        self.assertIn("2018 Honda Accord - Repair handoff preview", handoff.text)
        self.assertIn("Pads, rotors, and hardware staged", handoff.text)
        self.assertEqual(maria.status_code, 200)
        self.assertIn("Maria Lopez - Confirmed appointment", maria.text)
        self.assertIn("Customer waiting for inspection result", maria.text)
        self.assertEqual(unknown.status_code, 404)

    def test_demo_shop_sample_pdf_routes_are_public_pro_style_and_in_memory(self):
        client = TestClient(main.app, base_url="https://torquemech.com")

        honda_pdf = client.get("/pro/demo/2018-honda-accord-front-brake-service/approved-estimate.pdf")
        ford_pdf = client.get("/pro/demo/2016-ford-f-150-cooling-system-concern/estimate.pdf")
        toyota_pdf = client.get("/pro/demo/2020-toyota-camry-alternator-replacement/invoice.pdf")

        self.assertEqual(honda_pdf.status_code, 200)
        self.assertEqual(honda_pdf.headers["content-type"], "application/pdf")
        self.assertGreater(len(honda_pdf.content), 100)
        self.assertIn(b"Generated with TorqueMech", honda_pdf.content)
        self.assertIn(b"SAMPLE", honda_pdf.content)
        self.assertEqual(ford_pdf.status_code, 200)
        self.assertEqual(ford_pdf.headers["content-type"], "application/pdf")
        self.assertGreater(len(ford_pdf.content), 100)
        self.assertIn(b"Generated with TorqueMech", ford_pdf.content)
        self.assertIn(b"Estimate Totals", ford_pdf.content)
        self.assertIn(b"SAMPLE", ford_pdf.content)
        self.assertIn(b"Sample document - not a real estimate.", ford_pdf.content)
        self.assertEqual(toyota_pdf.status_code, 200)
        self.assertEqual(toyota_pdf.headers["content-type"], "application/pdf")
        self.assertGreater(len(toyota_pdf.content), 100)
        self.assertIn(b"Generated with TorqueMech", toyota_pdf.content)
        self.assertIn(b"Invoice Totals", toyota_pdf.content)
        self.assertIn(b"SAMPLE", toyota_pdf.content)
        self.assertIn(b"Sample document - not a real invoice.", toyota_pdf.content)
        for path in (
            "/pro/demo/2018-honda-accord-front-brake-service/approved-estimate.pdf",
            "/pro/demo/2016-ford-f-150-cooling-system-concern/estimate.pdf",
            "/pro/demo/2020-toyota-camry-alternator-replacement/invoice.pdf",
        ):
            for method in ("post", "put", "patch", "delete"):
                response = getattr(client, method)(path)
                self.assertEqual(response.status_code, 405, f"{method.upper()} {path}")

    def test_demo_shop_pages_expose_no_write_forms_or_write_routes(self):
        client = TestClient(main.app, base_url="https://torquemech.com")
        paths = [
            "/pro/demo",
            "/pro/demo/2018-honda-accord-front-brake-service",
            "/pro/demo/appointment-maria-lopez",
            "/pro/demo/deferred-tire-replacement",
        ]

        for path in paths:
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            demo_markup = response.text.split('<section class="tm-demo">', 1)[1].split("<footer", 1)[0].lower()
            self.assertNotIn("<form", demo_markup)
            self.assertNotRegex(response.text.lower(), r'method=["\'](?:post|put|patch|delete)["\']')
            for method in ("post", "put", "patch", "delete"):
                write_response = getattr(client, method)(path)
                self.assertEqual(write_response.status_code, 405, f"{method.upper()} {path}")

        demo_routes = [route for route in main.app.routes if getattr(route, "path", "").startswith("/pro/demo")]
        self.assertTrue(demo_routes)
        for route in demo_routes:
            self.assertTrue(set(route.methods or set()).isdisjoint({"POST", "PUT", "PATCH", "DELETE"}), route.path)


if __name__ == "__main__":
    unittest.main()
