param(
    [switch]$CreateValidatedBackup,
    [switch]$ExecuteMigration,
    [switch]$ConfirmedMaintenanceWindow,
    [switch]$ConfirmedSQLiteWriteFreeze,
    [switch]$ConfirmedBackupValidated,
    [ValidateSet("EMPTY_TARGET", "APPROVED_EXISTING_TARGET")]
    [string]$TargetState = "EMPTY_TARGET"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $Root

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

function Invoke-Native {
    param([string]$FilePath, [string[]]$Arguments = @())
    & $FilePath @Arguments
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
    if ($exitCode -ne 0) {
        throw "Command failed with exit code ${exitCode}: $FilePath $($Arguments -join ' ')"
    }
}

function Invoke-Python {
    param([string]$Name, [string]$Code)
    $helperDir = Join-Path $Root ".localstate\postgres_cutover_helpers"
    New-Item -ItemType Directory -Path $helperDir -Force | Out-Null
    $path = Join-Path $helperDir "$Name.py"
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    Set-Content -LiteralPath $path -Value $Code -Encoding UTF8
    Invoke-Native $python @("-m", "py_compile", $path)
    Invoke-Native $python @($path)
}

if (-not $CreateValidatedBackup -and -not $ExecuteMigration) {
    throw "Specify -CreateValidatedBackup or -ExecuteMigration."
}
if ($CreateValidatedBackup -and $ExecuteMigration) {
    throw "Run backup creation and migration as separate commands."
}
if ([string]::IsNullOrWhiteSpace($env:SOURCE_SQLITE_PATH)) {
    throw "SOURCE_SQLITE_PATH is required."
}

$env:CUTOVER_ROOT = $Root
$env:CUTOVER_TARGET_STATE = $TargetState
if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $env:PYTHONPATH = $Root
} else {
    $env:PYTHONPATH = "$Root;$env:PYTHONPATH"
}

if ($CreateValidatedBackup) {
    Invoke-Python "create_validated_backup" @'
import os
import sqlite3
from datetime import datetime
from pathlib import Path

root = Path(os.environ["CUTOVER_ROOT"])
source = Path(os.environ["SOURCE_SQLITE_PATH"]).resolve()
expected = (root / ".localstate" / "app.db").resolve()
if source != expected:
    raise SystemExit(f"SOURCE_SQLITE_PATH must be {expected}")
if not source.exists():
    raise SystemExit(f"SQLite source missing: {source}")
if source.name.lower() == "obd.sqlite":
    raise SystemExit("Refusing to back up obd.sqlite.")

backup_dir = root / ".localstate" / "backups"
backup_dir.mkdir(parents=True, exist_ok=True)
stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
backup = backup_dir / f"app-pre-postgres-cutover-{stamp}.db"

src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=10)
dst = sqlite3.connect(str(backup))
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()

def table_counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(f"integrity_check failed for {path}: {integrity}")
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return {row[0]: conn.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0] for row in rows}
    finally:
        conn.close()

source_counts = table_counts(source)
backup_counts = table_counts(backup)
if source_counts != backup_counts:
    raise SystemExit("Backup table counts do not match source table counts.")

print(f"PASS: Validated SQLite backup created: {backup}")
print("Set BACKUP_SQLITE_PATH to this exact file before migration.")
'@
    exit 0
}

if (-not $ExecuteMigration) {
    throw "Migration requires -ExecuteMigration."
}
if (-not $ConfirmedMaintenanceWindow -or -not $ConfirmedSQLiteWriteFreeze -or -not $ConfirmedBackupValidated) {
    throw "Migration requires -ConfirmedMaintenanceWindow -ConfirmedSQLiteWriteFreeze -ConfirmedBackupValidated."
}
if ([string]::IsNullOrWhiteSpace($env:TARGET_DATABASE_URL)) {
    throw "TARGET_DATABASE_URL is required."
}
if ([string]::IsNullOrWhiteSpace($env:BACKUP_SQLITE_PATH)) {
    throw "BACKUP_SQLITE_PATH is required and must point to the validated final backup."
}

Invoke-Python "final_migration" @'
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

root = Path(os.environ["CUTOVER_ROOT"])
source = Path(os.environ["SOURCE_SQLITE_PATH"]).resolve()
backup = Path(os.environ["BACKUP_SQLITE_PATH"]).resolve()
target_url = (os.environ.get("TARGET_DATABASE_URL") or "").strip()
target_state = os.environ["CUTOVER_TARGET_STATE"]
report_dir = root / ".localstate" / "postgres_cutover_reports"
report_dir.mkdir(parents=True, exist_ok=True)
report_path = report_dir / f"postgres-final-migration-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.txt"
lines = []

def log(message: str) -> None:
    print(message)
    lines.append(message)

def fail(message: str) -> None:
    log(f"FAIL: {message}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    raise SystemExit(1)

def sqlite_counts(path: Path, expected_source: bool = False) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        log(f"{path.name} integrity_check={integrity}")
        if integrity != "ok":
            fail(f"SQLite integrity failed for {path}")
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return {row[0]: conn.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0] for row in rows}
    finally:
        conn.close()

expected = (root / ".localstate" / "app.db").resolve()
if source != expected:
    fail(f"SOURCE_SQLITE_PATH must be {expected}")
if not source.exists():
    fail(f"SQLite source missing: {source}")
if source.name.lower() == "obd.sqlite":
    fail("Refusing to migrate obd.sqlite.")
if not backup.exists():
    fail(f"Backup file missing: {backup}")

parsed = urlparse(target_url)
kind = "postgresql" if parsed.scheme in {"postgres", "postgresql"} else (parsed.scheme or "unknown")
host = parsed.hostname or ""
port = f":{parsed.port}" if parsed.port else ""
dbname = parsed.path.lstrip("/") if parsed.path else ""
log(f"Target database type={kind}")
log(f"Target PostgreSQL host={host}{port}/{dbname}" if host else "Target PostgreSQL host=unavailable")
if kind != "postgresql":
    fail("TARGET_DATABASE_URL must be PostgreSQL.")
if host.lower().endswith("railway.internal"):
    fail("TARGET_DATABASE_URL uses railway.internal. Use DATABASE_PUBLIC_URL/TCP proxy for local migration.")

source_counts = sqlite_counts(source)
backup_counts = sqlite_counts(backup)
if source_counts != backup_counts:
    fail("Backup counts do not match source counts.")
log("Backup counts match source counts.")

os.environ["DATABASE_URL"] = target_url
from scripts import db_migration

pg = db_migration.pg_connect()
try:
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
            """
        )
        existing = cur.fetchall()
        for schema, table in existing:
            cur.execute(
                db_migration.psycopg_sql().SQL("SELECT COUNT(*) FROM {}.{}").format(
                    db_migration.psycopg_sql().Identifier(schema),
                    db_migration.psycopg_sql().Identifier(table),
                )
            )
            log(f"preexisting_postgres_table={schema}.{table} rows={cur.fetchone()[0]}")
finally:
    pg.close()

if target_state == "EMPTY_TARGET" and existing:
    fail("TargetState EMPTY_TARGET selected, but PostgreSQL has user tables.")

python = sys.executable
schema_cmd = [python, str(root / "scripts" / "db_migration.py"), "--sqlite-path", str(source), "migrate-schema"]
data_cmd = [python, str(root / "scripts" / "db_migration.py"), "--sqlite-path", str(source), "migrate-data"]
for label, cmd in [("schema", schema_cmd), ("data", data_cmd)]:
    log(f"Running {label} migration command with credentials redacted.")
    result = subprocess.run(cmd, cwd=root, env=os.environ.copy(), text=True, capture_output=True)
    if result.stdout:
        log(result.stdout.strip())
    if result.stderr:
        log(result.stderr.strip())
    if result.returncode != 0:
        fail(f"{label} migration failed with exit code {result.returncode}")

sqlite_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
sqlite_conn.row_factory = sqlite3.Row
pg = db_migration.pg_connect()
try:
    pg_sql = db_migration.psycopg_sql()
    tables = db_migration.sqlite_table_names(sqlite_conn)
    with pg.cursor() as cur:
        for table in tables:
            source_total = sqlite_conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            expected_rows = len(db_migration.sqlite_rows_for_migration(sqlite_conn, table))
            cur.execute(pg_sql.SQL("SELECT COUNT(*) FROM {}").format(pg_sql.Identifier(table)))
            pg_count = cur.fetchone()[0]
            skipped = source_total - expected_rows
            log(f"row_count table={table} sqlite_source={source_total} postgres={pg_count} skipped={skipped}")
            if expected_rows != pg_count:
                fail(f"Row-count mismatch for {table}")
            columns = sqlite_conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            if any(col["name"] == "id" for col in columns):
                cur.execute(
                    """
                    SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL)
                    FROM {table}
                    """.format(table=pg_sql.Identifier(table).as_string(cur)),
                    (table,),
                )
                log(f"sequence_repaired={table}.id")
        pg.commit()
finally:
    sqlite_conn.close()
    pg.close()

pg = db_migration.pg_connect()
try:
    pg_sql = db_migration.psycopg_sql()
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (is_identity = 'YES' OR column_default LIKE 'nextval%')
            ORDER BY table_name, column_name
            """
        )
        checked = 0
        skipped = 0
        for table, column in cur.fetchall():
            try:
                cur.execute(pg_sql.SQL("SELECT COALESCE(MAX({}), 0) FROM {}").format(pg_sql.Identifier(column), pg_sql.Identifier(table)))
                before = cur.fetchone()[0] or 0
                cur.execute(pg_sql.SQL("INSERT INTO {} DEFAULT VALUES RETURNING {}").format(pg_sql.Identifier(table), pg_sql.Identifier(column)))
                new_id = cur.fetchone()[0]
                if new_id <= before:
                    fail(f"Sequence check failed for {table}.{column}")
                checked += 1
                log(f"sequence_check={table}.{column} max_before={before} new_id={new_id} rolled_back=true")
                pg.rollback()
            except Exception as exc:
                pg.rollback()
                skipped += 1
                log(f"sequence_check_skipped={table}.{column} reason={type(exc).__name__}: {exc}")
        log(f"sequence_checks_checked={checked} skipped={skipped}")
finally:
    pg.close()

log("PASS: final migration completed. Railway variables were not changed.")
report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Migration report: {report_path}")
'@
