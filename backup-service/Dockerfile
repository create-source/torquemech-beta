FROM postgres:18-alpine

RUN apk add --no-cache aws-cli jq coreutils

COPY backup.sh /usr/local/bin/backup.sh
COPY restore-drill.sh /usr/local/bin/restore-drill.sh

RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/restore-drill.sh

CMD ["/usr/local/bin/backup.sh"]
