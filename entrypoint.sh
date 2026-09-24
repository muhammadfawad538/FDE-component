#!/bin/bash
set -euo pipefail

echo "=== Starting MariaDB ==="
mysqld --user=mysql --datadir=/var/lib/mysql --pid-file=/run/mysqld/mysqld.pid &
MYSQL_PID=$!

echo "=== Starting Redis ==="
redis-server --daemonize yes

# Wait for MariaDB to be ready
echo "=== Waiting for MariaDB ==="
for i in $(seq 1 30); do
    if mysqladmin ping -u root -proot --silent; then
        echo "MariaDB ready"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "MariaDB failed to start"
        exit 1
    fi
    sleep 2
done

# Wait for Redis to be ready
echo "=== Waiting for Redis ==="
for i in $(seq 1 30); do
    if redis-cli ping --silent; then
        echo "Redis ready"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "Redis failed to start"
        exit 1
    fi
    sleep 2
done

# Set up bench and create site
echo "=== Setting up bench ==="
cd /home/frappe/frappe-bench

# Only create site if it doesn't already exist
if [ ! -d "sites/test.localhost" ]; then
    echo "=== Creating test site ==="
    bench new-site test.localhost \
        --mariadb-root-password root \
        --admin-password admin
fi

# Install app if not already installed
if ! bench --site test.localhost list-apps | grep -q "fde_component"; then
    echo "=== Installing fde_component ==="
    bench --site test.localhost install-app fde_component
fi

# Run migrations
bench --site test.localhost migrate

# Run tests
echo "=== Running M2 tests ==="
bench --site test.localhost run-tests --app fde_component

# Exit code from tests
exit $?
