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

    def test_convert_to_pro_job_renders_after_customer_quote_actions(self):
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
        drafts_idx = html.index('id="draftsCard"')
        drafts_end_idx = html.index('id="customerQuoteFinalActions"')

        self.assertLess(saved_idx, final_idx)
        self.assertLess(final_idx, handoff_idx)
        self.assertLess(handoff_idx, convert_idx)
        self.assertNotIn('id="convertToProJobBtn"', html[drafts_idx:drafts_end_idx])

    def test_estimator_quantity_controls_and_line_item_display_are_present(self):
        response = TestClient(main.app, base_url="http://localhost").get("/estimator")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="serviceQuantity"', response.text)
        self.assertIn('id="serviceQuantityClearBtn"', response.text)
        self.assertIn('aria-label="Clear quantity"', response.text)
        self.assertIn("How should labor be calculated?", response.text)
        self.assertIn("Use entered labor as total job labor", response.text)
        self.assertIn("Multiply labor by quantity", response.text)
        self.assertIn("Parts Cost (optional)", response.text)
        self.assertIn("Use quantity for coils, plugs, injectors, tires, or per-side parts.", response.text)
        self.assertIn("Most jobs should use total job labor. Only multiply labor when the same labor time repeats for each item.", response.text)
        self.assertIn("Labor will not multiply. The labor hours entered are for the full job.", response.text)
        self.assertIn("Labor hours stay editable. Adjust total labor for the full job.", response.text)
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()
        self.assertIn("Parts Cost Per Item (optional)", app_js)
        self.assertIn("displayServiceNameWithQuantity", app_js)
        self.assertIn("partsUnitCost", app_js)
        self.assertIn("getPartsTotal(it)", app_js)
        self.assertIn("laborCalculationMode", app_js)
        self.assertIn("getBillableLaborHours", app_js)
        self.assertIn("Labor hours will multiply by quantity.", app_js)
        self.assertIn("Parts total", app_js)
        self.assertIn("showLaborCalculation = quantity > 1", app_js)
        self.assertIn("laborCalculationWrapEl.hidden = !showLaborCalculation", app_js)
        self.assertIn("serviceQuantityClearBtn.hidden", app_js)
        self.assertIn('serviceQuantityEl.value = "";', app_js)
        self.assertIn('laborCalculationModeEl.value = "total"', app_js)
        self.assertIn("normalizeQuantity(serviceQuantityEl?.value)", app_js)

    def test_finding_estimator_shows_parts_sources_before_price_job(self):
        response = TestClient(main.app, base_url="http://localhost").get(
            "/estimator?source=finding&customer_id=1&vehicle_id=2&finding_id=3"
            "&year=2008&make=Toyota&model=Sequoia&recommended_repair=Water+Pump+Replacement"
        )

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Parts Sources", html)
        self.assertIn("Research Parts Pricing", html)
        self.assertIn(
            "Use these source links to research parts pricing before entering Parts Cost. Confirm fitment on the vendor site before ordering.",
            html,
        )
        self.assertIn("Amazon", html)
        self.assertIn("O&#39;Reilly Catalog Search", html)
        self.assertIn("2008+Toyota+Sequoia+water+pump", html)
        self.assertLess(html.index("Research Parts Pricing"), html.index("Price Job"))

    def test_finding_estimator_parts_sources_include_service_keyword(self):
        response = TestClient(main.app, base_url="http://localhost").get(
            "/estimator?source=finding&customer_id=1&vehicle_id=2&finding_id=3"
            "&year=2002&make=Ford&model=F-150"
            "&service_name=Rear+Brake+Pads+Replacement"
            "&recommended_repair=Brake+Concern"
        )

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Rear Brake Pads Replacement", html)
        self.assertIn("2002+Ford+F-150+rear+brake+pads", html)
        self.assertIn("site%3Aoreillyauto.com+2002+Ford+F-150+rear+brake+pads", html)
        self.assertNotIn("site%3Aoreillyauto.com+2002+Ford+F-150%22", html)

    def test_parts_sources_api_prioritizes_selected_service_keyword(self):
        response = TestClient(main.app, base_url="http://localhost").get(
            "/api/parts-sources?year=2002&make=Ford&model=F-150"
            "&service_name=Rear+Brake+Pads+Replacement"
            "&recommended_repair=Brake+Concern"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["service_keyword"], "Rear Brake Pads Replacement")
        by_label = {source["source_label"]: source for source in payload["sources"]}
        self.assertEqual(by_label["Amazon"]["query"], "2002 Ford F-150 rear brake pads")
        self.assertIn("2002+Ford+F-150+rear+brake+pads", by_label["eBay"]["url"])
        self.assertIn("site%3Aoreillyauto.com+2002+Ford+F-150+rear+brake+pads", by_label["O'Reilly"]["url"])

    def test_estimator_parts_sources_refreshes_from_current_service_dom_paths(self):
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        self.assertIn("function getEstimatorPartsSourceServiceText()", app_js)
        self.assertIn('const typedService = String(serviceSearch?.value || "").trim();', app_js)
        self.assertIn('if (selectedService) params.set("service_name", selectedService);', app_js)
        self.assertIn("apiJSON(`/api/parts-sources?${params.toString()}`)", app_js)
        self.assertIn("scheduleEstimatorPartsSourcesRefresh();", app_js)
        self.assertGreaterEqual(app_js.count("void refreshEstimatorPartsSources();"), 7)

    def test_parts_sources_api_falls_back_to_recommended_repair_when_service_cleared(self):
        response = TestClient(main.app, base_url="http://localhost").get(
            "/api/parts-sources?year=2002&make=Ford&model=F-150"
            "&recommended_repair=Rear+Brake+Pads+Replacement"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["service_keyword"], "Rear Brake Pads Replacement")
        by_label = {source["source_label"]: source for source in payload["sources"]}
        self.assertEqual(by_label["Amazon"]["query"], "2002 Ford F-150 rear brake pads")

    def test_plain_estimator_does_not_show_parts_sources_card(self):
        response = TestClient(main.app, base_url="http://localhost").get("/estimator")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Research Parts Pricing", response.text)

    def test_pdf_generation_accepts_quantity_line_item(self):
        client = TestClient(main.app, base_url="http://localhost")

        response = client.post(
            "/estimate/pdf_multi",
            json={
                "year": 2016,
                "make": "Honda",
                "model": "Accord",
                "lineItems": [
                    {
                        "serviceCode": "ignition_coil_replacement",
                        "serviceText": "Ignition Coil Replacement (each)",
                        "displayServiceText": "Ignition Coil Replacement (each) × 4",
                        "quantity": 4,
                        "partsUnitCost": 45,
                        "pricingMode": "hourly",
                        "laborHoursInput": 1,
                        "laborCalculationMode": "per_item",
                        "laborHours": 4,
                        "partsPrice": 180,
                        "laborRate": 90,
                        "travelFee": 0,
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_finding_pdf_generation_saves_estimate_timeline_document(self):
        client = TestClient(main.app, base_url="http://localhost")

        with patch.object(main, "record_estimate_pdf_document", return_value={"id": 77}) as save_mock:
            response = client.post(
                "/estimate/pdf_multi",
                json={
                    "year": 2008,
                    "make": "Toyota",
                    "model": "Sequoia",
                    "customerName": "Sam Driver",
                    "source": "finding",
                    "customerId": "5",
                    "vehicleId": "8",
                    "findingId": "13",
                    "recommendedRepair": "Water Pump Replacement",
                    "customerAgrees": True,
                    "lineItems": [
                        {
                            "serviceCode": "water_pump_replacement",
                            "serviceText": "Water Pump Replacement",
                            "displayServiceText": "Water Pump Replacement",
                            "quantity": 1,
                            "partsUnitCost": 325,
                            "pricingMode": "hourly",
                            "laborHoursInput": 3,
                            "laborCalculationMode": "total",
                            "laborHours": 3,
                            "partsPrice": 325,
                            "laborRate": 125,
                            "travelFee": 0,
                            "estimate": 700,
                        }
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        save_mock.assert_called_once()
        kwargs = save_mock.call_args.kwargs
        self.assertTrue(kwargs["pdf_bytes"].startswith(b"%PDF"))
        self.assertEqual(kwargs["customer_id"], "5")
        self.assertEqual(kwargs["vehicle_id"], "8")
        self.assertEqual(kwargs["finding_id"], "13")
        self.assertEqual(kwargs["customer_name"], "Sam Driver")
        self.assertEqual(kwargs["vehicle_label"], "2008 Toyota Sequoia")
        self.assertEqual(kwargs["related_title"], "Water Pump Replacement")
        self.assertEqual(kwargs["estimate_total"], 700)
        self.assertEqual(kwargs["approval_status"], "Customer reviewed estimate")


if __name__ == "__main__":
    unittest.main()
