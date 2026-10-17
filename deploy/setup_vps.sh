#!/usr/bin/env bash
# One-time setup of the signal bot on a fresh Ubuntu 24.04 server, run as the bot account
# (e.g. ubuntu, with sudo) over SSH:
#     curl -fsSL https://raw.githubusercontent.com/BeerNoPro/freqtrade/main/deploy/setup_vps.sh | bash
# or, from a checkout: bash deploy/setup_vps.sh
#
# Installs git, sqlite3 and uv, a 2 GB swap file, a journald size cap and a firewall that only
# lets SSH in; clones the public repository (main) into ~/signal-bot; builds the Python 3.13
# venv from requirements.txt; installs the systemd user units and keeps them running without a
# login (linger). The runner and the recorder are enabled but NOT started: their private data
# (rules, store, journal, .env) is copied first, see docs/deploy-vps.md "Chuyển từ laptop".
set -euo pipefail

APP="$HOME/signal-bot"
REPO_URL="https://github.com/BeerNoPro/freqtrade.git"
UV_VERSION="0.10.9"
PYTHON="3.13"

echo "== packages"
sudo apt-get update -qq
sudo apt-get install -y -qq git sqlite3 rsync curl ufw >/dev/null

echo "== swap (2 GB) for the daily store sync next to the runner and the recorder"
if ! swapon --show | grep -q /swapfile; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== journald capped at 200 MB"
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\n' | sudo tee /etc/systemd/journald.conf.d/size.conf >/dev/null
sudo systemctl restart systemd-journald

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
    git clone --quiet --branch main "$REPO_URL" "$APP"
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
systemctl --user enable --now signal-backup.timer signal-audit.timer >/dev/null

echo
echo "Setup done. Next (docs/deploy-vps.md): copy .env and user_data/research from the laptop,"
echo "then: systemctl --user start signal-recorder signal-forward"
