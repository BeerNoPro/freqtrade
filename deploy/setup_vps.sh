#!/usr/bin/env bash
# One-time setup of the signal bot on a fresh Ubuntu 24.04 server, run as the bot account
# (e.g. ubuntu, with sudo) over SSH:
#     curl -fsSL https://raw.githubusercontent.com/BeerNoPro/freqtrade/main/deploy/setup_vps.sh | bash
# or, from a checkout: bash deploy/setup_vps.sh
#
# Sized for the smallest droplet (512 MB RAM, 10 GB SSD): installs git, sqlite3, rclone and uv,
# a 1 GB swap file (one memory peak at a time: the runner's cycle, the recorder's daily
# compaction at 01:00 UTC or the store sync at 01:30 UTC), a journald size cap and a firewall
# that only lets SSH in; clones the public repository (main, without old file contents) into
# ~/signal-bot; builds the Python 3.13 venv from requirements.txt without a wheel cache;
# installs the systemd user units and keeps them running without a login (linger). The runner
# and the recorder are enabled but NOT started: their private data (rules, store, journal,
# .env) is copied first, see docs/deploy-vps.md "Chuyển từ laptop".
set -euo pipefail

APP="$HOME/signal-bot"
REPO_URL="https://github.com/BeerNoPro/freqtrade.git"
UV_VERSION="0.10.9"
PYTHON="3.13"
export UV_NO_CACHE=1  # the cache would keep a second copy of every wheel on a 10 GB disk

echo "== packages"
sudo apt-get update -qq
sudo apt-get install -y -qq git sqlite3 rsync curl ufw rclone >/dev/null

echo "== swap (1 GB)"
if ! swapon --show | grep -q /swapfile; then
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== journald capped at 100 MB"
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=100M\n' | sudo tee /etc/systemd/journald.conf.d/size.conf >/dev/null
sudo systemctl restart systemd-journald

echo "== services a droplet never needs (modems, firmware updates): ~40 MB of RAM back"
sudo systemctl disable --now ModemManager.service >/dev/null 2>&1 || true
sudo systemctl mask --now fwupd.service >/dev/null 2>&1 || true

echo "== firewall: SSH only (the bot only makes outgoing connections)"
sudo ufw allow OpenSSH >/dev/null
sudo ufw --force enable >/dev/null

echo "== uv $UV_VERSION and Python $PYTHON"
if ! command -v uv >/dev/null || ! uv --version | grep -q "$UV_VERSION"; then
    curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
fi
export PATH="$HOME/.local/bin:$PATH"

echo "== repository (main) in $APP"
if [ ! -d "$APP/.git" ]; then
    # full commit history (deploy.sh compares commits), file contents fetched only when needed
    git clone --quiet --filter=blob:none --branch main "$REPO_URL" "$APP"
fi
cd "$APP"
mkdir -p user_data/logs user_data/research user_data/backups

echo "== venv"
uv python install "$PYTHON"
[ -x .venv/bin/python ] || uv venv --quiet --python "$PYTHON" .venv
uv pip install --quiet --python .venv/bin/python -r requirements.txt

echo "== systemd user units"
sudo loginctl enable-linger "$USER"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
mkdir -p "$HOME/.config/systemd/user"
cp deploy/systemd/*.service deploy/systemd/*.timer "$HOME/.config/systemd/user/"
chmod +x deploy/*.sh
systemctl --user daemon-reload
systemctl --user enable signal-forward.service signal-recorder.service >/dev/null
systemctl --user enable --now signal-backup.timer signal-audit.timer signal-offload.timer >/dev/null

echo
echo "Setup done. Next (docs/deploy-vps.md): copy .env and user_data/research from the laptop,"
echo "then: systemctl --user start signal-recorder signal-forward"
echo "Google Drive offload (optional, docs/deploy-vps.md section 5): rclone config -> remote 'gdrive'"
