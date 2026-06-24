import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class ProAccessGateTests(unittest.TestCase):
    def test_public_pro_routes_are_blocked_when_pro_is_not_enabled(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")

            pro_response = client.get("/pro")
            customers_response = client.get("/pro/customers")

        self.assertEqual(pro_response.status_code, 403)
        self.assertIn("TorqueMech Pro is in private development.", pro_response.text)
        self.assertEqual(customers_response.status_code, 403)
        self.assertIn("TorqueMech Pro is in private development.", customers_response.text)

    def test_public_homepage_does_not_link_to_pro(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('href="/pro', response.text)
        self.assertNotIn("Pro Dashboard", response.text)

    def test_localhost_bypasses_gate_for_development(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": ""}):
            client = TestClient(main.app, base_url="http://localhost")
            response = client.get("/pro")

        self.assertNotEqual(response.status_code, 403)

    def test_access_code_unlocks_public_pro_access(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "shop-test"}):
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


if __name__ == "__main__":
    unittest.main()
