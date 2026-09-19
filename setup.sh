#!/bin/bash
set -e

echo "========================================"
echo " Setting up Frappe bench"
echo "========================================"

cd /home/frappe/bench

# Initialize bench if not done
if [ ! -f "sites/common_site_config.json" ]; then
    echo "Initializing bench..."
    bench init --skip-redis-config-generation /home/frappe/bench
fi

# Add app to bench
if ! grep -q "fde_component" apps.txt 2>/dev/null; then
    echo "Adding fde_component to bench apps..."
    bench get-app fde_component /home/frappe/bench/apps/fde_component
fi

# Create site if not exists
if [ ! -d "sites/test.localhost" ]; then
    echo "Creating site..."
    bench new-site test.localhost \
        --mariadb-root-username test \
        --mariadb-root-password test \
        --admin-password admin \
        --force
fi

# Install app
echo "Installing fde_component..."
bench --site test.localhost install-app fde_component

# Run tests
echo ""
echo "========================================"
echo " Running M2 tests"
echo "========================================"
bench --site test.localhost run-tests --app fde_component

echo ""
echo "[DONE] All steps completed"
