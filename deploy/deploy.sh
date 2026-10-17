#!/usr/bin/env bash
# Deploy one commit of origin/main to this server. Called by the deploy job of
# .github/workflows/ci.yml over SSH (through deploy/ssh_command.sh) once the tests passed on a
# push to main, or by hand:
#     ~/signal-bot/deploy/deploy.sh <40-char commit sha>
#
# 1. The commit must be on origin/main.
# 2. Preflight on the NEW code in a temporary worktree (scripts/research/deploy_preflight.py):
#    rule fingerprints registered, private files present, Telegram set. If requirements.txt
#    changed, a fresh venv is built for it first. A failure leaves the running version as is.
# 3. Only the services whose code changed are restarted, at a safe moment: not in the 12 minutes
#    after a 4h close (the cycle runs then), not 01:25-03:10 UTC (daily store sync with
#    non-atomic parquet writes, audit), and only when the runner's last cycle has finished.
# 4. After the switch the runner must log a new end-of-cycle 'product:' line (it runs one cycle
#    at start) and the recorder must beat; otherwise the previous commit (and venv) comes back.
# Every run is appended to user_data/logs/deploys.log.
set -euo pipefail

APP="${APP:-$HOME/signal-bot}"
PYTHON_VERSION="3.13"
MAX_WAIT_MIN="${MAX_WAIT_MIN:-90}"
VERIFY_SEC="${VERIFY_SEC:-600}"
LOG="$APP/user_data/logs/deploys.log"
FWD_LOG="$APP/user_data/logs/forward.log"
BEAT="$APP/user_data/research/live/.heartbeat"
UNITS_DIR="$HOME/.config/systemd/user"
export PATH="$HOME/.local/bin:$PATH"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

WT=""  # temporary worktree of the new commit, removed on exit
cleanup() {
    if [ -n "$WT" ]; then
        git -C "$APP" worktree remove --force "$WT" >/dev/null 2>&1 || true
        rm -rf "$WT"
    fi
}
trap cleanup EXIT

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

# last runner cycle finished: its last 'store synced' line is followed by a 'product:' line
runner_idle() {
    tail -n 300 "$FWD_LOG" 2>/dev/null | grep -E ' INFO (store synced|product): ' | tail -n 1 \
        | grep -q ' INFO product: '
}

safe_now() {
    local h m hm
    h=$((10#$(date -u +%H)))
    m=$((10#$(date -u +%M)))
    hm=$((h * 60 + m))
    [ $(((h % 4) * 60 + m)) -ge 12 ] || return 1
    if [ "$hm" -ge 85 ] && [ "$hm" -lt 190 ]; then return 1; fi
    if [ "$FORWARD" = yes ] && systemctl --user is-active --quiet signal-forward; then
        runner_idle || return 1
    fi
    return 0
}

# which services run code changed between the two commits (file list in $1)
plan_services() {
    FORWARD=no RECORDER=no UNITS=no
    if grep -qE '^(scripts/research/|requirements\.txt)' <<<"$1"; then FORWARD=yes; fi
    local rec='^(scripts/research/(record_market\.py|mce/(recorder|universe|__init__)\.py)|requirements\.txt)'
    if grep -qE "$rec" <<<"$1"; then RECORDER=yes; fi
    if grep -qE '^deploy/systemd/' <<<"$1"; then UNITS=yes FORWARD=yes RECORDER=yes; fi
    return 0
}

restart_services() {
    if [ "$UNITS" = yes ]; then
        cp deploy/systemd/*.service deploy/systemd/*.timer "$UNITS_DIR/"
        systemctl --user daemon-reload
    fi
    if [ "$RECORDER" = yes ]; then systemctl --user restart signal-recorder; fi
    if [ "$FORWARD" = yes ]; then systemctl --user restart signal-forward; fi
}

verify() {
    local t0="$1" deadline last beat ok
    deadline=$(($(date -u +%s) + VERIFY_SEC))
    while [ "$(date -u +%s)" -lt "$deadline" ]; do
        ok=yes
        if [ "$FORWARD" = yes ]; then
            systemctl --user is-active --quiet signal-forward || ok=no
            last="$(grep ' INFO product: ' "$FWD_LOG" 2>/dev/null | tail -n 1 | cut -c1-19)"
            if [ -z "$last" ] || [ "$(date -u -d "$last" +%s)" -lt "$t0" ]; then ok=no; fi
        fi
        if [ "$RECORDER" = yes ]; then
            systemctl --user is-active --quiet signal-recorder || ok=no
            beat="$(cat "$BEAT" 2>/dev/null || echo 0)"
            [ $((beat / 1000)) -ge "$t0" ] || ok=no
        fi
        [ "$ok" = yes ] && return 0
        sleep 15
    done
    return 1
}

main() {
    local sha="${1:-}"
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "usage: deploy.sh <40-char commit sha>"; return 2; }
    cd "$APP"
    mkdir -p "$(dirname "$LOG")"
    exec 9>"$APP/user_data/logs/.deploy.lock"
    flock -n 9 || { echo "another deploy is running"; return 3; }

    git fetch --quiet origin main
    if ! git merge-base --is-ancestor "$sha" origin/main; then
        log "refused $sha: not on origin/main"
        return 2
    fi
    local old changed check_py venv_new=""
    old="$(git rev-parse HEAD)"
    if [ "$old" = "$sha" ]; then
        log "already at $sha"
        return 0
    fi
    changed="$(git diff --name-only "$old" "$sha")"
    plan_services "$changed"

    WT="$(mktemp -d)"
    git worktree add --quiet --detach "$WT" "$sha"
    check_py="$APP/.venv/bin/python"
    if grep -qx 'requirements.txt' <<<"$changed"; then
        venv_new="$APP/.venv-next"
        rm -rf "$venv_new"
        uv venv --quiet --python "$PYTHON_VERSION" "$venv_new"
        uv pip install --quiet --python "$venv_new/bin/python" -r "$WT/requirements.txt"
        check_py="$venv_new/bin/python"
    fi
    if ! (cd "$WT/scripts/research" && "$check_py" deploy_preflight.py \
        --research "$APP/user_data/research" --env-file "$APP/.env"); then
        log "preflight failed for $sha: the server keeps $old"
        return 1
    fi

    local waited=0
    until safe_now; do
        if [ "$waited" -ge "$MAX_WAIT_MIN" ]; then
            log "no safe moment within $MAX_WAIT_MIN min for $sha: the server keeps $old"
            return 1
        fi
        sleep 60
        waited=$((waited + 1))
    done

    log "deploying $sha (was $old): forward=$FORWARD recorder=$RECORDER units=$UNITS deps=${venv_new:+new}"
    git checkout --quiet --detach "$sha"
    if [ -n "$venv_new" ]; then
        rm -rf "$APP/.venv-prev"
        mv "$APP/.venv" "$APP/.venv-prev"
        mv "$venv_new" "$APP/.venv"
    fi
    local t0
    t0="$(date -u +%s)"
    restart_services
    if [ "$FORWARD" = no ] && [ "$RECORDER" = no ]; then
        log "deployed $sha (no service code changed, nothing restarted)"
        return 0
    fi
    if verify "$t0"; then
        log "deployed $sha"
        return 0
    fi

    log "verify failed for $sha within ${VERIFY_SEC}s: rolling back to $old"
    git checkout --quiet --detach "$old"
    if [ -n "$venv_new" ]; then
        rm -rf "$APP/.venv-failed"
        mv "$APP/.venv" "$APP/.venv-failed"
        mv "$APP/.venv-prev" "$APP/.venv"
    fi
    restart_services
    return 1
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then  # not when sourced by tests/research/test_deploy_sh.py
    main "$@"
    exit $?
fi
