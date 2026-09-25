#!/usr/bin/env bash
# setup_bench.sh — Reproduce the GitHub Actions CI test environment locally.
# Run from the repo root: bash fde_component/setup_bench.sh
# Requires: Python 3.14, MariaDB, Redis
set -euo pipefail

SITE="test.localhost"
MYSQL_ROOT_PW="test"
ADMIN_PW="admin"
FRAPPE_COMMIT="012667b9c4e7f66d5e1ff5858d2e922331d4300a"

# Verify Python version matches CI
PYTHON_BIN="python3"
if command -v python3.14 &>/dev/null; then
    PYTHON_BIN="python3.14"
elif [ "$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1-2)" != "3.14" ]; then
    echo "WARNING: CI uses Python 3.14, but system python3 is $(python3 --version 2>&1 | cut -d' ' -f2)"
fi

echo "========================================"
echo " Step 1/6: System dependencies"
echo "========================================"
sudo apt-get update -qq
sudo apt-get install -y -qq redis-server mariadb-server
echo "[OK] Dependencies installed"

echo ""
echo "========================================"
echo " Step 2/6: Start services"
echo "========================================"
redis-server --daemonize yes
sudo service mariadb start 2>/dev/null || sudo mysqld --user=root --datadir=/var/lib/mysql &
sleep 2
mysqladmin -u root -p${MYSQL_ROOT_PW} ping >/dev/null 2>&1 || {
    echo "  Setting MySQL root password..."
    sudo mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '${MYSQL_ROOT_PW}'; FLUSH PRIVILEGES;"
}
echo "[OK] Redis and MariaDB running"

echo ""
echo "========================================"
echo " Step 3/6: MariaDB root auth"
echo "========================================"
sudo mysql -u root <<'SQL' 2>/dev/null || true
CREATE USER IF NOT EXISTS 'test'@'localhost' IDENTIFIED BY 'test';
GRANT ALL PRIVILEGES ON *.* TO 'test'@'localhost' WITH GRANT OPTION;
FLUSH PRIVILEGES;
SQL
echo "[OK] MariaDB auth configured"

echo ""
echo "========================================"
echo " Step 4/6: Install bench"
echo "========================================"
pip install -q "click~=8.2.0" frappe-bench
echo "[OK] bench installed"

echo ""
echo "========================================"
echo " Step 5/6: Set up bench (matches CI exactly)"
echo "========================================"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

mkdir -p fde_bench/sites fde_bench/assets
git clone https://github.com/frappe/frappe.git fde_bench/apps/frappe
cd fde_bench/apps/frappe
git checkout ${FRAPPE_COMMIT}
cd "${REPO_DIR}"

python3 -m venv fde_bench/env
source fde_bench/env/bin/activate
pip install -q -e fde_bench/apps/frappe

cp -r "${REPO_DIR}/fde_component" fde_bench/apps/fde_component

# Frappe 16.35.0 compatibility patch for Python 3.14
# The `if key in v:` check in frappe/utils/__init__.py:356
# triggers incorrect __contains__ resolution under Python 3.14.
# Guarded: fails clearly if the expected line is absent or changed.
python3 -c "
import sys
target = 'fde_bench/apps/frappe/frappe/utils/__init__.py'
with open(target) as f:
    lines = f.readlines()
found = False
for i, line in enumerate(lines):
    if line.strip() == 'if key in v:':
        lines[i] = 'if hasattr(v, \"__contains__\") and key in v:\n'
        found = True
        break
if not found:
    sys.exit('ERROR: Expected line \"if key in v:\" not found in ' + target + ' — monkeypatch aborted')
with open(target, 'w') as f:
    f.writelines(lines)
print('Applied Python 3.14 compatibility patch to frappe/utils/__init__.py')
"

echo ""
echo "========================================"
echo " Step 6/6: Create site and run tests"
echo "========================================"
for i in $(seq 1 30); do
    mysqladmin ping -h 127.0.0.1 -u root -p${MYSQL_ROOT_PW} --silent && break
    echo "Waiting for MariaDB... ($i)"
    sleep 2
done

bench new-site ${SITE} --mariadb-root-password ${MYSQL_ROOT_PW} --admin-password ${ADMIN_PW} --force
bench --site ${SITE} install-app fde_component
bench --site ${SITE} run-tests --app fde_component

echo ""
echo "[DONE] All steps completed"
