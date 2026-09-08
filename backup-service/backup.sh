#!/bin/sh
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${AWS_S3_BUCKET_NAME:?AWS_S3_BUCKET_NAME is required}"
: "${AWS_ENDPOINT_URL:?AWS_ENDPOINT_URL is required}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is required}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is required}"
: "${AWS_DEFAULT_REGION:?AWS_DEFAULT_REGION is required}"

BACKUP_PREFIX="${BACKUP_PREFIX:-torquemech-production}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
FILE="${BACKUP_PREFIX}-${STAMP}.dump"
CHECKSUM="${FILE}.sha256"
TMP_FILE="/tmp/${FILE}"
TMP_CHECKSUM="/tmp/${CHECKSUM}"
REMOTE_PREFIX="database/daily"

echo "[backup] Starting TorqueMech database backup at ${STAMP}"

pg_dump "$DATABASE_URL"   --format=custom   --no-owner   --no-acl   --file="$TMP_FILE"

pg_restore --list "$TMP_FILE" >/dev/null

sha256sum "$TMP_FILE" | awk '{print $1 "  '"$FILE"'"}' > "$TMP_CHECKSUM"

aws s3 cp "$TMP_FILE"   "s3://${AWS_S3_BUCKET_NAME}/${REMOTE_PREFIX}/${FILE}"   --endpoint-url "$AWS_ENDPOINT_URL"   --only-show-errors

aws s3 cp "$TMP_CHECKSUM"   "s3://${AWS_S3_BUCKET_NAME}/${REMOTE_PREFIX}/${CHECKSUM}"   --endpoint-url "$AWS_ENDPOINT_URL"   --only-show-errors

aws s3api head-object   --bucket "$AWS_S3_BUCKET_NAME"   --key "${REMOTE_PREFIX}/${FILE}"   --endpoint-url "$AWS_ENDPOINT_URL" >/dev/null

aws s3api head-object   --bucket "$AWS_S3_BUCKET_NAME"   --key "${REMOTE_PREFIX}/${CHECKSUM}"   --endpoint-url "$AWS_ENDPOINT_URL" >/dev/null

echo "[backup] Backup uploaded and verified."

CUTOFF="$(( $(date -u +%s) - (RETENTION_DAYS * 86400) ))"

aws s3api list-objects-v2   --bucket "$AWS_S3_BUCKET_NAME"   --prefix "${REMOTE_PREFIX}/"   --endpoint-url "$AWS_ENDPOINT_URL"   --output json |
jq -r --argjson cutoff "$CUTOFF" '
  .Contents[]? |
  select(((
.LastModified
| sub("\\.[0-9]+\\+00:00$"; "Z")
| sub("\\+00:00$"; "Z")
| sub("\\.[0-9]+Z$"; "Z")
| fromdateiso8601
) < $cutoff)) |
  .Key
' |
while IFS= read -r KEY; do
  [ -z "$KEY" ] && continue
  echo "[backup] Deleting expired object: ${KEY}"
  aws s3api delete-object     --bucket "$AWS_S3_BUCKET_NAME"     --key "$KEY"     --endpoint-url "$AWS_ENDPOINT_URL" >/dev/null
done

rm -f "$TMP_FILE" "$TMP_CHECKSUM"

echo "[backup] SUCCESS: ${FILE}"
