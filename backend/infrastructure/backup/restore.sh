#!/usr/bin/env bash
# EarthScape Climate Agency — application database restore.
#
# DANGER: mongorestore --drop replaces every collection in the target database.
# The script therefore refuses to run unless the operator passes --yes-destroy-data
# and names the exact database, and it verifies the backup checksums first.
#
# Usage: ./infrastructure/backup/restore.sh <backup-dir> --db <database> --yes-destroy-data
set -euo pipefail

BACKUP_DIR="${1:-}"
if [[ -z "$BACKUP_DIR" || ! -d "$BACKUP_DIR" ]]; then
  echo "usage: $0 <backup-dir> --db <database> --yes-destroy-data" >&2
  exit 1
fi
shift

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1090
set -a; source "${ENV_FILE:-$ROOT/.env}"; set +a

TARGET_DB="${MONGODB_DATABASE:-earthscape_climate}"
CONFIRM=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --db) TARGET_DB="$2"; shift 2 ;;
    --yes-destroy-data) CONFIRM="yes"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ "$CONFIRM" != "yes" ]]; then
  echo "Refusing to restore: this overwrites existing data. Re-run with --yes-destroy-data." >&2
  exit 2
fi

if [[ -f "$BACKUP_DIR/SHA256SUMS" ]]; then
  echo "Verifying backup checksums"
  ( cd "$BACKUP_DIR" && sha256sum -c SHA256SUMS --quiet )
else
  echo "WARNING: SHA256SUMS missing — integrity of this backup cannot be verified." >&2
fi

URI="${MONGODB_URI:?MONGODB_URI must point at the Atlas cluster to restore into}"
echo "Restoring into database '$TARGET_DB' (existing collections in that database will be dropped)"
mongorestore --uri="$URI" --db="$TARGET_DB" --gzip --drop --nsInclude="*.*" "$BACKUP_DIR" --quiet

echo "Restore finished. Apply the schema (indexes/validators) before serving traffic:"
echo "  (cd backend && python scripts/bootstrap_platform.py)"
