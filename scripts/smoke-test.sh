#!/usr/bin/bash
# Installs the package into the active environment and exercises all five
# console scripts against a fixture database, to catch packaging/import
# regressions that unit-level checks might miss. Assumes `pip` on PATH
# points at the environment to test.
set -e

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Installing package from ${REPO_DIR}"
pip install --quiet "${REPO_DIR}"

echo "==> Creating fixture database and config"
python3 "${REPO_DIR}/scripts/make-fixture-db.py" "${WORKDIR}/readings.db"

cat > "${WORKDIR}/config.ini" <<EOF
[monitor]
address = AA:BB:CC:DD:EE:FF

[storage]
db_path = ${WORKDIR}/readings.db

[daemon]
log_level = INFO
EOF

echo "==> etekcity-bp-daemon"
etekcity-bp-daemon --version
etekcity-bp-daemon --help > /dev/null
etekcity-bp-daemon --config "${WORKDIR}/config.ini" --check-config

echo "==> etekcity-bp-report"
etekcity-bp-report --version
etekcity-bp-report --help > /dev/null
etekcity-bp-report --config "${WORKDIR}/config.ini" --output "${WORKDIR}/out.pdf"
test -s "${WORKDIR}/out.pdf"
etekcity-bp-report --config "${WORKDIR}/config.ini" --format csv --output "${WORKDIR}/out.csv"
grep -q "Date/Time" "${WORKDIR}/out.csv"

echo "==> etekcity-bp-prune"
etekcity-bp-prune --version
etekcity-bp-prune --help > /dev/null
etekcity-bp-prune --config "${WORKDIR}/config.ini" --older-than 9999 | grep -q "Would delete 0"

echo "==> etekcity-bp-alert-check"
etekcity-bp-alert-check --version
etekcity-bp-alert-check --help > /dev/null
etekcity-bp-alert-check --config "${WORKDIR}/config.ini" | grep -q "disabled"

echo "==> etekcity-bp-api"
etekcity-bp-api --version
etekcity-bp-api --help > /dev/null
etekcity-bp-api --config "${WORKDIR}/config.ini" | grep -q "disabled"

echo "==> Smoke test passed"
