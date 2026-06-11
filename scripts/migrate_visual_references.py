from __future__ import annotations

import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = Path("/data") if Path("/data").exists() else BASE_DIR / ".localstate"
DB_PATH = STATE_DIR / "app.db"


def migrate(db_path: Path = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS visual_reference_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              vehicle_identifier TEXT NOT NULL,
              service_type TEXT NOT NULL,
              title TEXT,
              quick_reference TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS visual_reference_images (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              visual_reference_id INTEGER NOT NULL,
              image_type TEXT NOT NULL CHECK (
                image_type IN (
                  'component_location',
                  'exploded_view',
                  'belt_routing',
                  'connector_view',
                  'reference_image'
                )
              ),
              image_path TEXT NOT NULL,
              caption TEXT,
              FOREIGN KEY (visual_reference_id) REFERENCES visual_reference_records(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS visual_reference_specs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              visual_reference_id INTEGER NOT NULL,
              spec_name TEXT NOT NULL,
              spec_value TEXT NOT NULL,
              spec_unit TEXT,
              FOREIGN KEY (visual_reference_id) REFERENCES visual_reference_records(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS visual_reference_oem_parts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              visual_reference_id INTEGER NOT NULL,
              part_name TEXT NOT NULL,
              oem_part_number TEXT NOT NULL,
              future_parts_intelligence_id INTEGER,
              FOREIGN KEY (visual_reference_id) REFERENCES visual_reference_records(id)
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_visual_reference_records_vehicle_service
            ON visual_reference_records (vehicle_identifier, service_type)
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_reference_records_vehicle ON visual_reference_records (vehicle_identifier)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_reference_records_service ON visual_reference_records (service_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_reference_images_reference ON visual_reference_images (visual_reference_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_reference_specs_reference ON visual_reference_specs (visual_reference_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_reference_oem_parts_reference ON visual_reference_oem_parts (visual_reference_id)")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
    print(f"Visual Reference Library migration applied to {DB_PATH}")
