#!/usr/bin/env bash
# Daily backup (signal-backup.timer, 03:30 UTC): consistent copies of the forward-test journal
# and the risk book (sqlite .backup is safe while the runner writes), the message log and the
# private rule files, packed as user_data/backups/<UTC date>.tar.gz; 14 days kept. The laptop
# pulls one with deploy/pull_backup.ps1.
set -euo pipefail

APP="${APP:-$HOME/signal-bot}"
R="$APP/user_data/research"
DIR="$APP/user_data/backups"
DAY="$(date -u +%F)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$DIR" "$WORK/$DAY"
for db in journal product; do
    sqlite3 "$R/forward/$db.sqlite" ".backup '$WORK/$DAY/$db.sqlite'"
    check="$(sqlite3 "$WORK/$DAY/$db.sqlite" 'PRAGMA integrity_check')"
    [ "$check" = "ok" ] || { echo "backup: $db integrity check failed: $check" >&2; exit 1; }
done
cp -p "$R/forward/messages.log" "$WORK/$DAY/" 2>/dev/null || true
for f in product.json thresholds.json fingerprints.jsonl audit_known.csv "$R"/*.py; do
    src="$f"; [ -e "$src" ] || src="$R/$f"
    [ -e "$src" ] && cp -p "$src" "$WORK/$DAY/"
done
tar -czf "$DIR/$DAY.tar.gz" -C "$WORK" "$DAY"
find "$DIR" -name '*.tar.gz' -mtime +14 -delete
echo "backup $DIR/$DAY.tar.gz $(du -h "$DIR/$DAY.tar.gz" | cut -f1)"
