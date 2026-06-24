import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class EstimatorProHandoffUiTests(unittest.TestCase):
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
