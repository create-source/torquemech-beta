# PostgreSQL Production Cutover Runbook

This runbook prepares a controlled cutover from the current Railway SQLite deployment to Railway PostgreSQL. It is preparation only until the maintenance window is explicitly approved.

## 1. Preconditions

- The completed PostgreSQL validation against a separate fresh Railway PostgreSQL test database must remain recorded as passed.
- The production cutover target PostgreSQL service must be explicitly named by the operator before any migration.
- The production web service must not be restarted, redeployed, or have variables changed during preparation.
- `.localstate/app.db` is the only application SQLite source.
- `.localstate/obd.sqlite` remains a separate OBD database and must never be migrated.
- The cutover scripts must receive `SOURCE_SQLITE_PATH` and `TARGET_DATABASE_URL` from the operator environment.
- Do not use `postgres.railway.internal` from local Windows. Use the Railway public TCP proxy URL for local cutover tooling.

## 2. Maintenance-Window Procedure

1. Announce a write freeze for estimates, Pro CRM work, bookings, invoices, and account/profile changes.
2. Confirm no users are actively writing to the app.
3. Run `scripts/postgres_cutover_preflight.ps1` against the selected PostgreSQL target.
4. Capture the preflight output.
5. Create and validate the final SQLite backup using `scripts/postgres_final_migration.ps1 -CreateValidatedBackup`.
6. Run the final migration only after explicit approval and only with all confirmation switches.
7. Compare row counts and sequence checks.
8. Change the Railway web service `DATABASE_URL` only after migration report PASS.
9. Restart or redeploy the Railway web service.
10. Run `scripts/postgres_post_cutover_validation.ps1`.
11. Keep the SQLite backup and original SQLite file unchanged.

## 3. Final SQLite Backup Procedure

Create a consistency-safe backup with SQLite's backup API, not a raw file copy:

```powershell
$env:SOURCE_SQLITE_PATH = "C:\path\to\.localstate\app.db"
.\scripts\postgres_final_migration.ps1 -CreateValidatedBackup
```

Expected backup path format:

```text
.localstate/backups/app-pre-postgres-cutover-YYYYMMDD-HHMMSS.db
```

The script must:

- Open the source SQLite database read-only.
- Use SQLite's backup API.
- Run `PRAGMA integrity_check` on the backup.
- Compare every source table count against the backup.
- Abort on any mismatch.
- Exclude `.localstate/obd.sqlite`.

## 4. SQLite Write-Freeze Procedure

- Put the app into maintenance mode or otherwise prevent user writes.
- Confirm no active browser session is writing customer, vehicle, estimate, invoice, booking, account, or profile data.
- Confirm the production process has stopped writing to `.localstate/app.db`.
- Do not delete, rename, replace, truncate, or move `.localstate/app.db`.
- Do not touch `.localstate/obd.sqlite`.

## 5. PostgreSQL Target Verification

Before migration, verify in Railway:

- Which Postgres service will become production.
- Whether that service already contains migrated test data.
- Whether the production web service currently has `DATABASE_URL`.
- Whether `DATABASE_URL` currently points to SQLite behavior by being absent, or PostgreSQL behavior by being set to `postgres://` or `postgresql://`.
- Whether the production web service uses a persistent Railway volume for `.localstate/app.db`.
- The exact variable name expected by the app is `DATABASE_URL`.
- `db.py` normalizes Railway `postgres://` URLs to `postgresql://`.
- `db.py` adds `sslmode=require` unless an SSL mode is already present.

Do not assume the existing Railway Postgres service is the production target. Require explicit confirmation.

## 6. Final Migration Procedure

After preflight and backup validation:

```powershell
$env:SOURCE_SQLITE_PATH = "C:\path\to\.localstate\app.db"
$env:TARGET_DATABASE_URL = "postgresql://REDACTED"
$env:BACKUP_SQLITE_PATH = "C:\path\to\.localstate\backups\app-pre-postgres-cutover-YYYYMMDD-HHMMSS.db"
.\scripts\postgres_final_migration.ps1 -ExecuteMigration -ConfirmedMaintenanceWindow -ConfirmedSQLiteWriteFreeze -ConfirmedBackupValidated -TargetState EMPTY_TARGET
```

The script must:

- Re-run source and backup integrity checks.
- Re-inspect PostgreSQL before migration.
- Refuse unexpected non-empty targets.
- Run `scripts/db_migration.py migrate-schema`.
- Run `scripts/db_migration.py migrate-data`.
- Abort immediately on failure.
- Write a timestamped report under `.localstate/postgres_cutover_reports/`.

## 7. Source/Destination Row-Count Comparison

Every migrated table must be compared:

- SQLite source count.
- Expected migrated count.
- PostgreSQL destination count.
- Skipped rows and reason, including filtered visual-reference orphan rows.

Any mismatch is a stop condition.

## 8. PostgreSQL Sequence Validation

After data migration:

- Repair identity/sequence values using `setval` for each migrated `id` column.
- Perform transactional insert-and-rollback checks where `DEFAULT VALUES` is safe.
- Roll back all sequence test inserts.
- Record checked and skipped identity columns in the report.

## 9. Railway Web-Service Variable Change

Do not change Railway variables until the final migration report passes and the operator explicitly approves cutover.

Cutover variable:

- Set the production web service `DATABASE_URL` to the approved production PostgreSQL URL.
- Do not paste or print the full URL in logs or chat.
- Do not set the app to the test PostgreSQL URL.

## 10. Railway Restart/Redeploy Procedure

After the variable change:

1. Restart or redeploy the Railway web service.
2. Confirm startup succeeds.
3. Confirm logs do not show PostgreSQL, SQL, schema, or HTTP 500 errors.
4. Run production smoke tests.

## 11. Production Smoke Tests

Run:

```powershell
$env:PRODUCTION_BASE_URL = "https://production.example"
.\scripts\postgres_post_cutover_validation.ps1
```

Validate:

- Homepage.
- Estimator.
- Login.
- Pro dashboard.
- Customer list.
- Customer detail.
- Vehicle detail.
- Service catalogs.
- `/api/service/ball_joint_replacement_each`.
- Parts-source lookup.
- Known valid estimate calculation payload.

Authentication redirects are acceptable for protected pages and must be reported separately from failures.

## 12. Rollback Decision Criteria

Rollback if any of these occur and cannot be fixed quickly inside the maintenance window:

- App fails startup.
- Core pages return HTTP 500.
- Login/session handling fails.
- Pro customer/vehicle workflows fail broadly.
- PostgreSQL row counts are mismatched.
- PostgreSQL errors appear in production logs.
- The production URL points to the wrong PostgreSQL service.

## 13. Rollback Procedure

Use `scripts/postgres_rollback_instructions.ps1` to print the checklist. It does not change Railway variables.

Rollback must:

1. Stop or place the web service into maintenance mode.
2. Restore the previous SQLite configuration by removing/reverting the production `DATABASE_URL` variable.
3. Confirm the persistent SQLite file still exists.
4. Restart the service.
5. Validate core pages.
6. Preserve PostgreSQL unchanged for diagnosis.
7. Never reverse-copy PostgreSQL data into SQLite automatically.
8. Document any writes made after PostgreSQL cutover, because rollback may lose those writes.

## 14. Post-Cutover Monitoring

For the first 24 hours:

- Watch HTTP 500s.
- Watch PostgreSQL connection errors.
- Watch login/session errors.
- Watch estimate calculation and PDF generation.
- Watch Pro customer, vehicle, finding, invoice, booking, and appointment routes.
- Verify no unexpected SQLite app writes occur.
- Keep the final SQLite backup and original SQLite file unchanged.

## 15. Cleanup Tasks After At Least 7 Days

Wait at least 7 days before any cleanup:

- Do not delete `.localstate/app.db`.
- Do not delete the final backup.
- Do not delete `.localstate/obd.sqlite`.
- Archive cutover reports.
- Confirm PostgreSQL backups/snapshots are configured.
- Decide whether obsolete SQLite compatibility artifacts can be retired in a separate reviewed change.

## Production Target Decision Checklist

Before cutover, verify in Railway:

- Which Postgres service will become production.
- Whether that service already contains migrated test data.
- Whether the production web service currently has `DATABASE_URL`.
- Whether it currently points to SQLite behavior or PostgreSQL behavior.
- Whether the web service uses a persistent Railway volume for `.localstate/app.db`.
- The app expects `DATABASE_URL`.
- `db.py` normalizes `postgres://` to `postgresql://`.
- `db.py` defaults PostgreSQL SSL to `sslmode=require`.

No final Railway variable-change command is included in this preparation runbook. Wait for a successful preflight report and explicit approval.
