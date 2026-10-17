#!/usr/bin/env bash
# Daily (signal-offload.timer, 03:45 UTC, after the backup): push to Google Drive with rclone
# (remote "gdrive", scope drive.file: rclone only sees the files it wrote) the daily backups and
# the recorder's compacted days. Recorder days older than KEEP_DAYS then leave the server: on a
# 10 GB disk the recorder's ~30 MB a day would fill it in about three months. "rclone move"
# checks each transfer before it deletes the local file. Without the remote it only logs the
# disk use. The laptop reads the same folder for research:
#     rclone copy gdrive:signal-bot/live user_data/research/live
set -euo pipefail

APP="${APP:-$HOME/signal-bot}"
REMOTE="${REMOTE:-gdrive}"
FOLDER="${FOLDER:-signal-bot}"
KEEP_DAYS="${KEEP_DAYS:-30}"                  # recorder days kept on the server
BACKUP_DAYS_DRIVE="${BACKUP_DAYS_DRIVE:-90}"  # daily backups kept on Drive
LIVE="$APP/user_data/research/live"
LOG="$APP/user_data/logs/offload.log"

disk() { df -h --output=avail,pcent / | tail -n 1 | xargs; }
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
mkdir -p "$(dirname "$LOG")"

if ! rclone listremotes 2>/dev/null | grep -qx "$REMOTE:"; then
    log "no rclone remote '$REMOTE': nothing pushed; disk free, used: $(disk)"
    exit 0
fi

rclone copy "$APP/user_data/backups" "$REMOTE:$FOLDER/backups" --include '*.tar.gz'
rclone delete "$REMOTE:$FOLDER/backups" --min-age "${BACKUP_DAYS_DRIVE}d"

days=(--include '*/????-??-??.parquet')
rclone copy "$LIVE" "$REMOTE:$FOLDER/live" "${days[@]}" --include 'meta/**' --include '_gaps.jsonl'
rclone move "$LIVE" "$REMOTE:$FOLDER/live" "${days[@]}" --min-age "${KEEP_DAYS}d"

log "pushed backups and recorder days to $REMOTE:$FOLDER, moved recorder days older than" \
    "${KEEP_DAYS}d off the server; disk free, used: $(disk)"
