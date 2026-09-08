#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 /path/to/backup.dump"
  exit 2
fi

: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL is required and MUST be a non-production database}"

DUMP="$1"

if [ ! -f "$DUMP" ]; then
  echo "Backup file not found: $DUMP"
  exit 2
fi

pg_restore --list "$DUMP" >/dev/null

pg_restore   --dbname="$RESTORE_DATABASE_URL"   --clean   --if-exists   --no-owner   --no-acl   --exit-on-error   "$DUMP"

psql "$RESTORE_DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
SELECT current_database() AS restored_database;
SELECT COUNT(*) AS public_tables
FROM information_schema.tables
WHERE table_schema = 'public';
SQL

echo "[restore] SUCCESS"
