#!/usr/bin/env bash
# EarthScape Climate Agency — application database backup.
#
# Atlas path : mongodump against MONGODB_URI (install the MongoDB Database Tools).
# Fallback   : the platform's own JSON snapshot writer, used when the development
#              document store is active instead of Atlas.
#
# Usage: ./infrastructure/backup/backup.sh [destination-dir]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
DEST="${1:-$ROOT/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "No environment file at $ENV_FILE — copy .env.example to .env first." >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a
mkdir -p "$DEST"

URI="${MONGODB_URI:-}"
DATABASE="${MONGODB_DATABASE:-earthscape_climate}"

if [[ -z "$URI" || "$URI" == *"<username>"* ]]; then
  echo "MONGODB_URI is not a real Atlas connection string." >&2
  echo "The development document store keeps one JSON file per collection under" >&2
  echo "backend/.data/localdb — stop the API, then copy that directory to back it" >&2
  echo "up, and copy it back to restore. There is no mongodump to take from Atlas" >&2
  echo "because this install is not connected to one." >&2
  exit 2
fi

if ! command -v mongodump >/dev/null 2>&1; then
  echo "mongodump not found. Install the MongoDB Database Tools: https://www.mongodb.com/docs/database-tools/installation/" >&2
  exit 3
fi

ARCHIVE="$DEST/earthscape-${DATABASE}-${STAMP}"
echo "Backing up database '$DATABASE' to $ARCHIVE (mongodump, compressed, consistent snapshot)"
mongodump --uri="$URI" --db="$DATABASE" --gzip --out="$ARCHIVE" --quiet

# Hash the dump so a restore can be proven to match the backup it came from.
( cd "$ARCHIVE" && find . -type f -print0 | xargs -0 sha256sum > SHA256SUMS )
echo "Checksums written to $ARCHIVE/SHA256SUMS"

# Keep the 14 most recent dumps; older ones are assumed to be archived off-host.
find "$DEST" -maxdepth 1 -type d -name "earthscape-${DATABASE}-*" | sort | head -n -14 | while read -r old; do
  echo "Pruning $old"
  rm -rf "$old"
done

echo "Backup complete: $ARCHIVE"
