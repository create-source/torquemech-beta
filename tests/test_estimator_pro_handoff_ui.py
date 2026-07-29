import os
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


class EstimatorProHandoffUiTests(unittest.TestCase):
    def start_finding_estimator_context(self):
        conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
        conn.row_factory = sqlite3.Row
        pro_module.ensure_customer_status_schema(conn)
        pro_module.ensure_findings_records_schema(conn)
        conn.execute(
            """
            INSERT INTO customers (
              id, shop_id, first_name, last_name, phone, email,
              customer_status, notes, created_at, updated_at
            )
            VALUES (1, 9, 'Sam', 'Driver', '', '', 'active', '',
                    '2026-07-24T12:00:00', '2026-07-24T12:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO customer_vehicles (
              id, shop_id, customer_id, year, make, model, created_at, updated_at
            )
            VALUES (2, 9, 1, 2008, 'Toyota', 'Sequoia',
                    '2026-07-24T12:00:00', '2026-07-24T12:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO findings_records (
              id, customer_id, vehicle_id, request_type, finding, recommendation,
              severity, status, created_at
            )
            VALUES (3, 1, 2, 'finding', 'Water pump leak',
                    'Water Pump Replacement', 'High', 'Open', '2026-07-24T12:00:00')
            """
        )
        conn.commit()
        self.addCleanup(conn.close_for_cleanup)
        patches = (
            patch.object(main, "app_db_conn", return_value=conn),
            patch.object(main, "current_user", return_value={"id": 4, "first_name": "Tech"}),
            patch.object(main, "current_shop_context", return_value={"id": 9, "shop_name": "Alpha Shop"}),
            patch.object(pro_module, "crm_db_conn", return_value=conn),
            patch.object(pro_module, "required_current_shop_id", return_value=9),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

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
        self.assertIn('id="proJobHandoffActions"', unlocked_response.text)
        self.assertIn('id="convertToProJobMount"', unlocked_response.text)
        self.assertNotIn('id="convertToProJobBtn"', unlocked_response.text)
        self.assertIn(main.PRO_QA_ACCESS_COOKIE, unlocked_response.cookies)
        self.assertNotIn("qa-secret", unlocked_response.text)
        self.assertNotIn("qa-secret", unlocked_response.headers.get("set-cookie", ""))
        self.assertEqual(persisted_response.status_code, 200)
        self.assertIn('id="convertToProJobMount"', persisted_response.text)
        self.assertNotIn('id="convertToProJobBtn"', persisted_response.text)

    def test_convert_to_pro_job_is_not_rendered_in_initial_estimator_html(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": ""}):
            client = TestClient(main.app, base_url="http://localhost")
            response = client.get("/estimator")

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('id="estimateSavedBlock"', html)
        self.assertIn('id="customerQuoteFinalActions"', html)
        self.assertIn('id="proJobHandoffActions"', html)
        self.assertIn('id="convertToProJobMount"', html)
        self.assertIn('class="tm-pro-job-handoff"', html)
        self.assertIn('aria-label="Pro job handoff"', html)
        self.assertNotIn('id="convertToProJobBtn"', html)
        self.assertNotIn(">Convert to Pro Job<", html)

        saved_idx = html.index('id="estimateSavedBlock"')
        handoff_idx = html.index('id="proJobHandoffActions"')
        final_idx = html.index('id="customerQuoteFinalActions"')
        drafts_idx = html.index('id="draftsCard"')
        drafts_end_idx = html.index('id="customerQuoteFinalActions"')

        self.assertLess(saved_idx, final_idx)
        self.assertLess(handoff_idx, final_idx)
        self.assertNotIn('id="convertToProJobBtn"', html[drafts_idx:drafts_end_idx])

    def test_convert_to_pro_job_is_created_after_quote_generation(self):
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        self.assertIn("function showEstimateSavedBlock(d)", app_js)
        self.assertIn("showProJobHandoffActions();", app_js)
        self.assertIn("function ensureConvertToProJobButton()", app_js)
        self.assertIn('button.id = "convertToProJobBtn";', app_js)
        self.assertIn('button.textContent = convertToProJobMount.dataset.readyLabel || "Convert to Pro Job";', app_js)
        self.assertIn("button.addEventListener(\"click\", handleConvertToProJob);", app_js)
        self.assertIn("customerQuoteReadyForProJob", app_js)
        self.assertIn("function validateCustomerQuoteReview()", app_js)
        self.assertIn("prepareReviewedEstimateBtn?.addEventListener", app_js)

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
        self.start_finding_estimator_context()
        response = TestClient(main.app, base_url="http://localhost").get(
            "/estimator?source=finding&customer_id=1&vehicle_id=2&finding_id=3"
            "&year=2008&make=Toyota&model=Sequoia&recommended_repair=Water+Pump+Replacement"
        )

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Pro finding workflow", html)
        self.assertIn('href="/pro/customers/1/vehicles/2/findings/3"', html)
        self.assertIn('href="/pro/customers/1/vehicles/2#recommendations-findings"', html)
        self.assertIn('data-finding-url="/pro/customers/1/vehicles/2/findings/3"', html)
        self.assertIn('data-finding-prepared-url="/pro/customers/1/vehicles/2/findings/3?estimate_prepared=1"', html)
        self.assertIn('data-finding-vehicle-url="/pro/customers/1/vehicles/2#recommendations-findings"', html)
        self.assertIn('data-finding-handoff-url="/pro/estimator/finding-handoff?customer_id=1&amp;vehicle_id=2&amp;finding_id=3"', html)
        self.assertIn('href="/pro/dashboard"', html)
        self.assertIn("Command Center", html)
        self.assertIn("Loading customer and vehicle...", html)
        self.assertNotIn("Choose the customer vehicle first.", html)
        self.assertNotIn(">Log In<", html)
        self.assertNotIn(">Sign Up<", html)
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
        self.start_finding_estimator_context()
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
        self.assertIn('const selectedService = getSelectedServiceDisplayName();', app_js)
        self.assertIn('if (selectedService) return selectedService;', app_js)
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
        self.assertNotIn("Back to Finding", response.text)
        self.assertNotIn("Back to Vehicle", response.text)
        self.assertNotIn("data-finding-prepared-url", response.text)
        self.assertNotIn("data-finding-handoff-url", response.text)
        self.assertIn("Choose the customer vehicle first.", response.text)
        self.assertNotIn("Loading customer and vehicle...", response.text)
        self.assertIn("Prepare Reviewed Estimate", response.text)
        self.assertIn('id="prepareReviewedEstimateBtn"', response.text)

    def test_finding_estimator_handoff_endpoint_returns_exact_vehicle(self):
        self.start_finding_estimator_context()
        response = TestClient(main.app, base_url="http://localhost").get(
            "/pro/estimator/finding-handoff?customer_id=1&vehicle_id=2&finding_id=3"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "finding")
        self.assertEqual(payload["customer"]["id"], 1)
        self.assertEqual(payload["customer"]["name"], "Sam Driver")
        self.assertEqual(payload["vehicle"]["id"], 2)
        self.assertEqual(payload["vehicle"]["customer_id"], 1)
        self.assertEqual(payload["vehicle"]["year"], 2008)
        self.assertEqual(payload["vehicle"]["make"], "Toyota")
        self.assertEqual(payload["vehicle"]["model"], "Sequoia")
        self.assertEqual(payload["finding"]["id"], 3)
        self.assertEqual(payload["finding"]["problemFound"], "Water pump leak")
        self.assertEqual(payload["finding"]["recommendedRepair"], "Water Pump Replacement")

    def test_finding_estimator_handoff_endpoint_enforces_shop_scope(self):
        self.start_finding_estimator_context()
        with patch.object(pro_module, "required_current_shop_id", return_value=10):
            response = TestClient(main.app, base_url="http://localhost").get(
                "/pro/estimator/finding-handoff?customer_id=1&vehicle_id=2&finding_id=3"
            )

        self.assertEqual(response.status_code, 404)

    def test_finding_estimator_hides_stage_two_completion_actions(self):
        self.start_finding_estimator_context()
        response = TestClient(main.app, base_url="http://localhost").get(
            "/estimator?source=finding&customer_id=1&vehicle_id=2&finding_id=3"
            "&year=2008&make=Toyota&model=Sequoia&recommended_repair=Water+Pump+Replacement"
        )

        self.assertEqual(response.status_code, 200)
        html = response.text
        button_labels = re.findall(r"<button[^>]*>(.*?)</button>", html, flags=re.S)
        button_labels = [re.sub(r"\s+", " ", label).strip() for label in button_labels]

        self.assertEqual(button_labels.count("Save Prepared Estimate"), 1)
        self.assertNotIn("Save as Prepared Estimate", html)
        self.assertIn("Review & Save Prepared Estimate", html)
        self.assertIn('id="generateAllBtn"', html)
        self.assertNotIn('id="prepareReviewedEstimateBtn"', html)
        self.assertIn('id="confirmAddBtn"', html)
        self.assertIn('id="copyCustomerMessageBtn"', html)
        self.assertIn('class="customer-message-heading-row"', html)
        self.assertIn("Return to Finding", html)
        self.assertIn(">Back to Finding</a>", html)
        self.assertIn(">Back to Vehicle</a>", html)
        self.assertIn(">Close</button>", html)
        self.assertIn('href="/pro/customers/1/vehicles/2/findings/3"', html)
        self.assertIn('href="/pro/customers/1/vehicles/2#recommendations-findings"', html)
        self.assertNotIn("Copy Estimate Link", html)
        self.assertNotIn("Open Customer Quote", html)
        self.assertNotIn("Email Customer Quote", html)
        self.assertNotIn("Create Repair Order", html)

    def test_finding_prepared_estimate_buttons_have_unique_ids_and_matching_js_bindings(self):
        self.start_finding_estimator_context()
        response = TestClient(main.app, base_url="http://localhost").get(
            "/estimator?source=finding&customer_id=1&vehicle_id=2&finding_id=3"
            "&year=2008&make=Toyota&model=Sequoia&recommended_repair=Water+Pump+Replacement"
        )
        html = response.text
        ids = re.findall(r'id="([^"]+)"', html)

        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn('button id="generateAllBtn" type="button"', html)
        self.assertNotIn('button id="prepareReviewedEstimateBtn" type="button"', html)
        self.assertIn('button id="confirmAddBtn" type="button"', html)

        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        self.assertIn('generateAllBtn?.addEventListener("click"', app_js)
        self.assertIn('prepareReviewedEstimateBtn?.addEventListener("click"', app_js)
        self.assertIn('copyCustomerMessageBtn?.addEventListener("click"', app_js)
        self.assertIn('navigator.clipboard?.writeText', app_js)
        self.assertIn('document.execCommand("copy")', app_js)
        self.assertIn('quotePreviewEl?.value || ""', app_js)
        self.assertIn('copyCustomerMessageBtn.textContent = "Copied";', app_js)
        self.assertIn('const trigger = e.target?.closest?.("#confirmAddBtn");', app_js)
        self.assertIn('if (openConfirm() && isFindingEstimatorSession()) {', app_js)

    def test_finding_prepared_estimate_save_skips_hidden_signature_gate(self):
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        self.assertIn("if (isFindingEstimatorSession()) {", app_js)
        self.assertIn("signatureDataUrl = null;", app_js)
        self.assertIn("if (customerAgreesChk) customerAgreesChk.checked = false;", app_js)
        self.assertIn("return true;", app_js)
        self.assertLess(
            app_js.index("if (isFindingEstimatorSession()) {", app_js.index("function validateCustomerQuoteReview()")),
            app_js.index('const wantSig = getWantSig();', app_js.index("function validateCustomerQuoteReview()")),
        )

    def test_finding_prepared_estimate_redirect_only_after_successful_pdf_save(self):
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        success_idx = app_js.index('if (isFindingSave) {')
        redirect_idx = app_js.index("window.location.assign(returnUrl);", success_idx)
        catch_idx = app_js.index("} catch (e) {", success_idx)

        self.assertLess(success_idx, redirect_idx)
        self.assertLess(redirect_idx, catch_idx)
        self.assertIn("if (!contentType.includes(\"application/pdf\"))", app_js)
        self.assertIn("if (!pdfResponse.ok)", app_js)
        self.assertIn("setConfirmMessage(\"error\", \"Unable to generate PDF. Please try again.\");", app_js)

    def test_finding_completion_redirect_logic_is_present(self):
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        self.assertIn("function isFindingEstimatorSession()", app_js)
        self.assertIn("function findingEstimatorReturnUrl()", app_js)
        self.assertIn("function hydrateFindingEstimatorHandoff()", app_js)
        self.assertIn("function applyFindingHandoffPayload(payload = {})", app_js)
        self.assertIn("apiJSON(url)", app_js)
        self.assertIn("/pro/estimator/finding-handoff?", app_js)
        self.assertIn("Loading customer and vehicle...", app_js)
        self.assertIn("Retry", app_js)
        self.assertNotIn("preloadServiceCatalog", app_js)
        self.assertNotIn('ensureAllServiceOptions("")', app_js)
        self.assertIn("const hasDirectFindingVehicle = findingContext.source === \"finding\" && !!findingContext.vehicleId;", app_js)
        self.assertIn("const vehicleLoaded = hasDirectFindingVehicle ? true : await preloadVehicle();", app_js)
        self.assertIn("if (!findingVehicleHydrated) {\n        await applyObdFromQuery();\n      }", app_js)
        self.assertIn("window.location.assign(returnUrl);", app_js)
        self.assertIn("findingPreparedUrl", app_js)
        self.assertIn('"estimate_prepared", "1"', app_js)
        self.assertIn('if (isFindingEstimatorSession()) {', app_js)

    def test_finding_hydration_does_not_start_global_service_search(self):
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        apply_idx = app_js.index("function applyFindingHandoffPayload(payload = {})")
        hydrate_idx = app_js.index("async function hydrateFindingEstimatorHandoff()", apply_idx)
        safe_read_idx = app_js.index("function safeReadStorage(storage, key)", hydrate_idx)
        hydration_block = app_js[apply_idx:safe_read_idx]

        self.assertIn("notesEl.value = `Recommended Repair: ${recommendedRepair}`;", hydration_block)
        self.assertIn("clearTimeout(globalServiceSearchTimer);", hydration_block)
        self.assertNotIn("scheduleGlobalServiceSearch(", hydration_block)
        self.assertNotIn("serviceSearch.dispatchEvent", hydration_block)
        self.assertNotIn("new Event(\"input\"", hydration_block)

    def test_service_search_uses_bounded_endpoint_not_all_category_timer(self):
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        self.assertNotIn("function scheduleFullServiceCatalogSearch", app_js)
        self.assertNotIn("ensureAllServiceOptions", app_js)
        self.assertNotIn("Promise.all(\n        serviceCategories.map", app_js)
        self.assertIn("function scheduleGlobalServiceSearch(searchValue, delayMs = 320)", app_js)
        self.assertIn("normalizedValue.length < 2", app_js)
        self.assertIn("await searchServiceOptions(normalizedValue, { limit: 40 })", app_js)
        self.assertIn("/api/services/search?q=", app_js)
        self.assertIn("requestId !== globalServiceSearchRequestId", app_js)
        self.assertIn("globalServiceSearchQuery = normalizeServiceSearch(normalizedValue);", app_js)

    def test_category_service_loading_remains_category_scoped_and_cached(self):
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        get_services_idx = app_js.index("async function getServicesForCategory(categoryKey)")
        load_services_idx = app_js.index("async function loadServices(categoryKey)", get_services_idx)
        category_block = app_js[get_services_idx:load_services_idx]

        self.assertIn("if (serviceCategoryCache.has(key))", category_block)
        self.assertIn("return serviceCategoryCache.get(key);", category_block)
        self.assertIn("apiJSON(`/api/services/${encodeURIComponent(key)}`)", category_block)
        self.assertNotIn("/api/services/search", category_block)

        load_block = app_js[load_services_idx:app_js.index("async function loadServiceMeta", load_services_idx)]
        self.assertIn("const rawServices = await getServicesForCategory(categoryKey);", load_block)
        self.assertIn('serviceCatalogLoadingHint.textContent = categoryKey ? "Loading services..." : "";', load_block)
        self.assertIn("serviceCatalogLoadingHint.hidden = true;", load_block)

    def test_service_selection_still_loads_metadata_and_updates_pricing(self):
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        change_idx = app_js.index('serviceEl?.addEventListener("change", async () => {')
        change_block = app_js[change_idx:app_js.index("function clearGlobalServiceSearchLoading", change_idx)]

        self.assertIn("syncServiceSearchFromSelect();", change_block)
        self.assertIn("await loadServiceMeta(serviceEl.value);", change_block)
        self.assertIn("updateQuantityPricingPreview();", change_block)
        self.assertIn("updateEstimateButtonState();", change_block)

    def test_finding_startup_renders_vehicle_before_optional_catalog_and_selectors(self):
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        init_idx = app_js.index("const initReady = (async () => {")
        hydrate_idx = app_js.index("const findingVehicleHydrated = await hydrateFindingEstimatorHandoff();", init_idx)
        ready_idx = app_js.index('setStatus("info", "Finding vehicle loaded. Estimator ready.");', hydrate_idx)
        selector_task_idx = app_js.index('console.warn("Finding vehicle selector initialization failed:", error);', ready_idx)
        selector_idx = app_js.index("await renderVehicles();", ready_idx)
        categories_task_idx = app_js.index('console.warn("Finding category initialization failed:", error);', selector_idx)
        categories_idx = app_js.index("await loadCategories();", selector_idx)
        return_idx = app_js.index("return;", selector_idx)
        standalone_makes_idx = app_js.index("await loadMakes();", return_idx)
        finding_branch = app_js[ready_idx:return_idx]

        self.assertLess(hydrate_idx, ready_idx)
        self.assertLess(ready_idx, selector_idx)
        self.assertLess(selector_idx, selector_task_idx)
        self.assertLess(selector_task_idx, categories_idx)
        self.assertLess(categories_idx, categories_task_idx)
        self.assertLess(selector_idx, return_idx)
        self.assertLess(return_idx, standalone_makes_idx)
        self.assertNotIn("await loadCategories();\n\n        void (async () => {\n          try {\n            await renderVehicles();", finding_branch)
        self.assertIn("renderFindingHandoffSummary(payload);", app_js)
        self.assertIn('data-finding-handoff-summary="true"', app_js)

    def test_finding_vehicle_display_does_not_require_make_or_model_apis(self):
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        apply_idx = app_js.index("function applyFindingHandoffPayload(payload = {})")
        summary_idx = app_js.index("renderFindingHandoffSummary(payload);", apply_idx)
        banner_idx = app_js.index("renderActiveVehicleBanner();", summary_idx)
        selector_idx = app_js.index("await renderVehicles();", banner_idx)

        self.assertLess(summary_idx, selector_idx)
        self.assertLess(banner_idx, selector_idx)
        self.assertIn("let validSelectedModel = models.includes(selectedModel) ? selectedModel : \"\";", app_js)
        self.assertIn("if (!validSelectedModel && selectedModel) {", app_js)
        self.assertIn("validSelectedModel = selectedModel;", app_js)

    def test_service_catalog_is_lazy_loaded_and_cached(self):
        with open("static/app.js", encoding="utf-8") as handle:
            app_js = handle.read()

        self.assertIn("const serviceCategoryCache = new Map();", app_js)
        self.assertIn("const serviceCategoryRequests = new Map();", app_js)
        self.assertIn("if (serviceCategoryCache.has(key))", app_js)
        self.assertIn("serviceCategoryCache.set(key, normalized);", app_js)
        self.assertIn('categoryEl?.addEventListener("change", async () => {', app_js)
        self.assertIn("await loadServices(categoryEl.value);", app_js)
        self.assertIn('serviceCatalogLoadingHint.hidden = !categoryKey;', app_js)
        init_block = app_js[
            app_js.index("const initReady = (async () => {"):
            app_js.index("initReady.then(() => loadSharedEstimateFromPath());")
        ]
        self.assertNotIn("ensureAllServiceOptions", init_block)

    def test_stage_one_visual_hooks_are_present(self):
        with open("templates/pro/finding_detail.html", encoding="utf-8") as handle:
            finding_html = handle.read()
        with open("templates/pro/vehicle_detail.html", encoding="utf-8") as handle:
            vehicle_html = handle.read()
        with open("static/style.css", encoding="utf-8") as handle:
            style_css = handle.read()

        self.assertIn(".tm-finding-panel {", finding_html)
        self.assertIn("color:#0f172a;", finding_html)
        self.assertIn("color:#334155;", finding_html)
        self.assertIn("Customer Review Link", finding_html)
        self.assertIn("Secure customer link", finding_html)
        self.assertIn("tm-customer-review-link__lock", finding_html)
        self.assertIn("tm-customer-review-link__preview", finding_html)
        self.assertIn("&bull;&bull;&bull;&bull;", finding_html)
        self.assertIn("Secure customer review link ready to share.", finding_html)
        self.assertIn("View Customer Review", finding_html)
        self.assertIn("Copy Customer Link", finding_html)
        self.assertIn("data-copy-customer-review-link", finding_html)
        self.assertIn("data-copy-customer-review-status", finding_html)
        self.assertIn("Customer link copied.", finding_html)
        self.assertIn("Unable to copy the link. Please try again.", finding_html)
        self.assertIn("}, 2000);", finding_html)
        self.assertNotIn('button.textContent = "Copied";', finding_html)
        self.assertNotIn('<code id="customerReviewUrl"', finding_html)
        self.assertNotIn("View/Edit Repair Estimate", finding_html)
        self.assertEqual(finding_html.count(">Finding History<"), 1)
        self.assertNotIn("Customer Decision / Update Status", vehicle_html)
        self.assertNotIn("Customer Decision / Approval Status", vehicle_html)
        self.assertIn("Estimate prepared", vehicle_html)
        self.assertIn("View/Edit Repair Estimate", vehicle_html)
        self.assertIn(".tm-estimator-parts-source-group__title", style_css)
        self.assertIn("color:#cbd5e1;", style_css)

    def test_stage_two_public_review_template_customer_safe_ui_hooks(self):
        with open("templates/customer_estimate_review.html", encoding="utf-8") as handle:
            public_review_html = handle.read()

        self.assertIn("{% block site_nav %}{% endblock %}", public_review_html)
        self.assertIn(".tm-public-estimate-heading", public_review_html)
        self.assertIn("background:#050e1b;", public_review_html)
        self.assertIn("background:var(--tm-panel)", public_review_html)
        self.assertIn("color:#f8fafc !important;", public_review_html)
        self.assertIn("border-left:4px solid var(--tm-orange);", public_review_html)
        self.assertIn("<span>Service</span>", public_review_html)
        self.assertIn("<span>Parts</span>", public_review_html)
        self.assertIn("<span>Labor</span>", public_review_html)
        self.assertIn("<span>Fees</span>", public_review_html)
        self.assertIn("<span>Tax</span>", public_review_html)
        self.assertIn("<span>Line Total</span>", public_review_html)
        self.assertIn("line_items[0].service_name or estimate.related_title or finding.recommendation", public_review_html)
        self.assertIn("object-fit:contain;", public_review_html)
        self.assertNotIn("Mark Customer Approved", public_review_html)
        self.assertNotIn("Mark Customer Declined", public_review_html)
        self.assertNotIn("Start Repair", public_review_html)
        self.assertNotIn("Edit Finding", public_review_html)
        self.assertNotIn("Internal Notes", public_review_html)
        self.assertNotIn("profit_margin", public_review_html.lower())
        self.assertNotIn("markup", public_review_html.lower())

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
        conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close_for_cleanup)
        pro_module.ensure_customer_status_schema(conn)
        pro_module.ensure_findings_records_schema(conn)
        conn.execute(
            """
            INSERT INTO customers (
              id, shop_id, first_name, last_name, phone, email,
              customer_status, notes, created_at, updated_at
            )
            VALUES (5, 1, 'Sam', 'Driver', '', '', 'active', '',
                    '2026-07-24T12:00:00', '2026-07-24T12:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO customer_vehicles (
              id, shop_id, customer_id, year, make, model, created_at, updated_at
            )
            VALUES (8, 1, 5, 2008, 'Toyota', 'Sequoia',
                    '2026-07-24T12:00:00', '2026-07-24T12:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO findings_records (
              id, customer_id, vehicle_id, request_type, finding, recommendation,
              severity, status, created_at
            )
            VALUES (13, 5, 8, 'finding', 'Water pump leak',
                    'Water Pump Replacement', 'High', 'Open', '2026-07-24T12:00:00')
            """
        )
        conn.commit()

        with (
            patch.object(main, "app_db_conn", return_value=conn),
            patch.object(main, "current_user", return_value={"id": 1}),
            patch.object(main, "current_shop_context", return_value={"id": 1}),
            patch.object(main, "shop_subscription_access_context", return_value={"can_write": True}),
            patch.object(main, "metric_incr", return_value=None),
            patch.object(main, "record_estimate_pdf_document", return_value={"id": 77}) as save_mock,
        ):
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
        self.assertEqual(kwargs["approval_status"], "Prepared estimate")


if __name__ == "__main__":
    unittest.main()
