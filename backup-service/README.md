# TorqueMech Backup Service

Railway service variables required:

- DATABASE_URL
- AWS_S3_BUCKET_NAME
- AWS_ENDPOINT_URL
- AWS_ACCESS_KEY_ID
- AWS_SECRET_ACCESS_KEY
- AWS_DEFAULT_REGION

Recommended optional variables:

- BACKUP_PREFIX=torquemech-production
- RETENTION_DAYS=30

Recommended Railway cron schedule:

0 10 * * *

Railway cron uses UTC. This is approximately 3:00 AM PDT / 2:00 AM PST.

A successful run ends with:

[backup] SUCCESS: torquemech-production-....dump

Verify the bucket contains both the .dump file and matching .sha256 file.

Never restore a backup drill over production.
