import asyncio
import sqlite3
import unittest

from routers.pro import (
    ensure_visual_reference_schema,
    load_visual_references_for_vehicle,
    read_multipart_form_data,
    seed_visual_references,
)


class VisualReferenceLibraryTests(unittest.TestCase):
    def open_temp_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def test_schema_supports_multiple_children(self):
        conn = self.open_temp_db()
        ensure_visual_reference_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO visual_reference_records (
              vehicle_identifier, service_type, title, quick_reference, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2010 Honda Accord 2.4",
                "front_brake_pads",
                "Front Brake Visual Reference",
                "Architecture test record",
                "2026-06-10T12:00:00",
            ),
        )
        reference_id = cur.lastrowid
        conn.executemany(
            """
            INSERT INTO visual_reference_images (
              visual_reference_id, image_type, image_path, caption
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                (reference_id, "component_location", "/static/a.png", "Location"),
                (reference_id, "exploded_view", "/static/b.png", "Exploded"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO visual_reference_specs (
              visual_reference_id, spec_name, spec_value, spec_unit
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                (reference_id, "Caliper Bolt", "26", "Nm"),
                (reference_id, "Lug Nut", "80", "ft-lb"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO visual_reference_oem_parts (
              visual_reference_id, part_name, oem_part_number, future_parts_intelligence_id
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                (reference_id, "Pad Set", "OEM-PAD", None),
                (reference_id, "Hardware Kit", "OEM-HW", None),
            ],
        )
        conn.executemany(
            """
            INSERT INTO visual_reference_hotspots (
              visual_reference_id, label, hotspot_type, x_percent, y_percent,
              title, description, torque_spec, fastener_size, tool_size,
              oem_part_number, related_part_name, parts_intelligence_id,
              sort_order, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    reference_id,
                    "Caliper",
                    "component",
                    42,
                    58,
                    "Caliper",
                    "Brake caliper body",
                    "26 Nm",
                    "14mm",
                    "",
                    "",
                    "Front Caliper",
                    None,
                    1,
                    "2026-06-10T12:00:00",
                ),
                (
                    reference_id,
                    "Pad Clip",
                    "fastener",
                    50,
                    62,
                    "Pad Clip",
                    "Pad retaining clip",
                    "",
                    "",
                    "",
                    "OEM-CLIP",
                    "Pad Hardware Kit",
                    None,
                    2,
                    "2026-06-10T12:00:00",
                ),
            ],
        )
        conn.commit()

        records = load_visual_references_for_vehicle(
            conn,
            {"year": 2010, "make": "Honda", "model": "Accord", "engine": "2.4"},
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["images"]), 2)
        self.assertEqual(len(records[0]["specs"]), 2)
        self.assertEqual(len(records[0]["oem_parts"]), 2)
        self.assertEqual(len(records[0]["hotspots"]), 2)

    def test_seed_support_loads_reference_fixture(self):
        conn = self.open_temp_db()
        seed_visual_references(conn)

        records = load_visual_references_for_vehicle(
            conn,
            {"year": 2018, "make": "Toyota", "model": "Camry", "engine": "2.2"},
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["service_type"], "alternator_replacement")
        self.assertGreaterEqual(len(records[0]["images"]), 2)
        self.assertGreaterEqual(len(records[0]["specs"]), 2)
        self.assertGreaterEqual(len(records[0]["oem_parts"]), 2)

    def test_honda_accord_front_brake_poc_seed_loads_complete_reference(self):
        conn = self.open_temp_db()
        seed_visual_references(conn)

        records = load_visual_references_for_vehicle(
            conn,
            {"year": 2010, "make": "Honda", "model": "Accord"},
        )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["service_type"], "front_brake_pads")
        self.assertIn("Front Brake Pads", record["title"])
        self.assertIn("Front axle", record["quick_reference"])
        self.assertEqual(
            {image["image_type"] for image in record["images"]},
            {"component_location", "exploded_view"},
        )
        self.assertGreaterEqual(len(record["specs"]), 3)
        self.assertGreaterEqual(len(record["oem_parts"]), 2)

    def test_mercedes_e320_alternator_repair_map_seed_loads_hotspots(self):
        conn = self.open_temp_db()
        seed_visual_references(conn)

        records = load_visual_references_for_vehicle(
            conn,
            {"year": 2001, "make": "Mercedes-Benz", "model": "E320"},
        )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["service_type"], "alternator_replacement")
        self.assertEqual(len(record["images"]), 1)
        self.assertEqual(len(record["hotspots"]), 4)
        titles = {hotspot["title"] for hotspot in record["hotspots"]}
        self.assertEqual(
            titles,
            {"Alternator", "Belt Tensioner", "Serpentine Belt", "B+ Terminal"},
        )
        alternator = next(hotspot for hotspot in record["hotspots"] if hotspot["title"] == "Alternator")
        self.assertEqual(alternator["torque_spec"], "42 Nm")
        self.assertEqual(alternator["fastener_size"], "E12")

    def test_multipart_upload_parser_reads_fields_and_file(self):
        boundary = "tm-test-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image_type"\r\n\r\n'
            "component_location\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image_file"; filename="location.svg"\r\n'
            "Content-Type: image/svg+xml\r\n\r\n"
            "<svg></svg>\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")

        class FakeRequest:
            headers = {"content-type": f"multipart/form-data; boundary={boundary}"}

            async def body(self):
                return body

        fields, files = asyncio.run(read_multipart_form_data(FakeRequest()))

        self.assertEqual(fields["image_type"], "component_location")
        self.assertEqual(files["image_file"]["filename"], "location.svg")
        self.assertEqual(files["image_file"]["content"], b"<svg></svg>")


if __name__ == "__main__":
    unittest.main()
