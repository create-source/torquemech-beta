import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class EstimatorProHandoffUiTests(unittest.TestCase):
    def test_production_estimator_hides_convert_without_pro_access(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": "qa-secret"}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            response = client.get("/estimator")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="proJobHandoffActions"', response.text)
        self.assertNotIn('id="convertToProJobBtn"', response.text)

    def test_production_estimator_shows_convert_with_qa_key_and_cookie(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": "qa-secret"}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            unlocked_response = client.get("/estimator?qa_key=qa-secret")
            persisted_response = client.get("/estimator")

        self.assertEqual(unlocked_response.status_code, 200)
        self.assertIn('id="convertToProJobBtn"', unlocked_response.text)
        self.assertIn(main.PRO_QA_ACCESS_COOKIE, unlocked_response.cookies)
        self.assertNotIn("qa-secret", unlocked_response.text)
        self.assertNotIn("qa-secret", unlocked_response.headers.get("set-cookie", ""))
        self.assertEqual(persisted_response.status_code, 200)
        self.assertIn('id="convertToProJobBtn"', persisted_response.text)

    def test_convert_to_pro_job_is_not_inside_hidden_customer_quote_actions(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": ""}):
            client = TestClient(main.app, base_url="http://localhost")
            response = client.get("/estimator")

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('id="estimateSavedBlock"', html)
        self.assertIn('id="customerQuoteFinalActions"', html)
        self.assertIn('id="proJobHandoffActions"', html)
        self.assertIn('id="convertToProJobBtn"', html)

        saved_idx = html.index('id="estimateSavedBlock"')
        handoff_idx = html.index('id="proJobHandoffActions"')
        final_idx = html.index('id="customerQuoteFinalActions"')
        convert_idx = html.index('id="convertToProJobBtn"')

        self.assertLess(saved_idx, handoff_idx)
        self.assertLess(handoff_idx, final_idx)
        self.assertLess(handoff_idx, convert_idx)
        self.assertLess(convert_idx, final_idx)


if __name__ == "__main__":
    unittest.main()
