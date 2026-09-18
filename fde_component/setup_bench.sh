#!/usr/bin/env bash
# setup_bench.sh — Set up bench environment and run M2 tests for fde_component
# Run from the Codespace terminal: bash setup_bench.sh
set -euo pipefail

BENCH_DIR="/workspaces/FDE-component/fde_bench"
SITE="test.localhost"
MYSQL_ROOT_PW="test"
ADMIN_PW="admin"
APP_URL="https://github.com/muhammadfawad538/FDE-component.git"
APP_DIR="${BENCH_DIR}/apps/fde_component"

echo "========================================"
echo " Step 1/7: System dependencies"
echo "========================================"
sudo apt-get update -qq
sudo apt-get install -y -qq redis-server mariadb-server
echo "[OK] Dependencies installed"

echo ""
echo "========================================"
echo " Step 2/7: Start services"
echo "========================================"
redis-server --daemonize yes
sudo service mariadb start
sleep 2
mysqladmin -u root -p${MYSQL_ROOT_PW} ping >/dev/null 2>&1 || {
    echo "  Setting MySQL root password..."
    sudo mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '${MYSQL_ROOT_PW}'; FLUSH PRIVILEGES;"
}
echo "[OK] Redis and MariaDB running"

echo ""
echo "========================================"
echo " Step 3/7: Crontab workaround"
echo "========================================"
sudo bash -c 'echo -e "#!/bin/sh\nexit 0" > /usr/bin/crontab'
sudo chmod +x /usr/bin/crontab
echo "[OK] crontab stub in place"

echo ""
echo "========================================"
echo " Step 4/7: Install bench"
echo "========================================"
pip install frappe-bench -q
echo "[OK] bench installed"

echo ""
echo "========================================"
echo " Step 5/7: Initialize bench"
echo "========================================"
cd /workspaces/FDE-component
rm -rf fde_bench
bench init --skip-redis-config-generation fde_bench
cd "${BENCH_DIR}"
echo "[OK] bench initialized at ${BENCH_DIR}"

echo ""
echo "========================================"
echo " Step 6/7: Create site"
echo "========================================"
bench new-site "${SITE}" \
    --mariadb-root-password "${MYSQL_ROOT_PW}" \
    --admin-password "${ADMIN_PW}" \
    --force
echo "[OK] site ${SITE} created"

echo ""
echo "========================================"
echo " Step 7/7: Redis configs, app install, tests"
echo "========================================"

# Redis configs
cat > "${BENCH_DIR}/sites/${SITE}/redis_cache.conf" << 'RCONF'
bind 127.0.0.1
port 11000
timeout 0
save ""
maxmemory-policy allkeys-lru
RCONF

cat > "${BENCH_DIR}/sites/${SITE}/redis_socketio.conf" << 'RCONF'
bind 127.0.0.1
port 12000
timeout 0
save ""
maxmemory-policy allkeys-lru
RCONF

cat > "${BENCH_DIR}/sites/${SITE}/redis_queue.conf" << 'RCONF'
bind 127.0.0.1
port 13000
timeout 0
save ""
maxmemory-policy allkeys-lru
RCONF

# Start Redis instances
redis-server "${BENCH_DIR}/sites/${SITE}/redis_cache.conf" --daemonize yes
redis-server "${BENCH_DIR}/sites/${SITE}/redis_socketio.conf" --daemonize yes
redis-server "${BENCH_DIR}/sites/${SITE}/redis_queue.conf" --daemonize yes
echo "[OK] Redis instances started on 11000, 12000, 13000"

# Clone app
cd "${BENCH_DIR}/apps"
rm -rf fde_component
git clone "${APP_URL}" fde_component
echo "[OK] App cloned"

# Install app
bench --site "${SITE}" install-app fde_component
echo "[OK] App installed"

# Run tests
echo ""
echo "========================================"
echo " Running M2 tests"
echo "========================================"
bench --site "${SITE}" run-tests --app fde_component
echo ""
echo "[DONE] All steps completed"
