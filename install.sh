#!/usr/bin/bash
# Installs etekcity-bp-daemon: creates a venv, installs the package from
# this checkout, seeds the config, creates the service user, and installs
# and enables the systemd unit. Re-running is safe: it skips steps that are
# already done (existing config, existing user) and upgrades the rest.
set -e

if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must be run as root (e.g. with sudo)." >&2
    exit 1
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    echo "Usage: sudo ./install.sh"
    echo "Installs etekcity-bp-daemon as a systemd service. No options."
    exit 0
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/etekcity-bp-daemon"
CONFIG_DIR="/etc/etekcity-bp-daemon"
SERVICE_USER="etekcity-bp-daemon"

echo "==> Creating virtual environment at ${INSTALL_DIR}/venv"
python3 -m venv "${INSTALL_DIR}/venv"

echo "==> Installing etekcity-bp-daemon from ${REPO_DIR}"
"${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install --quiet "${REPO_DIR}"

echo "==> Linking commands into /usr/bin"
ln -sf "${INSTALL_DIR}/venv/bin/etekcity-bp-daemon" /usr/bin/etekcity-bp-daemon
ln -sf "${INSTALL_DIR}/venv/bin/etekcity-bp-report" /usr/bin/etekcity-bp-report
ln -sf "${INSTALL_DIR}/venv/bin/etekcity-bp-prune" /usr/bin/etekcity-bp-prune
ln -sf "${INSTALL_DIR}/venv/bin/etekcity-bp-alert-check" /usr/bin/etekcity-bp-alert-check
ln -sf "${INSTALL_DIR}/venv/bin/etekcity-bp-api" /usr/bin/etekcity-bp-api
cp "${REPO_DIR}/scripts/generate-scheduled-report.sh" "${INSTALL_DIR}/generate-scheduled-report.sh"
chmod +x "${INSTALL_DIR}/generate-scheduled-report.sh"
ln -sf "${INSTALL_DIR}/generate-scheduled-report.sh" /usr/bin/etekcity-bp-generate-report

echo "==> Creating service user"
if ! id "${SERVICE_USER}" &>/dev/null; then
    useradd --system --no-create-home --group "${SERVICE_USER}"
fi

echo "==> Seeding config"
mkdir -p "${CONFIG_DIR}"
if [[ -f "${CONFIG_DIR}/config.ini" ]]; then
    echo "    ${CONFIG_DIR}/config.ini already exists, leaving its contents as-is."
else
    cp "${REPO_DIR}/config/etekcity-bp-daemon.ini.example" "${CONFIG_DIR}/config.ini"
    echo "    Wrote ${CONFIG_DIR}/config.ini -- edit it before (or after) starting the service."
fi
# The config can hold real secrets (ntfy/API tokens, apprise_urls with
# embedded credentials), so it's only readable by the service account --
# applied every run, not just on first write, in case it was ever loosened.
chown "${SERVICE_USER}:${SERVICE_USER}" "${CONFIG_DIR}/config.ini"
chmod 600 "${CONFIG_DIR}/config.ini"

echo "==> Installing systemd units"
cp "${REPO_DIR}/systemd/etekcity-bp-daemon.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/etekcity-bp-report-generate.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/etekcity-bp-report-generate.timer" /etc/systemd/system/
cp "${REPO_DIR}/systemd/etekcity-bp-alert-check.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/etekcity-bp-alert-check.timer" /etc/systemd/system/
cp "${REPO_DIR}/systemd/etekcity-bp-api.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now etekcity-bp-daemon

echo "==> Done. Edit ${CONFIG_DIR}/config.ini if you haven't, then watch discovery with:"
echo "        journalctl -u etekcity-bp-daemon -f"
echo "==> Since the config is now owned by ${SERVICE_USER} (mode 600), running the CLI"
echo "    tools by hand needs sudo -u, e.g.:"
echo "        sudo -u ${SERVICE_USER} etekcity-bp-report --config ${CONFIG_DIR}/config.ini"
echo "==> Scheduled report generation, alert checking, and the HTTP API are installed"
echo "    but not enabled (opt-in). To turn them on:"
echo "        sudo systemctl enable --now etekcity-bp-report-generate.timer"
echo "        sudo systemctl enable --now etekcity-bp-alert-check.timer"
echo "        sudo systemctl enable --now etekcity-bp-api.service"
