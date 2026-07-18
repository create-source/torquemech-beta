# TorqueMech PostgreSQL Migration Runbook

## Architecture Summary

TorqueMech still uses raw SQL and `sqlite3`-style connection calls. The least disruptive launch-safe approach is a compatibility connection layer in `db.py`: local development keeps using `.localstate/app.db`, while production uses `DATABASE_URL` when it is set. PostgreSQL startup does not create missing tables; schema must be applied with the explicit migration script first.

## SQLite Dependencies Found

- `main.py`: app DB path constants, `app_db_conn`, startup schema creation, feedback/metrics/shop/profile/CRM schema creation, auth/password reset use, OBD local SQLite database.
- `routers/pro.py`: Pro CRM connection factory, auth tables, customers, vehicles, estimates/jobs, repairs/findings, approvals/logs, parts, invoices, maintenance, calendar/bookings, shop/account settings, and many `PRAGMA table_info`, `sqlite_master`, `AUTOINCREMENT`, `INSERT OR IGNORE`, and `lastrowid` patterns.
- `routers/knowledge.py`: separate local knowledge SQLite database at `.localstate/app.db`/fallback with SQLite PRAGMAs and `AUTOINCREMENT`.
- `scripts/import_diagnostics.py`: imports diagnostics into SQLite.
- `scripts/migrate_visual_references.py`: SQLite-only visual reference schema migration.
- Tests under `tests/`: SQLite in-memory fixtures and SQLite schema assertions.
- `main.py` OBD lookup: intentionally separate local SQLite database `.localstate/obd.sqlite`; not part of the app account/CRM migration.

## Environment Variables

- `DATABASE_URL`: Railway PostgreSQL URL. Both `postgres://` and `postgresql://` are accepted.
- `PGSSLMODE`: optional; defaults to `require` for PostgreSQL URLs.
- `TORQUEMECH_ALLOW_RUNTIME_SCHEMA`: internal migration escape hatch. Use only for controlled schema tooling, not normal app startup.

## Railway Setup

1. Create a Railway PostgreSQL service.
2. Copy the service `DATABASE_URL`.
3. Add `DATABASE_URL` to the TorqueMech web service variables.
4. Do not deploy/start the app against the new database until `migrate-schema` has completed.
5. Keep the SQLite file untouched until the PostgreSQL data migration has been verified.

## Schema Migration

```powershell
$env:DATABASE_URL = "postgresql://USER:PASSWORD:HOST:PORT/DB"
python scripts/db_migration.py --sqlite-path .localstate/app.db backup-sqlite --backup-dir backups
python scripts/db_migration.py --sqlite-path .localstate/app.db migrate-schema
```

The schema command creates PostgreSQL tables/indexes from the existing SQLite schema and adds nullable account fields if missing:

- `trial_started_at`
- `trial_ends_at`
- `subscription_status`
- `stripe_customer_id`
- `stripe_subscription_id`
- `subscription_current_period_end`
- `subscription_cancel_at_period_end`

## Data Migration

```powershell
$env:DATABASE_URL = "postgresql://USER:PASSWORD:HOST:PORT/DB"
python scripts/db_migration.py --sqlite-path .localstate/app.db migrate-data
```

The data command aborts if a destination table already contains rows. Use `--append` only after manually confirming duplicates cannot be created.

## Rollback And Backup

1. Before migration, run `backup-sqlite`; it creates a timestamped copy and leaves the original SQLite database in place.
2. For app rollback, remove/unset `DATABASE_URL` and restart the app; local SQLite fallback will be used.
3. For PostgreSQL rollback before launch, drop/recreate the Railway database or restore from a Railway backup/snapshot.
4. Do not delete `.localstate/app.db` or `.localstate/obd.sqlite`.

## PostgreSQL Notes

- Railway SSL is handled by URL normalization in `db.py`; `sslmode=require` is added if absent.
- Schema creation and data copy are separate commands.
- Runtime PostgreSQL schema creation is blocked by default so incomplete tables are not silently created during startup.
- Raw SQL remains in place; a full SQLAlchemy ORM conversion is deferred because it would be larger and riskier than needed for this launch phase.

## Unresolved Risks

- `routers/knowledge.py`, diagnostics import scripts, and OBD SQLite remain SQLite-specific by design/current scope.
- PostgreSQL behavior still needs verification against a live Railway database.
- The app has many raw SQL statements. The adapter covers common SQLite patterns found in the app, but a full live PostgreSQL route sweep is still required before launch.
