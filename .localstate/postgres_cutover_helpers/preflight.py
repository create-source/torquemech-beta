import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

root = Path(os.environ["CUTOVER_ROOT"])
source = Path(os.environ["SOURCE_SQLITE_PATH"]).resolve()
target_state = os.environ["CUTOVER_TARGET_STATE"]
target_url = (os.environ.get("TARGET_DATABASE_URL") or "").strip()

def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)

def normalize_database_url(raw: str) -> str:
    url = raw.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("sslmode", os.environ.get("PGSSLMODE", "require"))
    return urlunparse(parsed._replace(query=urlencode(query)))

def redacted_host(raw: str) -> tuple[str, str]:
    parsed = urlparse(raw)
    kind = "postgresql" if parsed.scheme in {"postgres", "postgresql"} else (parsed.scheme or "unknown")
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    dbname = parsed.path.lstrip("/") if parsed.path else ""
    return kind, f"{host}{port}/{dbname}" if host else "unavailable"

if source.name.lower() == "obd.sqlite":
    fail("SOURCE_SQLITE_PATH must not be .localstate/obd.sqlite.")
if not source.exists():
    fail(f"SQLite source does not exist: {source}")
expected = (root / ".localstate" / "app.db").resolve()
if source != expected:
    fail(f"SOURCE_SQLITE_PATH must be {expected}")
obd = root / ".localstate" / "obd.sqlite"
print(f"SQLite source: {source}")
print(f"OBD SQLite excluded: {obd}")

kind, host = redacted_host(target_url)
print(f"Target database type: {kind}")
print(f"Target PostgreSQL host: {host}")
parsed = urlparse(target_url)
if kind != "postgresql":
    fail("TARGET_DATABASE_URL must be a PostgreSQL URL.")
if (parsed.hostname or "").lower().endswith("railway.internal"):
    fail("TARGET_DATABASE_URL uses railway.internal. Use DATABASE_PUBLIC_URL/TCP proxy for local preflight.")

sqlite_uri = f"file:{source.as_posix()}?mode=ro"
try:
    conn = sqlite3.connect(sqlite_uri, uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"SQLite integrity_check: {integrity}")
        if integrity != "ok":
            fail("SQLite integrity_check failed.")
        conn.execute("BEGIN")
        table_rows = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        print("SQLite tables and row counts:")
        for row in table_rows:
            name = row["name"]
            count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            print(f"sqlite_table={name} rows={count}")
        conn.execute("ROLLBACK")
    finally:
        conn.close()
except sqlite3.OperationalError as exc:
    fail(f"SQLite read-only check failed, possibly due to active lock: {exc}")

os.environ["DATABASE_URL"] = target_url
from scripts import db_migration

pg = db_migration.pg_connect()
try:
    with pg:
        with pg.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                  AND table_type = 'BASE TABLE'
                ORDER BY table_schema, table_name
                """
            )
            tables = cur.fetchall()
            print("PostgreSQL user tables and row counts:")
            non_empty = []
            for schema, table in tables:
                cur.execute(
                    db_migration.psycopg_sql().SQL("SELECT COUNT(*) FROM {}.{}").format(
                        db_migration.psycopg_sql().Identifier(schema),
                        db_migration.psycopg_sql().Identifier(table),
                    )
                )
                count = cur.fetchone()[0]
                print(f"postgres_table={schema}.{table} rows={count}")
                if count:
                    non_empty.append((schema, table, count))
finally:
    pg.close()

if target_state == "EMPTY_TARGET" and tables:
    fail("TargetState EMPTY_TARGET selected, but PostgreSQL has user tables.")
if target_state == "APPROVED_EXISTING_TARGET":
    print("APPROVED_EXISTING_TARGET selected; existing tables reported for operator review.")

print("PASS: PostgreSQL cutover preflight completed without database modifications.")
