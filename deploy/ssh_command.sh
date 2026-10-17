#!/usr/bin/env bash
# Forced command of the GitHub Actions deploy key, one line in ~/.ssh/authorized_keys:
#   command="/home/ubuntu/signal-bot/deploy/ssh_command.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... github-deploy
# Whatever the client asks, the key can only run "deploy <40-char sha>" or "status".
set -euo pipefail

APP="$HOME/signal-bot"
cmd="${SSH_ORIGINAL_COMMAND:-}"
if [[ "$cmd" =~ ^deploy\ ([0-9a-f]{40})$ ]]; then
    sha="${BASH_REMATCH[1]}"
    mkdir -p "$APP/user_data/logs"
    out="$APP/user_data/logs/deploy-last.out"
    rc_file="$out.rc"
    rm -f "$rc_file"
    git -C "$APP" fetch --quiet origin main
    if ! git -C "$APP" merge-base --is-ancestor "$sha" origin/main; then
        echo "refused: $sha is not on origin/main"
        exit 2
    fi
    # the commit's own deploy.sh (a change to the deploy steps applies to the deploy that brings
    # it), run from a copy, detached from this SSH session: a dropped connection must not stop a
    # deploy halfway
    copy="$(mktemp /tmp/deploy.XXXXXX.sh)"
    git -C "$APP" show "$sha:deploy/deploy.sh" >"$copy"
    setsid nohup bash -c 'bash "$0" "$1"; echo $? > "$2"; rm -f "$0"' "$copy" "$sha" "$rc_file" \
        >"$out" 2>&1 </dev/null &
    tail --pid="$!" -n +1 -f "$out"
    exit "$(cat "$rc_file" 2>/dev/null || echo 1)"
elif [ "$cmd" = "status" ]; then
    cd "$APP"
    git log -1 --format='%h %cs %s'
    XDG_RUNTIME_DIR="/run/user/$(id -u)" systemctl --user --no-pager is-active \
        signal-forward signal-recorder || true
    tail -n 3 user_data/logs/deploys.log 2>/dev/null || true
else
    echo "refused: only 'deploy <sha>' or 'status'"
    exit 2
fi
