from pathlib import Path
import asyncio
from datetime import date
import json
import sqlite3
import unittest
from unittest.mock import patch

import routers.pro as pro_module


ROOT = Path(__file__).resolve().parents[1]


class NoCloseConnection(sqlite3.Connection):
    def close(self):
        pass


class FakeRequest:
    headers = {"x-requested-with": "fetch"}

    async def body(self):
        return b"message=old%20message"


class RepairWorkspaceCleanupTests(unittest.TestCase):
    def reminder_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        pro_module.ensure_maintenance_records_schema(conn)
        pro_module.ensure_maintenance_reminder_events_schema(conn)
        return conn

    def base_reminder_record(self):
        return {
            "id": 1,
            "customer_id": 1,
            "vehicle_id": 1,
            "service_type": "Oil Change",
            "maintenance_status_key": "overdue",
            "maintenance_status": "Overdue",
        }

    def test_maintenance_reminder_event_creation_and_copy(self):
        conn = self.reminder_conn()

        drafted_id = pro_module.create_maintenance_reminder_event(
            conn,
            customer_id=1,
            vehicle_id=1,
            maintenance_record_id=1,
            service_type="Oil Change",
            status="drafted",
            method="manual",
            message="Draft reminder",
            created_at="2026-07-03T12:00:00",
        )
        copied_id = pro_module.create_maintenance_reminder_event(
            conn,
            customer_id=1,
            vehicle_id=1,
            maintenance_record_id=1,
            service_type="Oil Change",
            status="copied",
            method="manual",
            message="Copied reminder",
            created_at="2026-07-03T12:01:00",
        )
        conn.commit()

        self.assertNotEqual(drafted_id, copied_id)
        events = pro_module.load_maintenance_reminder_events_map(conn, {1})[1]
        self.assertEqual(events[0]["status"], "copied")
        self.assertEqual(events[0]["method"], "manual")
        self.assertEqual(events[0]["message"], "Copied reminder")

    def test_copied_reminder_event_stores_final_generated_message(self):
        conn = sqlite3.connect(":memory:", factory=NoCloseConnection)
        conn.row_factory = sqlite3.Row
        pro_module.ensure_maintenance_records_schema(conn)
        pro_module.ensure_maintenance_reminder_events_schema(conn)
        conn.execute(
            """
            INSERT INTO maintenance_records (
              id, customer_id, vehicle_id, service_type, date_performed, mileage_performed,
              interval_miles, interval_months, created_at, updated_at
            )
            VALUES (1, 1, 2, 'Oil Change', '2026-01-03', 177000, 5000, 6, '2026-07-03', '2026-07-03')
            """
        )
        conn.commit()

        with (
            patch.object(pro_module, "crm_db_conn", return_value=conn),
            patch.object(
                pro_module,
                "load_customer_vehicle",
                return_value=(
                    {"id": 1, "first_name": "Natalie"},
                    {"id": 2, "year": 2008, "make": "TOYOTA", "model": "SEQUOIA", "mileage": 183777},
                ),
            ),
            patch.object(
                pro_module,
                "load_shop_profile_context",
                return_value={
                    "shop_name": "Bryan from TorqueMech Auto",
                    "scheduling_link": "https://book.example.com/torquemech",
                },
            ),
            patch.object(pro_module, "local_today", return_value=date(2026, 7, 4)),
        ):
            asyncio.run(pro_module.pro_maintenance_reminder_event_action(FakeRequest(), 1, 2, 1, "copy"))

        events = pro_module.load_maintenance_reminder_events_map(conn, {1})[1]
        self.assertEqual(events[0]["status"], "copied")
        self.assertIn("this is Bryan from TorqueMech Auto", events[0]["message"])
        self.assertIn("Your 2008 TOYOTA SEQUOIA is overdue for Oil Change.", events[0]["message"])
        self.assertIn("Schedule your service here:\nhttps://book.example.com/torquemech", events[0]["message"])
        self.assertTrue(events[0]["message"].endswith("Reply here if you have any questions."))

    def test_maintenance_reminder_history_uses_automatic_events(self):
        conn = self.reminder_conn()

        pro_module.create_maintenance_reminder_event(
            conn,
            customer_id=1,
            vehicle_id=1,
            maintenance_record_id=1,
            service_type="Oil Change",
            status="drafted",
            message="Prepared reminder",
            created_at="2026-07-03T12:05:00",
        )
        conn.commit()

        record = self.base_reminder_record()
        pro_module.attach_maintenance_reminder_events(
            [record],
            pro_module.load_maintenance_reminder_events_map(conn, {1}),
        )

        self.assertEqual(record["latest_automatic_reminder_event"]["status"], "drafted")
        self.assertEqual(record["latest_automatic_reminder_event"]["message"], "Prepared reminder")

    def test_generated_reminders_remain_visible_as_waiting_follow_ups(self):
        record = self.base_reminder_record()
        record["latest_automatic_reminder_event"] = {"status": "drafted"}

        self.assertEqual(
            pro_module.maintenance_reminder_follow_up_bucket(record, date(2026, 7, 3)),
            "sent_waiting",
        )

    def test_copied_reminders_remain_visible_as_waiting_follow_ups(self):
        record = self.base_reminder_record()
        record["latest_automatic_reminder_event"] = {"status": "copied"}

        self.assertEqual(
            pro_module.maintenance_reminder_follow_up_bucket(record, date(2026, 7, 3)),
            "sent_waiting",
        )

    def test_non_latest_maintenance_record_does_not_create_active_follow_up(self):
        old_record = {**self.base_reminder_record(), "id": 1}
        old_record["is_active_maintenance_baseline"] = False

        self.assertIsNone(pro_module.maintenance_reminder_follow_up_bucket(old_record, date(2026, 7, 3)))

    def test_workspace_items_label_sources_and_estimate_totals(self):
        vehicle = {"id": 1, "year": 2016, "make": "Honda", "model": "Accord", "mileage": 120000}
        findings = [
            {
                "id": 10,
                "customer_id": 1,
                "vehicle_id": 1,
                "status": "Approved",
                "finding": "Front pads are below spec",
                "recommendation": "Replace front brake pads",
                "request_type": "finding",
                "repair_work_status": "ready",
                "created_at": "2026-06-24T10:00:00",
                "mileage": 120000,
                "labor_amount": 90,
                "parts_cost": 180,
            }
        ]
        repairs = [
            {
                "id": 20,
                "customer_id": 1,
                "vehicle_id": 1,
                "repair_name": "Replace front brake pads",
                "notes": "Source: Estimate\nCreated from estimator.",
                "status": "Open",
                "labor_hours": 1.2,
                "labor_rate": 125,
                "labor_cost": 150,
                "parts_cost": 115,
                "workflow_source_type": "estimate",
                "created_at": "2026-06-24T10:05:00",
                "repair_date": "2026-06-24",
                "mileage": 120000,
            },
            {
                "id": 21,
                "customer_id": 1,
                "vehicle_id": 1,
                "repair_name": "Rotate tires",
                "notes": "Manual counter repair.",
                "status": "Open",
                "labor_hours": 0.5,
                "labor_rate": 100,
                "labor_cost": 50,
                "parts_cost": 0,
                "workflow_source_type": "",
                "created_at": "2026-06-24T10:06:00",
                "repair_date": "2026-06-24",
                "mileage": 120000,
            },
        ]

        with patch.object(pro_module, "get_repair_blueprint_for_work_item", return_value=None):
            items = pro_module.build_repair_work_items(vehicle, findings, [], repairs)

        by_title = {item["title"]: item for item in items}
        self.assertEqual(by_title["Replace front brake pads"]["source_label"], "Source: Estimate")
        self.assertEqual(by_title["Replace front brake pads"]["repair_work_status_label"], "Ready for Repair")
        self.assertEqual(by_title["Replace front brake pads"]["labor_total"], 150)
        self.assertEqual(by_title["Replace front brake pads"]["parts_total"], 115)
        self.assertEqual(by_title["Replace front brake pads"]["grand_total"], 265)
        self.assertTrue(by_title["Replace front brake pads"]["has_pricing"])
        self.assertEqual(by_title["Replace front brake pads"]["detail"], "Created from estimator.")

        self.assertEqual(by_title["Rotate tires"]["source_label"], "Source: Manual Repair")
        self.assertEqual(by_title["Replace front brake pads"]["source_type"], "repair")

        source_labels = {item["source_label"] for item in items}
        self.assertIn("Source: Finding", source_labels)
        self.assertIn("Source: Estimate", source_labels)
        finding_item = next(item for item in items if item["source_type"] == "finding")
        self.assertEqual(finding_item["source_label"], "Source: Finding")
        self.assertEqual(finding_item["original_finding"], "Front pads are below spec")
        self.assertEqual(finding_item["labor_total"], 90)
        self.assertEqual(finding_item["parts_total"], 180)
        self.assertEqual(finding_item["grand_total"], 270)
        self.assertTrue(finding_item["has_pricing"])
        self.assertIn("O'Reilly", [source["source_label"] for source in by_title["Rotate tires"]["parts_sources"]])

    def test_workspace_card_includes_parts_sources_when_vendor_data_exists(self):
        vehicle = {"id": 1, "year": 2016, "make": "Honda", "model": "Accord", "mileage": 120000}
        repairs = [
            {
                "id": 20,
                "customer_id": 1,
                "vehicle_id": 1,
                "repair_name": "Replace front brake pads",
                "notes": "Source: Estimate",
                "status": "Open",
                "labor_hours": 1.2,
                "labor_rate": 125,
                "labor_cost": 150,
                "parts_cost": 115,
                "workflow_source_type": "estimate",
                "created_at": "2026-06-24T10:05:00",
                "repair_date": "2026-06-24",
                "mileage": None,
            }
        ]
        blueprint = {
            "title": "Front Brake Pads",
            "vendor_links": [
                {"label": "OEM/dealer catalog", "status": "VIN-confirmed source"},
                {"label": "NAPA", "url": "https://example.test/napa"},
            ],
        }

        with patch.object(pro_module, "get_repair_blueprint_for_work_item", return_value=blueprint):
            items = pro_module.build_repair_work_items(vehicle, [], [], repairs)

        self.assertEqual(items[0]["parts_sources"][0]["source_label"], "OEM/dealer catalog")
        self.assertEqual(items[0]["parts_sources"][0]["label"], "OEM Catalog Search")
        self.assertEqual(items[0]["parts_sources"][0]["note"], "VIN-confirmed source")
        self.assertIn("2016+Honda+Accord+front+brake+pads", items[0]["parts_sources"][1]["url"])
        self.assertNotIn("example.test", items[0]["parts_sources"][1]["url"])

    def test_parts_search_query_helper_uses_vehicle_engine_and_service_keywords(self):
        self.assertEqual(
            pro_module.build_parts_search_query(
                {"year": 2008, "make": "Toyota", "model": "Sequoia"},
                "Water Pump Replacement",
            ),
            "2008 Toyota Sequoia water pump",
        )
        self.assertEqual(
            pro_module.build_parts_search_query(
                {"year": 2008, "make": "TOYOTA", "model": "SEQUOIA", "engine": "5.7"},
                "water pump need replacement",
            ),
            "2008 Toyota Sequoia 5.7L water pump",
        )
        self.assertEqual(
            pro_module.build_parts_search_query(
                {"year": 2008, "make": "Toyota", "model": "Sequoia"},
                "water pump needs replacement",
            ),
            "2008 Toyota Sequoia water pump",
        )
        self.assertEqual(
            pro_module.build_parts_search_query(
                {"year": 2016, "make": "Honda", "model": "Accord"},
                "needs radiator replacement",
            ),
            "2016 Honda Accord radiator",
        )
        self.assertEqual(
            pro_module.build_parts_search_query(
                {"year": 2002, "make": "Ford", "model": "F-150"},
                "customer approved spark plug replacement",
            ),
            "2002 Ford F-150 spark plugs",
        )
        self.assertEqual(
            pro_module.build_parts_search_query(
                {"year": 2008, "make": "Toyota", "model": "Sequoia", "engine": "5.7"},
                "recommended upper radiator hose replacement",
            ),
            "2008 Toyota Sequoia 5.7L upper radiator hose",
        )

    def test_parts_sources_build_prefilled_vendor_search_links(self):
        sources = pro_module.repair_workspace_parts_sources(
            None,
            {"year": 2008, "make": "TOYOTA", "model": "SEQUOIA", "engine": "5.7"},
            "water pump need replacement",
        )
        by_label = {source["source_label"]: source for source in sources}

        self.assertEqual(by_label["RockAuto"]["query"], "2008 Toyota Sequoia 5.7L water pump")
        self.assertIn("site%3Arockauto.com+2008+Toyota+Sequoia+5.7L+water+pump", by_label["RockAuto"]["url"])
        self.assertEqual(by_label["RockAuto"]["label"], "RockAuto Catalog Search")
        self.assertEqual(by_label["OEM/dealer catalog"]["label"], "OEM Catalog Search")
        self.assertEqual(by_label["O'Reilly"]["label"], "O'Reilly Catalog Search")
        self.assertEqual(by_label["AutoZone"]["label"], "AutoZone Catalog Search")
        self.assertEqual(by_label["NAPA"]["label"], "NAPA Catalog Search")
        self.assertEqual(by_label["1A Auto"]["label"], "1A Auto Catalog Search")
        self.assertEqual(by_label["Amazon"]["search_group"], "Marketplace Search")
        self.assertEqual(by_label["eBay"]["search_group"], "Marketplace Search")
        self.assertEqual(by_label["AutoZone"]["search_group"], "Catalog Search")
        self.assertEqual(by_label["O'Reilly"]["search_group"], "Catalog Search")
        self.assertEqual(by_label["Google Shopping"]["search_group"], "Catalog Search")
        self.assertEqual(by_label["RockAuto"]["search_group"], "Catalog Search")
        self.assertEqual(by_label["NAPA"]["search_group"], "Catalog Search")
        self.assertEqual(by_label["1A Auto"]["search_group"], "Catalog Search")
        self.assertEqual(by_label["OEM/dealer catalog"]["search_group"], "Catalog Search")
        self.assertEqual(
            sorted(source["source_label"] for source in sources if source["search_group"] == "Marketplace Search"),
            ["Amazon", "eBay"],
        )
        self.assertIn("site%3Aoreillyauto.com+2008+Toyota+Sequoia+5.7L+water+pump", by_label["O'Reilly"]["url"])
        self.assertIn("google.com/search", by_label["O'Reilly"]["url"])
        self.assertNotIn("oreillyauto.com/search", by_label["O'Reilly"]["url"])
        self.assertIn("site%3Aautozone.com+2008+Toyota+Sequoia+5.7L+water+pump", by_label["AutoZone"]["url"])
        self.assertIn("google.com/search", by_label["AutoZone"]["url"])
        self.assertNotIn("autozone.com/searchresult", by_label["AutoZone"]["url"])
        self.assertIn("site%3Anapaonline.com+2008+Toyota+Sequoia+5.7L+water+pump", by_label["NAPA"]["url"])
        self.assertIn("site%3A1aauto.com+2008+Toyota+Sequoia+5.7L+water+pump", by_label["1A Auto"]["url"])
        self.assertIn("2008+Toyota+Sequoia+5.7L+water+pump", by_label["Amazon"]["url"])
        self.assertIn("amazon.com", by_label["Amazon"]["url"])
        self.assertNotIn("google.com", by_label["Amazon"]["url"])
        self.assertIn("2008+Toyota+Sequoia+5.7L+water+pump", by_label["eBay"]["url"])
        self.assertIn("ebay.com", by_label["eBay"]["url"])
        self.assertNotIn("google.com", by_label["eBay"]["url"])
        self.assertIn("2008+Toyota+Sequoia+5.7L+water+pump", by_label["Google Shopping"]["url"])
        self.assertIn("google.com/search", by_label["Google Shopping"]["url"])
        self.assertIn("tbm=shop", by_label["Google Shopping"]["url"])
        self.assertNotIn("partnum", by_label["RockAuto"]["url"])
        self.assertNotIn("/tools", by_label["AutoZone"]["url"].lower())
        self.assertNotIn("/2002-ford-f150", by_label["1A Auto"]["url"].lower())
        for source in sources:
            self.assertTrue(source["url"])
            self.assertNotIn("need", source["url"].lower())
            self.assertNotIn("fit", source["label"].lower())
            self.assertNotIn("match", source["label"].lower())
            if "google.com/search" in source["url"] and source["source_label"] != "Google Shopping":
                self.assertTrue("via Google" in source["label"] or "Search" in source["label"])
            self.assertNotEqual(source["url"].rstrip("/"), "https://www.rockauto.com")
            self.assertNotEqual(source["url"].rstrip("/"), "https://www.napaonline.com")

    def test_parts_sources_use_mapped_search_intent_for_coolant_service(self):
        sources = pro_module.repair_workspace_parts_sources(
            None,
            {"year": 2021, "make": "Kia", "model": "Forte"},
            "Coolant Drain & Refill",
        )

        self.assertEqual(sources[0]["query"], "2021 Kia Forte engine coolant")
        self.assertNotIn("coolant drain refill", sources[0]["query"].lower())

    def test_parts_sources_fall_back_to_unknown_service_label(self):
        sources = pro_module.repair_workspace_parts_sources(
            None,
            {"year": 2021, "make": "Kia", "model": "Forte"},
            "Unknown Calibration",
        )

        self.assertEqual(sources[0]["query"], "2021 Kia Forte unknown calibration")

    def test_repair_record_parts_sources_use_custom_parts_search_term(self):
        record = {"repair_name": "Radio Antenna Replacement", "parts_search_term": "radio antenna"}
        sources = pro_module.repair_workspace_parts_sources(
            None,
            {"year": 2021, "make": "Kia", "model": "Forte"},
            pro_module.repair_record_parts_search_title(record),
        )

        self.assertEqual(sources[0]["query"], "2021 Kia Forte radio antenna")

    def test_repair_record_parts_sources_fall_back_to_custom_service_name(self):
        record = {"repair_name": "Radio Antenna Replacement", "parts_search_term": ""}
        sources = pro_module.repair_workspace_parts_sources(
            None,
            {"year": 2021, "make": "Kia", "model": "Forte"},
            pro_module.repair_record_parts_search_title(record),
        )

        self.assertEqual(sources[0]["query"], "2021 Kia Forte radio antenna")

    def test_approved_finding_creates_source_finding_repair_job_with_estimate_totals(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        pro_module.ensure_findings_records_schema(conn)
        pro_module.ensure_repair_records_schema(conn)
        now = "2026-06-24T12:00:00"
        cur = conn.execute(
            """
            INSERT INTO findings_records (
              customer_id, vehicle_id, request_type, finding, recommendation,
              labor_description, labor_hours, labor_rate, labor_amount, parts_cost,
              labor_reason, severity, status, repair_work_status,
              repair_work_updated_at, mileage, finding_date, created_at
            )
            VALUES (?, ?, 'labor', ?, ?, ?, ?, ?, ?, ?, ?, 'Medium', 'Approved', '', '', ?, ?, ?)
            """,
            (
                1,
                1,
                "Rotors are worn",
                "Front Rotors Replacement",
                "Front Rotors Replacement",
                1.4,
                125,
                175,
                210,
                "Rotor thickness below spec.",
                177000,
                "2026-06-24",
                now,
            ),
        )

        repair_id = pro_module.ensure_repair_record_for_approved_finding(
            conn,
            customer_id=1,
            vehicle_id=1,
            finding_id=int(cur.lastrowid),
            now=now,
        )
        repair = dict(conn.execute("SELECT * FROM repair_records WHERE id = ?", (repair_id,)).fetchone())

        self.assertEqual(repair["repair_name"], "Front Rotors Replacement")
        self.assertEqual(repair["workflow_source_type"], "finding")
        self.assertEqual(repair["status"], "Open")
        self.assertEqual(repair["parts_cost"], 210)
        self.assertEqual(repair["labor_cost"], 175)
        self.assertEqual(repair["total_cost"], 385)

    def test_completed_repair_items_include_totals_source_and_after_photos(self):
        items = pro_module.build_completed_repair_work_items(
            [
                {
                    "id": 30,
                    "customer_id": 1,
                    "vehicle_id": 1,
                    "repair_name": "Replace alternator",
                    "status": "Completed",
                    "workflow_source_type": "estimate",
                    "completed_at": "2026-06-24T12:00:00",
                    "labor_hours": 1.5,
                    "labor_rate": 120,
                    "parts_cost": 220,
                    "completion": {"after_repair_photo_urls": ["/static/uploads/after.jpg"]},
                },
                {
                    "id": 31,
                    "customer_id": 1,
                    "vehicle_id": 1,
                    "repair_name": "Open repair",
                    "status": "Open",
                },
            ]
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_label"], "Source: Estimate")
        self.assertEqual(items[0]["repair_work_status_label"], "Completed")
        self.assertEqual(items[0]["labor_total"], 180)
        self.assertEqual(items[0]["parts_total"], 220)
        self.assertEqual(items[0]["grand_total"], 400)
        self.assertEqual(items[0]["after_repair_photo_urls"], ["/static/uploads/after.jpg"])

    def test_repair_workspace_groups_active_ready_and_invoiced_jobs(self):
        vehicle = {"id": 1, "year": 2016, "make": "Honda", "model": "Accord", "mileage": 120000}
        active = [
            {
                "source_type": "repair",
                "source_id": 20,
                "title": "Replace front brake pads",
                "repair_work_status": "ready",
            }
        ]
        repairs = [
            {
                "id": 20,
                "customer_id": 1,
                "vehicle_id": 1,
                "repair_name": "Replace front brake pads",
                "status": "Open",
                "labor_hours": 1,
                "labor_rate": 120,
                "parts_cost": 80,
            },
            {
                "id": 21,
                "customer_id": 1,
                "vehicle_id": 1,
                "repair_name": "Front rotors",
                "status": "Completed",
                "workflow_source_type": "finding",
                "workflow_source_id": 10,
                "labor_hours": 1.5,
                "labor_rate": 120,
                "parts_cost": 180,
                "is_invoiced": False,
            },
            {
                "id": 22,
                "customer_id": 1,
                "vehicle_id": 1,
                "repair_name": "Brake fluid",
                "status": "Completed",
                "workflow_source_type": "estimate",
                "labor_hours": 0.5,
                "labor_rate": 120,
                "parts_cost": 30,
                "is_invoiced": True,
                "invoice_number": "TM-INV-1003",
                "invoice_url": "/pro/customers/1/vehicles/1/invoices/3",
            },
        ]

        with patch.object(pro_module, "get_repair_blueprint_for_work_item", return_value=None):
            groups = pro_module.build_repair_workspace_groups(vehicle, active, repairs)

        self.assertEqual(groups["active"], active)
        self.assertEqual([item["title"] for item in groups["ready_for_invoice"]], ["Front rotors"])
        self.assertEqual(groups["ready_for_invoice"][0]["repair_work_status_label"], "Ready for Invoice")
        self.assertEqual(groups["ready_for_invoice"][0]["source_action_label"], "View Source Finding")
        self.assertEqual([item["title"] for item in groups["invoiced"]], ["Brake fluid"])
        self.assertEqual(groups["invoiced"][0]["invoice_number"], "TM-INV-1003")
        self.assertEqual(groups["invoiced"][0]["source_action_label"], "View Source Estimate")

    def test_repair_workspace_primary_cta_uses_saved_estimate_state(self):
        no_estimate = {
            "source_type": "finding",
            "source_label": "Source: Finding",
            "source_action_url": "/pro/customers/1/vehicles/1/findings/10",
            "create_estimate_url": "/estimator?source=finding&finding_id=10",
        }
        no_estimate_with_repair = {
            **no_estimate,
            "linked_repair_record_id": 30,
            "repair_record_url": "/pro/customers/1/vehicles/1/repairs/30",
        }
        open_estimate = {
            **no_estimate,
            "estimate_document_url": "/pro/customers/1/vehicles/1/estimates/12/pdf",
        }
        completed_invoice = {
            "repair_record_url": "/pro/customers/1/vehicles/1/repairs/30",
            "invoice_url": "/pro/customers/1/vehicles/1/invoices/9",
        }

        self.assertEqual(
            pro_module.repair_workspace_primary_action(no_estimate, "open"),
            {"label": "Create Estimate", "url": "/estimator?source=finding&finding_id=10", "kind": "link"},
        )
        self.assertEqual(
            pro_module.repair_workspace_primary_action(no_estimate_with_repair, "approved"),
            {"label": "Create Estimate", "url": "/estimator?source=finding&finding_id=10", "kind": "link"},
        )
        self.assertEqual(
            pro_module.repair_workspace_primary_action(open_estimate, "open"),
            {"label": "Open Estimate", "url": "/pro/customers/1/vehicles/1/estimates/12/pdf", "kind": "link"},
        )
        self.assertEqual(
            pro_module.repair_workspace_primary_action(open_estimate, "approved")["label"],
            "Open Repair / Track Parts",
        )
        self.assertEqual(
            pro_module.repair_workspace_primary_action(completed_invoice, "completed"),
            {"label": "Open Final Invoice", "url": "/pro/customers/1/vehicles/1/invoices/9", "kind": "link"},
        )

    def test_approved_finding_with_saved_estimate_opens_repair_not_duplicate_estimate(self):
        vehicle = {"id": 1, "year": 2008, "make": "Toyota", "model": "Sequoia", "mileage": 177000}
        findings = [
            {
                "id": 10,
                "customer_id": 1,
                "vehicle_id": 1,
                "status": "Approved",
                "finding": "Coolant leak at water pump",
                "recommendation": "Water Pump Replacement",
                "request_type": "finding",
                "repair_work_status": "ready",
                "created_at": "2026-06-24T10:00:00",
            }
        ]
        estimate_docs = [
            {
                "id": 12,
                "finding_id": 10,
                "estimate_date": "2026-06-24",
                "created_at": "2026-06-24T10:05:00",
                "approval_status": "Prepared estimate",
            }
        ]

        with patch.object(pro_module, "get_repair_blueprint_for_work_item", return_value=None):
            items = pro_module.build_repair_work_items(vehicle, findings, [], [], estimate_docs)

        self.assertEqual(items[0]["estimate_document_url"], "/pro/customers/1/vehicles/1/estimates/12/pdf")
        self.assertEqual(items[0]["estimate_badge"], "Estimate PDF")
        pro_module.enrich_repair_workspace_item(items[0])
        self.assertEqual(items[0]["primary_action_label"], "Open Repair / Track Parts")
        self.assertEqual(items[0]["primary_action_kind"], "repair")
        self.assertNotEqual(items[0]["primary_action_label"], "Create Estimate")

    def test_open_finding_with_saved_estimate_gets_open_estimate_metadata(self):
        findings = [{"id": 10, "customer_id": 1, "vehicle_id": 1, "status": "Open"}]
        pro_module.attach_estimate_documents_to_findings(
            findings,
            [
                {
                    "id": 12,
                    "finding_id": 10,
                    "estimate_date": "2026-06-24",
                    "created_at": "2026-06-24T10:05:00",
                    "approval_status": "Prepared estimate",
                    "estimate_total": 700,
                }
            ],
            customer_id=1,
            vehicle_id=1,
        )

        self.assertEqual(findings[0]["estimate_document_url"], "/pro/customers/1/vehicles/1/estimates/12/pdf")
        self.assertEqual(findings[0]["estimate_document_status"], "Prepared estimate")
        self.assertEqual(findings[0]["estimate_total"], 700)

    def test_completed_repairs_render_in_vehicle_timeline_repaired_services(self):
        timeline = pro_module.build_vehicle_timeline(
            1,
            1,
            {"id": 1},
            [
                {
                    "id": 40,
                    "source_type": "repair",
                    "source_record_id": 30,
                    "service_date": "2026-06-24",
                    "service_name": "Replace alternator",
                    "mileage": 177000,
                    "total_cost": 400,
                    "created_at": "2026-06-24T12:00:00",
                }
            ],
            [
                {
                    "id": 9,
                    "invoice_number": "TM-INV-1009",
                    "repair_record_id": 30,
                    "repair_record_ids": "30",
                    "repair_name": "Replace alternator",
                    "grand_total": 400,
                    "created_at": "2026-06-25T09:00:00",
                }
            ],
            [],
            [],
            [],
            repair_completion_events=[
                {
                    "id": 50,
                    "repair_record_id": 30,
                    "repair_name": "Replace alternator",
                    "completion_date": "2026-06-24",
                    "completed_at": "2026-06-24T13:00:00",
                    "created_at": "2026-06-24T13:00:00",
                    "workflow_source_type": "estimate",
                    "after_repair_photo_paths": json.dumps(["/static/uploads/after.jpg"]),
                    "tracked_parts": [
                        {"part_name": "Alternator", "status": "Installed", "subtotal": 225},
                    ],
                    "tracked_parts_total": 225,
                    "tracked_parts_count": 1,
                }
            ],
        )

        repaired = next(group for group in timeline if group["key"] == "repaired")
        self.assertEqual(repaired["title"], "Completed Repairs")
        self.assertEqual(repaired["records"][0]["service_name"], "Replace alternator")
        self.assertEqual(repaired["records"][0]["mileage"], 177000)
        self.assertEqual(repaired["records"][0]["source_label"], "Source: Estimate")
        self.assertEqual(repaired["records"][0]["total"], 400)
        self.assertEqual(repaired["records"][0]["invoice_number"], "TM-INV-1009")
        self.assertEqual(repaired["records"][0]["invoice_url"], "/pro/customers/1/vehicles/1/invoices/9")
        self.assertEqual(repaired["records"][0]["status_label"], "Completed")
        self.assertEqual(repaired["records"][0]["photo_count"], 1)
        self.assertEqual(repaired["records"][0]["tracked_parts_count"], 1)
        self.assertEqual(repaired["records"][0]["tracked_parts_total"], 225)
        self.assertEqual(repaired["records"][0]["tracked_parts"][0]["part_name"], "Alternator")
        self.assertEqual(repaired["records"][0]["url"], "/pro/customers/1/vehicles/1/repairs/30")
        self.assertEqual(repaired["records"][0]["action_label"], "Open Repair Record")

    def test_completion_event_timeline_fallback_includes_repair_details(self):
        timeline = pro_module.build_vehicle_timeline(
            1,
            1,
            {"id": 1},
            [],
            [],
            [],
            [],
            [],
            repair_completion_events=[
                {
                    "id": 51,
                    "repair_record_id": 31,
                    "repair_name": "Replace water pump",
                    "completion_date": "",
                    "completed_at": "2026-06-24T13:00:00",
                    "created_at": "2026-06-24T13:00:00",
                    "workflow_source_type": "finding",
                    "mileage": 177000,
                    "total_cost": 650,
                    "after_repair_photo_paths": json.dumps(["/static/uploads/after.jpg"]),
                    "tracked_parts": [
                        {"part_name": "Water Pump", "status": "Arrived", "subtotal": 180},
                    ],
                    "tracked_parts_total": 180,
                    "tracked_parts_count": 1,
                }
            ],
        )

        repaired = next(group for group in timeline if group["key"] == "repaired")["records"][0]
        self.assertEqual(repaired["service_name"], "Replace water pump")
        self.assertEqual(repaired["mileage"], 177000)
        self.assertEqual(repaired["source_label"], "Source: Finding")
        self.assertEqual(repaired["total"], 650)
        self.assertEqual(repaired["status_label"], "Completed")
        self.assertEqual(repaired["date"], "2026-06-24T13:00:00")
        self.assertEqual(repaired["tracked_parts_count"], 1)
        self.assertEqual(repaired["tracked_parts"][0]["status"], "Arrived")

    def test_timeline_does_not_put_active_work_or_checklists_in_repaired_services(self):
        timeline = pro_module.build_vehicle_timeline(
            1,
            1,
            {"id": 1},
            [],
            [],
            [
                {
                    "id": 41,
                    "customer_id": 1,
                    "vehicle_id": 1,
                    "status": "Approved",
                    "repair_work_status": "in_progress",
                    "finding": "Brake pulsation",
                    "recommendation": "Replace front rotors",
                    "mileage": 177000,
                    "finding_date": "2026-06-24",
                    "created_at": "2026-06-24T09:00:00",
                }
            ],
            [],
            [],
            repair_checklist_events=[
                {
                    "id": 88,
                    "repair_record_id": 30,
                    "task_name": "Road test",
                    "completed_at": "2026-06-24T12:00:00",
                    "created_at": "2026-06-24T11:00:00",
                }
            ],
        )

        groups = {group["key"]: group for group in timeline}
        self.assertEqual(groups["repaired"]["title"], "Completed Repairs")
        self.assertEqual(groups["repaired"]["records"], [])
        self.assertEqual(groups["findings"]["title"], "Findings")
        self.assertEqual(groups["findings"]["records"][0]["service_name"], "Brake pulsation")
        self.assertEqual(groups["findings"]["records"][0]["recommendation"], "Replace front rotors")
        self.assertEqual(groups["findings"]["records"][0]["status_label"], "Approved")
        self.assertEqual(groups["approvals"]["title"], "Approvals / Decisions")

    def test_completed_linked_finding_stays_visible_in_findings_timeline(self):
        timeline = pro_module.build_vehicle_timeline(
            1,
            1,
            {"id": 1},
            [],
            [
                {
                    "id": 9,
                    "invoice_number": "TM-INV-1009",
                    "repair_record_id": 30,
                    "repair_record_ids": "30",
                    "repair_name": "Water pump replacement",
                    "grand_total": 900,
                    "created_at": "2026-06-25T09:00:00",
                }
            ],
            [
                {
                    "id": 41,
                    "customer_id": 1,
                    "vehicle_id": 1,
                    "status": "Completed",
                    "repair_work_status": "completed",
                    "linked_repair_record_id": 30,
                    "finding": "Coolant leak",
                    "recommendation": "Replace water pump",
                    "severity": "High",
                    "mileage": 177000,
                    "finding_date": "2026-06-24",
                    "created_at": "2026-06-24T09:00:00",
                    "estimate_document_url": "/pro/customers/1/vehicles/1/estimates/12/pdf",
                    "estimate_total": 825,
                }
            ],
            [],
            [],
        )

        groups = {group["key"]: group for group in timeline}
        finding = groups["findings"]["records"][0]
        self.assertEqual(groups["findings"]["count"], 1)
        self.assertEqual(finding["service_name"], "Coolant leak")
        self.assertEqual(finding["severity"], "High")
        self.assertEqual(finding["estimate_total"], 825)
        self.assertEqual(finding["repair_url"], "/pro/customers/1/vehicles/1/repairs/30")
        self.assertEqual(finding["invoice_number"], "TM-INV-1009")

    def test_repair_estimate_documents_render_as_estimates_not_completed_repairs(self):
        timeline = pro_module.build_vehicle_timeline(
            1,
            1,
            {"id": 1},
            [],
            [
                {
                    "id": 9,
                    "invoice_number": "TM-INV-1009",
                    "repair_record_id": 30,
                    "repair_record_ids": "30",
                    "repair_name": "Water pump replacement",
                    "grand_total": 825,
                    "created_at": "2026-06-25T09:00:00",
                }
            ],
            [],
            [],
            [],
            estimate_document_records=[
                {
                    "id": 12,
                    "estimate_date": "2026-06-24",
                    "created_at": "2026-06-24T10:00:00",
                    "customer_name": "Sam Driver",
                    "vehicle_label": "2008 Toyota Sequoia",
                    "related_title": "Water pump replacement",
                    "estimate_total": 825,
                    "approval_status": "Signed customer approval",
                    "invoice_id": 9,
                    "invoice_number": "TM-INV-1009",
                }
            ],
        )

        groups = {group["key"]: group for group in timeline}
        self.assertEqual(groups["findings"]["title"], "Findings")
        self.assertEqual(groups["estimates"]["title"], "Estimates")
        self.assertEqual(groups["repaired"]["title"], "Completed Repairs")
        self.assertEqual(groups["invoices"]["title"], "Invoices")
        self.assertEqual(groups["repaired"]["records"], [])
        estimate = groups["estimates"]["records"][0]
        self.assertIn("Repair Estimate", estimate["service_name"])
        self.assertNotIn("Completed Repair", estimate["service_name"])
        self.assertEqual(estimate["customer_name"], "Sam Driver")
        self.assertEqual(estimate["vehicle_label"], "2008 Toyota Sequoia")
        self.assertEqual(estimate["related_title"], "Water pump replacement")
        self.assertEqual(estimate["total"], 825)
        self.assertEqual(estimate["approval_status"], "Signed customer approval")
        self.assertEqual(estimate["url"], "/pro/customers/1/vehicles/1/estimates/12/pdf")
        self.assertIn("/estimator?", estimate["edit_url"])
        self.assertIn("estimate_id=12", estimate["edit_url"])
        self.assertEqual(estimate["action_label"], "Open Estimate PDF")
        self.assertEqual(estimate["invoice_number"], "TM-INV-1009")
        invoice = groups["invoices"]["records"][0]
        self.assertIn("Final Invoice TM-INV-1009", invoice["service_name"])
        self.assertEqual(invoice["action_label"], "Open Final Invoice")

    def test_photo_stage_labels_are_present(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        repair_detail = (ROOT / "templates" / "pro" / "repair_detail.html").read_text(encoding="utf-8")

        self.assertIn("Before / Inspection Photos", vehicle_detail)
        self.assertIn('id="before_inspection_photos"', vehicle_detail)
        self.assertIn('type="file"', vehicle_detail)
        self.assertIn('accept="image/*"', vehicle_detail)
        self.assertIn('capture="environment"', vehicle_detail)
        self.assertIn('class="tm-photo-input-hidden"', vehicle_detail)
        self.assertIn("Add Photos", vehicle_detail)
        self.assertNotIn("Add Before Photos", vehicle_detail)
        self.assertIn("Tap Add Photos to choose Camera or Photo Library.", vehicle_detail)
        self.assertIn("Photos are optional and only saved when you attach them to this repair record.", vehicle_detail)
        self.assertIn("Up to 5 photos.", vehicle_detail)
        self.assertIn("No photos selected", vehicle_detail)
        self.assertIn("Upload photos of the original problem before repair.", vehicle_detail)
        self.assertIn("After / Completion Photos", repair_detail)
        self.assertIn("Before / Inspection Photos", repair_detail)
        self.assertIn('id="after_repair_photos"', repair_detail)
        self.assertIn('name="after_repair_photos"', repair_detail)
        self.assertIn('type="file"', repair_detail)
        self.assertIn('accept="image/*"', repair_detail)
        self.assertIn('capture="environment"', repair_detail)
        self.assertIn('class="tm-photo-input-hidden"', repair_detail)
        self.assertIn("Add Photos", repair_detail)
        self.assertNotIn("Add Completion Photos", repair_detail)
        self.assertIn("Tap Add Photos to choose Camera or Photo Library.", repair_detail)
        self.assertIn("Photos are optional and only saved when you attach them to this repair record.", repair_detail)
        self.assertIn("Up to 5 photos.", repair_detail)
        self.assertIn("No photos selected", repair_detail)
        self.assertIn('enctype="multipart/form-data"', repair_detail)
        self.assertIn("Upload photos showing the completed repair or proof of work.", repair_detail)
        self.assertIn("completion.after_repair_photo_urls", repair_detail)
        self.assertIn("Uploaded After Photos", repair_detail)
        self.assertNotIn(">Choose Files<", vehicle_detail)
        self.assertNotIn(">Choose Files<", repair_detail)

    def test_photo_upload_helpers_limit_before_and_after_photos_to_five(self):
        uploads = [
            {"filename": f"photo-{idx}.jpg", "content_type": "image/jpeg", "content": b"img"}
            for idx in range(6)
        ]

        with self.assertRaises(pro_module.HTTPException) as raised:
            pro_module.save_image_upload_paths(
                uploads,
                max_files=pro_module.PHOTO_UPLOAD_MAX_FILES,
                allowed_extensions=pro_module.PHOTO_UPLOAD_ALLOWED_EXTENSIONS,
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Upload up to 5 photos", raised.exception.detail)

    def test_photo_upload_helpers_restrict_completion_photo_types(self):
        with self.assertRaises(pro_module.HTTPException):
            pro_module.save_image_upload_paths(
                [{"filename": "photo.svg", "content_type": "image/svg+xml", "content": b"<svg></svg>"}],
                max_files=pro_module.PHOTO_UPLOAD_MAX_FILES,
                allowed_extensions=pro_module.PHOTO_UPLOAD_ALLOWED_EXTENSIONS,
            )

    def test_finding_form_uploads_before_photos_separately_from_after_photos(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        repair_detail = (ROOT / "templates" / "pro" / "repair_detail.html").read_text(encoding="utf-8")

        self.assertIn('action="/pro/customers/{{ customer.id }}/vehicles/{{ vehicle.id }}/findings" enctype="multipart/form-data"', vehicle_detail)
        self.assertIn('name="before_inspection_photos"', vehicle_detail)
        self.assertIn("item.before_inspection_photo_urls", vehicle_detail)
        self.assertNotIn('name="after_repair_photos"', vehicle_detail)
        self.assertNotIn('name="before_inspection_photos"', repair_detail)

    def test_workspace_card_does_not_repeat_source_label_in_footer(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        self.assertIn("tm-repair-source-chip", vehicle_detail)
        self.assertIn("Source Record", vehicle_detail)
        self.assertNotIn('tm-repair-work-source-label">{{ item.source_label', vehicle_detail)

    def test_repair_detail_status_open_displays_clear_workspace_status(self):
        repair_detail = (ROOT / "templates" / "pro" / "repair_detail.html").read_text(encoding="utf-8")
        form_helpers = (ROOT / "static" / "pro_form_helpers.js").read_text(encoding="utf-8")
        self.assertIn('repair_status_display = "Completed"', repair_detail)
        self.assertIn('"Approved" if execution_status == "ready" else "Open"', repair_detail)
        self.assertIn("Ready to Complete", repair_detail)
        self.assertIn("{{ repair_status_display }}", repair_detail)
        self.assertIn("repair_display_mileage", repair_detail)
        self.assertIn("Completion Date", repair_detail)
        self.assertIn('name="completion_date"', repair_detail)
        self.assertIn("Completion Mileage", repair_detail)
        self.assertIn('name="completion_mileage"', repair_detail)
        self.assertIn("data-pro-mileage-input", repair_detail)
        self.assertIn("Technician Notes", repair_detail)
        self.assertIn('name="technician_notes"', repair_detail)
        self.assertIn("Mark Repair Completed", repair_detail)
        self.assertIn("Invoice Status", repair_detail)
        self.assertIn("Completion Status", repair_detail)
        self.assertIn('querySelectorAll(\'input[type="date"]\')', form_helpers)
        self.assertIn("tm-date-clear-button", form_helpers)
        self.assertIn("Clear date", form_helpers)
        self.assertIn("Not Invoiced", repair_detail)
        self.assertIn("Invoiced: {{ invoice.invoice_number }}", repair_detail)
        self.assertIn("Back to Repair Workspace", repair_detail)
        self.assertIn("View Completed Repair in Timeline", repair_detail)
        self.assertNotIn("Generate Invoice", repair_detail)
        self.assertIn("Final Inspection Comments", repair_detail)
        self.assertIn('name="final_inspection_passed"', repair_detail)

    def test_vehicle_detail_keeps_add_finding_action_outside_repair_workspace(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        vehicle_card_idx = vehicle_detail.index('id="vehicle-information"')
        add_finding_idx = vehicle_detail.index('aria-label="Add finding or recommended work"')
        workspace_idx = vehicle_detail.index('id="repair-workspace"')
        timeline_idx = vehicle_detail.index('id="vehicle-timeline"')
        findings_idx = vehicle_detail.index('id="recommendations-findings"')

        self.assertLess(vehicle_card_idx, add_finding_idx)
        self.assertLess(add_finding_idx, workspace_idx)
        self.assertLess(workspace_idx, timeline_idx)
        self.assertLess(workspace_idx, findings_idx)
        self.assertLess(findings_idx, timeline_idx)
        self.assertIn("Active Repair Jobs", vehicle_detail)
        self.assertIn("Open Repairs", vehicle_detail)
        self.assertIn("Approved Repairs", vehicle_detail)
        self.assertIn("In Progress Repairs", vehicle_detail)
        self.assertIn("Ready to Complete", vehicle_detail)
        self.assertIn("Recently Completed", vehicle_detail)
        self.assertIn("No active repairs yet. Create one from a finding, estimate, or manual repair.", vehicle_detail)
        self.assertIn("No approved repairs waiting to start.", vehicle_detail)
        self.assertIn("No repairs currently in progress.", vehicle_detail)
        self.assertIn("Ready for Invoice", vehicle_detail)
        self.assertIn("Invoiced Jobs", vehicle_detail)
        self.assertIn("Create Final Invoice", vehicle_detail)
        self.assertIn("{% if ready_invoice_items %}", vehicle_detail)
        self.assertIn("Complete repair jobs before creating a final invoice.", vehicle_detail)
        self.assertIn("View Invoice", vehicle_detail)
        self.assertIn("View in Vehicle Timeline", vehicle_detail)
        self.assertIn("Not Invoiced", vehicle_detail)
        self.assertIn("Invoiced: {{ item.invoice_number }}", vehicle_detail)
        self.assertIn("Additional Findings / Recommended Work", vehicle_detail)
        self.assertIn('aria-label="Expandable additional finding status groups"', vehicle_detail)
        self.assertIn('class="tm-history-summary-card tm-findings-status-card"', vehicle_detail)
        self.assertIn('data-finding-status-group="{{ group.label }}"', vehicle_detail)
        self.assertIn("Create Estimate", vehicle_detail)
        self.assertIn("Open Repair", vehicle_detail)
        self.assertIn("Customer Decision / Update Status", vehicle_detail)
        self.assertIn("Edit Finding", vehicle_detail)
        self.assertIn("build_finding_estimator_href(customer, vehicle, item)", vehicle_detail)
        self.assertNotIn("Open Source: Finding Repair Job", vehicle_detail)
        self.assertNotIn("Save Estimate / Recommended Repair", vehicle_detail)
        self.assertIn("Document problems found during inspection or during a repair. Approved recommended repairs become repair jobs.", vehicle_detail)
        self.assertIn("+ Add Finding / Recommended Work", vehicle_detail)
        self.assertEqual(vehicle_detail.count("+ Add Finding / Recommended Work"), 1)
        self.assertIn("Declined / Deferred", vehicle_detail)
        self.assertIn('"statuses": ["Open"]', vehicle_detail)
        self.assertIn('"statuses": ["Approved"]', vehicle_detail)
        self.assertIn('"statuses": ["Declined", "Deferred"]', vehicle_detail)
        self.assertNotIn('tm-findings-visible-list" aria-label="Saved additional findings and recommended work"', vehicle_detail)
        self.assertNotIn("Completed / Repaired Services", vehicle_detail)
        self.assertNotIn("completed_repair_work_items", vehicle_detail)
        self.assertIn("Vehicle Timeline", vehicle_detail)
        self.assertNotIn('href="#vehicle-timeline">Vehicle Timeline</a>', vehicle_detail)
        self.assertIn("item.action_label", vehicle_detail)
        self.assertIn("item.photo_count", vehicle_detail)
        self.assertIn("item.source_label", vehicle_detail)
        self.assertIn("item.status_label", vehicle_detail)
        self.assertIn("item.workspace_status_label", vehicle_detail)
        self.assertIn("item.primary_action_label", vehicle_detail)
        self.assertIn("Status {{ item.status_label }}", vehicle_detail)
        self.assertNotIn('<h2 style="margin:4px 0 0;">Inspection Findings</h2>', vehicle_detail)

    def test_workspace_groups_status_lanes_and_primary_actions(self):
        vehicle = {"id": 1, "year": 2016, "make": "Honda", "model": "Accord", "mileage": 120000}
        active = [
            {
                "source_type": "repair",
                "source_id": 20,
                "title": "Manual tire repair",
                "repair_work_status": "ready",
                "record_status": "Open",
                "repair_record_url": "/repairs/20",
                "url": "/repairs/20",
            },
            {
                "source_type": "finding",
                "source_id": 21,
                "source_label": "Source: Finding",
                "create_estimate_url": "/estimator?source=finding&finding_id=21",
                "title": "Front pads",
                "repair_work_status": "ready",
                "linked_repair_record_id": 21,
                "repair_record_url": "/repairs/21",
                "url": "/findings/21",
            },
            {
                "source_type": "finding",
                "source_id": 22,
                "title": "Front rotors",
                "repair_work_status": "in_progress",
                "linked_repair_record_id": 22,
                "repair_record_url": "/repairs/22",
                "url": "/findings/22",
            },
            {
                "source_type": "finding",
                "source_id": 23,
                "title": "Brake fluid",
                "repair_work_status": "ready",
                "linked_repair_record_id": 23,
                "repair_record_url": "/repairs/23",
                "url": "/findings/23",
                "checklist_summary": {"completed": 2, "total": 2, "incomplete": 0, "percent": 100},
            },
        ]

        groups = pro_module.build_repair_workspace_groups(vehicle, active, [])

        self.assertEqual(groups["open"][0]["workspace_status_label"], "Open")
        self.assertEqual(groups["open"][0]["primary_action_label"], "Open Repair / Track Parts")
        self.assertEqual(groups["approved"][0]["workspace_status_label"], "Approved")
        self.assertEqual(groups["approved"][0]["primary_action_label"], "Create Estimate")
        self.assertEqual(groups["in_progress"][0]["workspace_status_label"], "In Progress")
        self.assertEqual(groups["in_progress"][0]["primary_action_label"], "Continue Repair / Track Parts")
        self.assertEqual(groups["ready_to_complete"][0]["workspace_status_label"], "Ready to Complete")
        self.assertEqual(groups["ready_to_complete"][0]["primary_action_label"], "Mark Completed")

    def test_invoice_builder_selects_completed_ready_jobs(self):
        invoice_builder = (ROOT / "templates" / "pro" / "invoice_builder.html").read_text(encoding="utf-8")

        self.assertIn("Ready for Invoice", invoice_builder)
        self.assertIn('{% for job in job_groups.ready %}', invoice_builder)
        self.assertIn('type="checkbox" name="repair_record_id" value="{{ job.id }}" checked', invoice_builder)
        self.assertIn("Create Invoice", invoice_builder)
        self.assertIn("Already Invoiced", invoice_builder)

    def test_invoice_number_helper_uses_tm_sequence(self):
        self.assertEqual(pro_module.invoice_number_for(1, "2026-06-25T12:30:00"), "TM-INV-0001")
        self.assertEqual(pro_module.invoice_number_for(4, "2026-06-25T12:30:00"), "TM-INV-0004")

    def test_pro_date_filter_displays_utc_timestamps_in_shop_local_date(self):
        self.assertEqual(pro_module.format_pro_date("2026-07-01T05:57:00+00:00"), "06/30/2026")
        self.assertEqual(pro_module.format_pro_date("2026-07-01T05:57:00"), "06/30/2026")
        self.assertEqual(pro_module.format_pro_date("2026-06-30"), "06/30/2026")

    def test_finding_detail_has_estimate_and_customer_decision_actions(self):
        finding_detail = (ROOT / "templates" / "pro" / "finding_detail.html").read_text(encoding="utf-8")

        self.assertIn("Recommended Repair Estimate", finding_detail)
        self.assertIn("Create Estimate", finding_detail)
        self.assertIn("Review Estimate", finding_detail)
        self.assertIn("Customer Decision", finding_detail)
        self.assertIn("Update Customer Decision", finding_detail)
        self.assertIn("Open Repair Workspace", finding_detail)
        self.assertIn("Start Repair", finding_detail)

    def test_repair_completion_persists_uploaded_after_photos(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        now = "2026-06-24T12:00:00"

        saved = pro_module.upsert_repair_completion(
            conn,
            repair_record_id=77,
            form={"completion_notes": "Done", "final_inspection_passed": "1", "final_inspection_notes": "QA pass"},
            completed_at=now,
            now=now,
            after_repair_photo_paths=["/static/uploads/after-1.jpg"],
        )
        saved = pro_module.upsert_repair_completion(
            conn,
            repair_record_id=77,
            form={"completion_notes": "Done", "final_inspection_notes": "QA pass"},
            completed_at=now,
            now=now,
            after_repair_photo_paths=["/static/uploads/after-2.jpg"],
        )

        self.assertEqual(
            saved["after_repair_photo_urls"],
            ["/static/uploads/after-1.jpg", "/static/uploads/after-2.jpg"],
        )
        self.assertEqual(
            json.loads(saved["after_repair_photo_paths"]),
            ["/static/uploads/after-1.jpg", "/static/uploads/after-2.jpg"],
        )
        self.assertEqual(saved["final_inspection_passed"], 0)
        self.assertEqual(saved["final_inspection_notes"], "QA pass")

    def test_repair_completion_persists_date_mileage_and_technician_notes(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        now = "2026-06-24T12:00:00"

        saved = pro_module.upsert_repair_completion(
            conn,
            repair_record_id=79,
            form={
                "completion_date": "2026-06-24",
                "completion_mileage": "177,000",
                "technician_notes": "Road tested and rechecked.",
                "completion_notes": "Ready for customer.",
                "final_inspection_passed": "1",
                "final_inspection_notes": "QA pass",
            },
            completed_at=now,
            now=now,
        )

        self.assertEqual(saved["completion_date"], "2026-06-24")
        self.assertEqual(saved["completion_mileage"], 177000)
        self.assertEqual(pro_module.format_mileage(saved["completion_mileage"]), "177,000")
        self.assertEqual(saved["technician_notes"], "Road tested and rechecked.")
        self.assertEqual(saved["completion_notes"], "Ready for customer.")

    def test_final_inspection_passed_and_legacy_comments_are_preserved(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        now = "2026-06-24T12:00:00"

        saved = pro_module.upsert_repair_completion(
            conn,
            repair_record_id=78,
            form={"final_inspection_passed": "1", "final_inspection_notes": "Old QA notes still visible."},
            completed_at=now,
            now=now,
        )

        self.assertEqual(saved["final_inspection_passed"], 1)
        self.assertEqual(saved["final_inspection_notes"], "Old QA notes still visible.")

    def test_repair_completion_total_after_photos_cannot_exceed_five(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        now = "2026-06-24T12:00:00"

        pro_module.upsert_repair_completion(
            conn,
            repair_record_id=77,
            form={},
            completed_at=now,
            now=now,
            after_repair_photo_paths=[f"/static/uploads/after-{idx}.jpg" for idx in range(5)],
        )

        with self.assertRaises(pro_module.HTTPException):
            pro_module.upsert_repair_completion(
                conn,
                repair_record_id=77,
                form={},
                completed_at=now,
                now=now,
                after_repair_photo_paths=["/static/uploads/after-extra.jpg"],
            )

    def test_estimate_conversion_mobile_controls_and_phone_mask_are_present(self):
        conversion = (ROOT / "templates" / "pro" / "estimate_conversion.html").read_text(encoding="utf-8")

        self.assertIn("tm-convert-choice--selected", conversion)
        self.assertIn("/static/pro_form_helpers.js", conversion)
        self.assertIn('id="new_customer_phone"', conversion)
        self.assertIn("data-pro-phone-input", conversion)
        self.assertIn('id="new_vehicle_mileage"', conversion)
        self.assertIn("data-pro-mileage-input", conversion)
        self.assertIn("Select Existing Customer", conversion)
        self.assertIn("Create New Customer", conversion)
        self.assertIn("Select Existing Vehicle", conversion)
        self.assertIn("Create New Vehicle", conversion)
        self.assertIn('placeholder="Enter current mileage"', conversion)
        self.assertNotIn('placeholder="120,000"', conversion)

    def test_estimate_conversion_payload_preserves_optional_vehicle_mileage(self):
        payload = pro_module.load_estimate_conversion_payload(
            json.dumps(
                {
                    "vehicle": {"year": "2016", "make": "Honda", "model": "Accord", "mileage": "177,000"},
                    "lineItems": [{"serviceText": "Front Brake Pads", "laborHours": 1, "laborRate": 120}],
                }
            )
        )
        blank_payload = pro_module.load_estimate_conversion_payload(
            json.dumps(
                {
                    "vehicle": {"year": "2016", "make": "Honda", "model": "Accord"},
                    "lineItems": [{"serviceText": "Front Brake Pads", "laborHours": 1, "laborRate": 120}],
                }
            )
        )

        self.assertEqual(payload["vehicle"]["mileage"], 177000)
        self.assertIsNone(blank_payload["vehicle"]["mileage"])

    def test_estimator_customer_quote_phone_fields_use_hyphen_mask(self):
        estimator = (ROOT / "templates" / "estimator.html").read_text(encoding="utf-8")
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="customerPhone" type="tel" placeholder="###-###-####"', estimator)
        self.assertIn('id="businessPhone" type="tel"', estimator)
        self.assertIn("function formatPhone", app_js)
        self.assertIn("bindEstimatorPhoneInput(customerPhoneEl)", app_js)
        self.assertIn("bindEstimatorPhoneInput(businessPhoneEl)", app_js)
        self.assertIn("phoneValue(customerPhoneEl)", app_js)

    def test_mileage_formats_with_commas_and_parses_clean(self):
        self.assertEqual(pro_module.format_mileage(120000), "120,000")
        self.assertEqual(pro_module.format_mileage("177000"), "177,000")
        self.assertEqual(pro_module.optional_int({"mileage": "177,000"}, "mileage"), 177000)

    def test_phone_formats_with_hyphens_and_parses_raw_digits(self):
        self.assertEqual(pro_module.format_phone("2223334444"), "222-333-4444")
        self.assertEqual(pro_module.format_phone("1 (222) 333-4444"), "222-333-4444")
        self.assertEqual(pro_module.clean_phone("222-333-4444"), "2223334444")
        self.assertEqual(pro_module.clean_phone("1-222-333-4444"), "2223334444")

    def test_parts_sources_section_is_visible_on_workspace_cards(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")

        self.assertIn("Parts Sources", vehicle_detail)
        self.assertIn("Searches by vehicle + repair. Confirm fitment on the vendor site before ordering.", vehicle_detail)
        self.assertIn("Vendor sites may use saved garage filters. Always confirm year, make, model, engine, and fitment before ordering.", vehicle_detail)
        self.assertIn("Marketplace Search", vehicle_detail)
        self.assertIn("Catalog Search", vehicle_detail)
        self.assertIn("source.search_group == source_group", vehicle_detail)
        self.assertIn("item.parts_sources", vehicle_detail)
        self.assertIn("O'Reilly", pro_module.DEFAULT_PARTS_SOURCE_LABELS)
        self.assertIn("Google Shopping", pro_module.DEFAULT_PARTS_SOURCE_LABELS)
        self.assertIn("1A Auto", pro_module.DEFAULT_PARTS_SOURCE_LABELS)

    def test_parts_tracking_crud_summary_excludes_returned_and_not_needed(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        pro_module.ensure_repair_records_schema(conn)
        pro_module.ensure_repair_job_parts_schema(conn)
        now = "2026-06-29T10:00:00"
        conn.execute(
            """
            INSERT INTO repair_records (id, customer_id, vehicle_id, repair_name, status, created_at)
            VALUES (44, 1, 1, 'Coolant Drain & Refill', 'Open', ?)
            """,
            (now,),
        )

        coolant_id = pro_module.create_repair_job_part(
            conn,
            44,
            {"part_name": "Engine Coolant", "qty": "2", "unit_cost": "18.50", "status": "Ordered"},
            now,
        )
        returned_id = pro_module.create_repair_job_part(
            conn,
            44,
            {"part_name": "Wrong Cap", "qty": "1", "unit_cost": "12", "status": "Returned"},
            now,
        )
        not_needed_id = pro_module.create_repair_job_part(
            conn,
            44,
            {"part_name": "Extra Hose", "qty": "1", "unit_cost": "22", "status": "Not Needed"},
            now,
        )

        parts = pro_module.load_repair_job_parts(conn, 44)
        summary = pro_module.repair_job_parts_summary(parts)

        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["tracked_parts_total"], 37)
        self.assertEqual(parts[0]["qty_display"], "2")
        self.assertEqual(parts[0]["subtotal"], 37)

        pro_module.update_repair_job_part(
            conn,
            44,
            coolant_id,
            {"status": "Installed"},
            "2026-06-29T10:05:00",
        )
        self.assertEqual(pro_module.load_repair_job_parts(conn, 44)[0]["status"], "Installed")

        pro_module.delete_repair_job_part(conn, 44, returned_id)
        summary = pro_module.repair_job_parts_summary(pro_module.load_repair_job_parts(conn, 44))
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["tracked_parts_total"], 37)
        self.assertIn(not_needed_id, [part["id"] for part in summary["parts"]])

    def test_parts_tracking_ui_is_available_on_workspace_and_repair_detail(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        repair_detail = (ROOT / "templates" / "pro" / "repair_detail.html").read_text(encoding="utf-8")
        partial = (ROOT / "templates" / "pro" / "partials" / "parts_tracking.html").read_text(encoding="utf-8")

        self.assertIn('include "pro/partials/parts_tracking.html"', vehicle_detail)
        self.assertIn('include "pro/partials/parts_tracking.html"', repair_detail)
        self.assertIn("Parts Tracking", partial)
        self.assertIn("No parts tracked yet.", partial)
        self.assertIn("Vendor / Source", partial)
        self.assertIn("Part Number", partial)
        self.assertIn("repair_job_part_status_options", partial)

    def test_repair_workspace_collapsible_sections_and_track_parts_actions_render(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        pro_py = (ROOT / "routers" / "pro.py").read_text(encoding="utf-8")

        self.assertIn('"key": "in_progress"', vehicle_detail)
        self.assertIn('"key": "ready_to_complete"', vehicle_detail)
        self.assertIn('data-workspace-section="recently_completed"', vehicle_detail)
        self.assertIn('data-workspace-section="already_invoiced"', vehicle_detail)
        self.assertIn("tm-workspace-section-summary", vehicle_detail)
        self.assertIn('Recently Completed <span class="tm-workspace-section-count">({{ recently_completed_count }})</span>', vehicle_detail)
        self.assertIn('Already Invoiced <span class="tm-workspace-section-count">({{ invoiced_repair_items|length }})</span>', vehicle_detail)
        self.assertIn("No recently completed repairs.", vehicle_detail)
        self.assertIn("No repair jobs have been invoiced yet.", vehicle_detail)
        self.assertIn("View Invoice", vehicle_detail)
        self.assertIn("Open Repair / Track Parts", pro_py)
        self.assertIn("Continue Repair / Track Parts", pro_py)
        recent_section_start = vehicle_detail.index('data-workspace-section="recently_completed"')
        recent_section_open_window = vehicle_detail[recent_section_start - 140:recent_section_start + 140]
        self.assertNotIn(" open", recent_section_open_window)
        invoiced_section_start = vehicle_detail.index('data-workspace-section="already_invoiced"')
        invoiced_section_open_window = vehicle_detail[invoiced_section_start - 140:invoiced_section_start + 140]
        self.assertNotIn(" open", invoiced_section_open_window)

    def test_pro_customer_and_vehicle_forms_use_shared_input_formatters(self):
        customers = (ROOT / "templates" / "pro" / "customers.html").read_text(encoding="utf-8")
        customer_detail = (ROOT / "templates" / "pro" / "customer_detail.html").read_text(encoding="utf-8")
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")
        repair_edit = (ROOT / "templates" / "pro" / "repair_edit.html").read_text(encoding="utf-8")
        finding_edit = (ROOT / "templates" / "pro" / "finding_edit.html").read_text(encoding="utf-8")
        maintenance_detail = (ROOT / "templates" / "pro" / "maintenance_detail.html").read_text(encoding="utf-8")
        helper = (ROOT / "static" / "pro_form_helpers.js").read_text(encoding="utf-8")

        self.assertIn("/static/pro_form_helpers.js", customers)
        self.assertIn("/static/pro_form_helpers.js", customer_detail)
        self.assertIn("/static/pro_form_helpers.js", vehicle_detail)
        self.assertIn("data-pro-phone-input", customers)
        self.assertIn("data-pro-phone-input", customer_detail)
        self.assertIn("data-pro-mileage-input", customer_detail)
        self.assertIn('placeholder="Enter current mileage"', customer_detail)
        self.assertIn("data-pro-mileage-input", vehicle_detail)
        self.assertIn("data-pro-mileage-input", repair_edit)
        self.assertIn("data-pro-mileage-input", finding_edit)
        self.assertIn("data-pro-mileage-input", maintenance_detail)
        self.assertIn("/static/pro_form_helpers.js", repair_edit)
        self.assertIn("/static/pro_form_helpers.js", finding_edit)
        self.assertIn("/static/pro_form_helpers.js", maintenance_detail)
        self.assertIn("function formatPhone", helper)
        self.assertIn("return `${digits.slice(0, 3)}-${digits.slice(3, 6)}-${digits.slice(6)}`", helper)
        self.assertIn("function formatMileage", helper)
        self.assertIn("normalizeMileageBeforeSubmit", helper)

    def test_maintenance_reminder_message_includes_sender_vehicle_service_and_cta(self):
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
                "scheduling_link": "https://calendly.com/torquemech/service",
            },
        )

        self.assertIn("Hi Natalie, this is Bryan from TorqueMech Auto.", message)
        self.assertIn("Your 2008 TOYOTA SEQUOIA is overdue for Oil Change.", message)
        self.assertIn("Our records show it was due around 182,000 miles or by 07/03/2026.", message)
        self.assertIn("You are currently at about 183,777 miles.", message)
        self.assertIn("Schedule your service here:\nhttps://calendly.com/torquemech/service", message)
        self.assertTrue(message.endswith("Reply here if you have any questions."))

    def test_maintenance_reminder_message_without_scheduling_link_uses_reply_cta(self):
        message = pro_module.build_maintenance_reminder_message(
            customer={"first_name": "Natalie"},
            vehicle={"year": 2008, "make": "TOYOTA", "model": "SEQUOIA", "mileage": 183777},
            record={
                "service_type": "Oil Change",
                "maintenance_status_key": "overdue",
                "next_due_mileage": 182000,
                "next_due_date": "2026-07-03",
            },
            sender_context={"shop_name": "Bryan from TorqueMech Auto"},
        )

        self.assertNotIn("Schedule your service here:", message)
        self.assertNotIn("http", message)
        self.assertTrue(message.endswith("Reply here when you're ready to schedule."))

    def test_maintenance_reminder_sender_name_priority_and_fallback(self):
        self.assertEqual(
            pro_module.resolve_sender_display_name(
                {
                    "business_name": "Business Name",
                    "mechanic_name": "Mechanic Name",
                    "shop_profile": {"shop_name": "Shop Name"},
                }
            ),
            "Shop Name",
        )
        self.assertEqual(
            pro_module.resolve_sender_display_name({"business_name": "Business Name", "mechanic_name": "Mechanic Name"}),
            "Business Name",
        )
        self.assertEqual(pro_module.resolve_sender_display_name({"mechanic_name": "Mechanic Name"}), "Mechanic Name")
        self.assertEqual(pro_module.resolve_sender_display_name({"user": {"full_name": "Alex Mechanic"}}), "Alex Mechanic")
        self.assertEqual(pro_module.resolve_sender_display_name({"account": {"first_name": "Alex"}}), "Alex")
        self.assertEqual(pro_module.resolve_sender_display_name({}), "your mechanic")

    def test_maintenance_reminder_message_avoids_missing_data_wording(self):
        base = {
            "customer": {"first_name": "Natalie"},
            "vehicle": {"year": 2008, "make": "TOYOTA", "model": "SEQUOIA"},
            "sender_context": {},
        }

        mileage_only = pro_module.build_maintenance_reminder_message(
            **base,
            record={"service_type": "Oil Change", "maintenance_status_key": "overdue", "next_due_mileage": 182000},
        )
        date_only = pro_module.build_maintenance_reminder_message(
            **base,
            record={"service_type": "Oil Change", "maintenance_status_key": "overdue", "next_due_date": "2026-07-03"},
        )

        self.assertIn("Our records show it was due around 182,000 miles.", mileage_only)
        self.assertNotIn("You are currently at about", mileage_only)
        self.assertIn("Our records show it was due by 07/03/2026.", date_only)

    def test_vehicle_maintenance_records_calculate_due_status(self):
        records = [
            {
                "service_type": "Oil Change",
                "date_performed": "2026-01-01",
                "mileage_performed": 95000,
                "interval_miles": 5000,
                "interval_months": 6,
            },
            {
                "service_type": "Transmission Service",
                "date_performed": "2026-05-15",
                "mileage_performed": 141500,
                "interval_miles": 60000,
                "interval_months": 48,
            },
            {
                "service_type": "Tire Rotation",
                "date_performed": "2026-05-01",
                "mileage_performed": 140800,
                "interval_miles": 5000,
                "interval_months": 6,
            },
            {
                "service_type": "Brake Fluid",
                "date_performed": "2024-10-20",
                "mileage_performed": 118000,
                "interval_miles": 30000,
                "interval_months": 24,
            },
            {
                "service_type": "Cabin Air Filter",
                "date_performed": "2026-07-01",
                "mileage_performed": 130000,
                "interval_miles": 15000,
                "interval_months": 12,
            },
        ]

        annotated = pro_module.annotate_vehicle_maintenance_records(
            records,
            {"mileage": 145000},
            {"first_name": "Natalie"},
            date(2026, 7, 4),
        )

        self.assertEqual(annotated[0]["next_due_mileage"], 100000)
        self.assertEqual(annotated[0]["next_due_date"], "2026-07-01")
        self.assertEqual(annotated[0]["remaining_miles"], -45000)
        self.assertEqual(annotated[0]["remaining_days"], -3)
        self.assertEqual(annotated[0]["maintenance_status"], "Overdue")
        self.assertIn("Hi Natalie, this is your mechanic.", annotated[0]["reminder_message"])
        self.assertIn("Your vehicle is overdue for Oil Change.", annotated[0]["reminder_message"])
        self.assertIn("around 100,000 miles", annotated[0]["reminder_message"])
        self.assertIn("by 07/01/2026", annotated[0]["reminder_message"])
        self.assertIn("You are currently at about 145,000 miles.", annotated[0]["reminder_message"])
        self.assertTrue(annotated[0]["reminder_message"].endswith("Reply here when you're ready to schedule."))

        self.assertEqual(annotated[1]["remaining_miles"], 56500)
        self.assertEqual(annotated[1]["maintenance_status"], "Current")
        self.assertEqual(annotated[1]["reminder_message"], "")

        self.assertEqual(annotated[2]["remaining_miles"], 800)
        self.assertEqual(annotated[2]["maintenance_status"], "Due Soon")
        self.assertIn("due for Tire Rotation", annotated[2]["reminder_message"])
        self.assertIn("around 145,800 miles", annotated[2]["reminder_message"])

        self.assertEqual(annotated[3]["remaining_miles"], 3000)
        self.assertEqual(annotated[3]["remaining_days"], 108)
        self.assertEqual(annotated[3]["maintenance_status"], "Upcoming")

        self.assertEqual(annotated[4]["remaining_miles"], 0)
        self.assertEqual(annotated[4]["remaining_days"], 362)
        self.assertEqual(annotated[4]["maintenance_status"], "Overdue")

    def test_vehicle_maintenance_driving_rate_prefers_recent_reliable_pair(self):
        today = date(2026, 7, 4)
        maintenance_records = [
            {
                "service_type": "Oil Change",
                "date_performed": "2026-05-01",
                "mileage_performed": 140000,
                "interval_miles": 5000,
                "interval_months": 6,
            },
            {
                "service_type": "Tire Rotation",
                "date_performed": "2026-06-25",
                "mileage_performed": 144000,
                "interval_miles": 5000,
                "interval_months": 6,
            },
        ]
        service_history_records = [
            {"service_date": "2026-06-01", "mileage": 143000},
            {"service_date": "2026-06-20", "mileage": 146000},
            {"service_date": "", "mileage": 142000},
            {"service_date": "2026-04-01", "mileage": None},
        ]

        rate = pro_module.estimate_vehicle_driving_rate(
            maintenance_records,
            service_history_records,
            {"mileage": 145000},
            today,
        )

        self.assertIsNotNone(rate)
        self.assertEqual(rate["source_date"], "2026-06-01")
        self.assertEqual(rate["source_mileage"], 143000)
        self.assertEqual(rate["miles_per_month"], 1845)

        annotated = pro_module.annotate_vehicle_maintenance_records(
            [
                {
                    "service_type": "Oil Change",
                    "date_performed": "2026-02-01",
                    "mileage_performed": 145000,
                    "interval_miles": 5000,
                    "interval_months": 6,
                }
            ],
            {"mileage": 145000},
            {"first_name": "Natalie"},
            today,
            rate,
        )

        self.assertEqual(annotated[0]["next_due_mileage"], 150000)
        self.assertEqual(annotated[0]["estimated_due_date_by_mileage"], "2026-09-25")
        self.assertEqual(annotated[0]["due_date_by_time_interval"], "2026-08-01")
        self.assertEqual(annotated[0]["earliest_estimated_due_date"], "2026-08-01")

    def test_vehicle_maintenance_template_has_copy_only_reminder_controls(self):
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")

        self.assertIn("data-maintenance-reminder-toggle", vehicle_detail)
        self.assertIn("Send Reminder", vehicle_detail)
        self.assertIn("data-suggested-message", vehicle_detail)
        self.assertIn("data-copy-message", vehicle_detail)
        self.assertIn("TorqueMech booking link included", vehicle_detail)
        self.assertIn("External scheduling link included", vehicle_detail)
        self.assertIn("No scheduling link saved", vehicle_detail)
        self.assertIn("Add Scheduling Link", vehicle_detail)
        self.assertIn("Reminder History", vehicle_detail)
        self.assertIn("navigator.clipboard?.writeText", vehicle_detail)
        self.assertNotIn("Mark as Sent", vehicle_detail)
        self.assertNotIn("Snooze 7 Days", vehicle_detail)

    def test_follow_up_maintenance_uses_latest_record_per_vehicle_service(self):
        records = [
            {
                "id": 1,
                "customer_id": 7,
                "vehicle_id": 9,
                "service_type": "Oil Change",
                "date_performed": "2026-01-01",
                "mileage_performed": 100000,
            },
            {
                "id": 2,
                "customer_id": 7,
                "vehicle_id": 9,
                "service_type": "oil service",
                "date_performed": "2026-07-01",
                "mileage_performed": 105000,
            },
            {
                "id": 3,
                "customer_id": 7,
                "vehicle_id": 9,
                "service_type": "Brake Fluid",
                "date_performed": "2024-01-01",
                "mileage_performed": 80000,
            },
        ]

        latest = pro_module.latest_maintenance_records_by_vehicle_service(records)

        self.assertEqual(len(latest), 2)
        self.assertIn(2, {record["id"] for record in latest})
        self.assertIn(3, {record["id"] for record in latest})

    def test_newer_maintenance_record_becomes_active_baseline(self):
        records = [
            {
                "id": 1,
                "customer_id": 7,
                "vehicle_id": 9,
                "service_type": "Oil Change",
                "date_performed": "2026-03-03",
                "mileage_performed": 177000,
                "interval_miles": 5000,
                "interval_months": 6,
            },
            {
                "id": 2,
                "customer_id": 7,
                "vehicle_id": 9,
                "service_type": "engine oil service",
                "date_performed": "2026-07-10",
                "mileage_performed": 183900,
                "interval_miles": 5000,
                "interval_months": 6,
            },
        ]

        annotated = pro_module.annotate_vehicle_maintenance_records(
            records,
            {"mileage": 184000},
            {"first_name": "Natalie"},
            date(2026, 7, 11),
        )
        pro_module.mark_active_maintenance_baselines(annotated)

        old_record = next(record for record in annotated if record["id"] == 1)
        new_record = next(record for record in annotated if record["id"] == 2)
        self.assertFalse(old_record["is_active_maintenance_baseline"])
        self.assertEqual(old_record["reminder_message"], "")
        self.assertTrue(new_record["is_active_maintenance_baseline"])
        self.assertEqual(new_record["next_due_mileage"], 188900)
        self.assertEqual(new_record["next_due_date"], "2027-01-10")

    def test_older_reminder_events_remain_stored_but_do_not_create_active_followups(self):
        conn = self.reminder_conn()
        pro_module.create_maintenance_reminder_event(
            conn,
            customer_id=7,
            vehicle_id=9,
            maintenance_record_id=1,
            service_type="Oil Change",
            status="copied",
            message="Old reminder",
            created_at="2026-07-01T12:00:00",
        )
        conn.commit()

        old_record = {
            "id": 1,
            "customer_id": 7,
            "vehicle_id": 9,
            "service_type": "Oil Change",
            "maintenance_status_key": "overdue",
            "maintenance_status": "Overdue",
            "is_active_maintenance_baseline": False,
        }
        pro_module.attach_maintenance_reminder_events(
            [old_record],
            pro_module.load_maintenance_reminder_events_map(conn, {1}),
        )

        self.assertEqual(old_record["latest_automatic_reminder_event"]["message"], "Old reminder")
        self.assertIsNone(pro_module.maintenance_reminder_follow_up_bucket(old_record, date(2026, 7, 11)))

    def test_prior_maintenance_records_still_contribute_to_driving_rate_prediction(self):
        rate = pro_module.estimate_vehicle_driving_rate(
            [
                {"service_type": "Oil Change", "date_performed": "2026-03-03", "mileage_performed": 177000},
                {"service_type": "engine oil service", "date_performed": "2026-07-10", "mileage_performed": 183900},
            ],
            [],
            {"mileage": 184900},
            date(2026, 7, 30),
        )

        self.assertIsNotNone(rate)
        self.assertEqual(rate["source_date"], "2026-07-10")
        self.assertEqual(rate["source_mileage"], 183900)

    def test_maintenance_service_aliases_normalize_for_latest_baseline(self):
        records = [
            {"id": 1, "customer_id": 1, "vehicle_id": 1, "service_type": "Oil Change", "date_performed": "2026-01-01", "mileage_performed": 100000},
            {"id": 2, "customer_id": 1, "vehicle_id": 1, "service_type": "engine oil", "date_performed": "2026-02-01", "mileage_performed": 105000},
            {"id": 3, "customer_id": 1, "vehicle_id": 1, "service_type": "oil service", "date_performed": "2026-03-01", "mileage_performed": 110000},
            {"id": 4, "customer_id": 1, "vehicle_id": 1, "service_type": "engine oil service", "date_performed": "2026-04-01", "mileage_performed": 115000},
        ]

        latest = pro_module.latest_maintenance_records_by_vehicle_service(records)

        self.assertEqual([record["id"] for record in latest], [4])

    def test_follow_up_dashboard_template_is_maintenance_reminder_only(self):
        follow_ups = (ROOT / "templates" / "pro" / "follow_ups.html").read_text(encoding="utf-8")

        self.assertIn("Copy Message", follow_ups)
        self.assertIn("TorqueMech booking link included", follow_ups)
        self.assertIn("External scheduling link included", follow_ups)
        self.assertIn("No scheduling link saved", follow_ups)
        self.assertIn("Add your scheduling link in Shop Settings to include it in customer reminders.", follow_ups)
        self.assertIn("Due Mileage", follow_ups)
        self.assertIn("Due Date", follow_ups)
        self.assertIn("Sent / Waiting", follow_ups)
        self.assertNotIn("Mark as Sent", follow_ups)
        self.assertNotIn("Snooze 7 Days", follow_ups)
        self.assertNotIn("Mark Completed", follow_ups)
        self.assertNotIn("Follow-Up Candidates", follow_ups)

    def test_pro_date_fields_use_native_picker_with_calendar_and_clear_controls(self):
        helper = (ROOT / "static" / "pro_form_helpers.js").read_text(encoding="utf-8")
        repair_detail = (ROOT / "templates" / "pro" / "repair_detail.html").read_text(encoding="utf-8")
        maintenance_detail = (ROOT / "templates" / "pro" / "maintenance_detail.html").read_text(encoding="utf-8")
        repair_edit = (ROOT / "templates" / "pro" / "repair_edit.html").read_text(encoding="utf-8")
        finding_edit = (ROOT / "templates" / "pro" / "finding_edit.html").read_text(encoding="utf-8")
        vehicle_detail = (ROOT / "templates" / "pro" / "vehicle_detail.html").read_text(encoding="utf-8")

        self.assertIn('id="completion_date" name="completion_date" type="date"', repair_detail)
        self.assertIn('id="date_performed" name="date_performed" type="date"', maintenance_detail)
        self.assertIn('id="due_date" name="due_date" type="date"', maintenance_detail)
        self.assertIn('id="repair_date" name="repair_date" type="date"', repair_edit)
        self.assertIn('id="finding_date" name="finding_date" type="date"', finding_edit)
        self.assertIn('name="repair_date" type="date"', vehicle_detail)
        self.assertIn("tm-date-picker-button", helper)
        self.assertIn("tm-date-clear-button", helper)
        self.assertIn("input.showPicker", helper)
        self.assertIn('scope.querySelectorAll(\'input[type="date"]\').forEach(bindDateInput)', helper)


if __name__ == "__main__":
    unittest.main()
