from __future__ import annotations

import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "app.db"
SEED_PATH = BASE_DIR / "data" / "diagnostics_seed.json"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_seed_data():
    if not SEED_PATH.exists():
        raise FileNotFoundError(f"Seed file not found: {SEED_PATH}")

    with open(SEED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def upsert_symptom(cur, item: dict) -> int:
    cur.execute(
        "SELECT id FROM symptoms WHERE slug = ?",
        (item["slug"],)
    )
    existing = cur.fetchone()

    if existing:
        symptom_id = existing["id"]
        cur.execute(
            """
            UPDATE symptoms
            SET
                title = ?,
                summary = ?,
                intro = ?,
                system = ?,
                severity = ?,
                driveability = ?,
                repair_cost_min = ?,
                repair_cost_max = ?,
                difficulty = ?,
                repair_time = ?,
                is_published = 1
            WHERE id = ?
            """,
            (
                item.get("title", ""),
                item.get("summary", ""),
                item.get("intro", ""),
                item.get("system", ""),
                item.get("severity", ""),
                item.get("driveability", ""),
                item.get("repair_cost_min"),
                item.get("repair_cost_max"),
                item.get("difficulty", ""),
                item.get("repair_time", ""),
                symptom_id,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO symptoms (
                slug, title, summary, intro, system, severity, driveability,
                repair_cost_min, repair_cost_max, difficulty, repair_time, is_published
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                item.get("slug", ""),
                item.get("title", ""),
                item.get("summary", ""),
                item.get("intro", ""),
                item.get("system", ""),
                item.get("severity", ""),
                item.get("driveability", ""),
                item.get("repair_cost_min"),
                item.get("repair_cost_max"),
                item.get("difficulty", ""),
                item.get("repair_time", ""),
            ),
        )
        symptom_id = cur.lastrowid

    return symptom_id


def replace_child_rows(cur, symptom_id: int, item: dict):
    cur.execute("DELETE FROM symptom_causes WHERE symptom_id = ?", (symptom_id,))
    cur.execute("DELETE FROM symptom_codes WHERE symptom_id = ?", (symptom_id,))
    cur.execute("DELETE FROM symptom_repairs WHERE symptom_id = ?", (symptom_id,))
    cur.execute("DELETE FROM symptom_search_terms WHERE symptom_id = ?", (symptom_id,))

    for i, cause in enumerate(item.get("common_causes", []), start=1):
        cur.execute(
            """
            INSERT INTO symptom_causes (symptom_id, cause_text, sort_order)
            VALUES (?, ?, ?)
            """,
            (symptom_id, cause, i),
        )

    for i, code in enumerate(item.get("related_codes", []), start=1):
        cur.execute(
            """
            INSERT INTO symptom_codes (symptom_id, code, label, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (
                symptom_id,
                code.get("code", ""),
                code.get("label", ""),
                i,
            ),
        )

    for i, repair in enumerate(item.get("common_repairs", []), start=1):
        cur.execute(
            """
            INSERT INTO symptom_repairs (symptom_id, repair_name, sort_order)
            VALUES (?, ?, ?)
            """,
            (
                symptom_id,
                repair.get("name", ""),
                i,
            ),
        )

    for i, term in enumerate(item.get("search_terms", []), start=1):
        cur.execute(
            """
            INSERT INTO symptom_search_terms (symptom_id, term, sort_order)
            VALUES (?, ?, ?)
            """,
            (symptom_id, term, i),
        )


def main():
    items = load_seed_data()
    conn = get_db()
    cur = conn.cursor()

    imported = 0

    for item in items:
        if not item.get("slug"):
            continue

        symptom_id = upsert_symptom(cur, item)
        replace_child_rows(cur, symptom_id, item)
        imported += 1

    conn.commit()
    conn.close()

    print(f"Imported {imported} diagnostic guides into {DB_PATH.name}")


if __name__ == "__main__":
    main()