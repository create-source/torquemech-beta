import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


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
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": "qa-secret"}):
            client = TestClient(main.app, base_url="https://torquemech.com")

            locked_response = client.get("/pro")
            bad_key_response = client.get("/pro?qa_key=wrong")
            good_key_response = client.get("/pro?qa_key=qa-secret")
            persisted_response = client.get("/pro/customers")

        self.assertEqual(locked_response.status_code, 403)
        self.assertIn("TorqueMech Pro is in private development.", locked_response.text)
        self.assertEqual(bad_key_response.status_code, 403)
        self.assertIn("TorqueMech Pro is in private development.", bad_key_response.text)
        self.assertNotEqual(good_key_response.status_code, 403)
        self.assertIn(main.PRO_QA_ACCESS_COOKIE, good_key_response.cookies)
        self.assertNotIn("qa-secret", good_key_response.text)
        self.assertNotIn("qa-secret", good_key_response.headers.get("set-cookie", ""))
        self.assertNotEqual(persisted_response.status_code, 403)

    def test_qa_key_cookie_does_not_store_raw_key(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": "qa-secret"}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            response = client.get("/pro?qa_key=qa-secret")

        cookie_value = response.cookies.get(main.PRO_QA_ACCESS_COOKIE)
        self.assertTrue(cookie_value)
        self.assertNotEqual(cookie_value, "qa-secret")


if __name__ == "__main__":
    unittest.main()
