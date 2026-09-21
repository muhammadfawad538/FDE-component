# Single Dockerfile to run M2 tests for fde_component
# Build: docker build -t fde-component-test .
# Run:   docker run --rm fde-component-test
#
# Prerequisites: Docker Desktop installed and running

FROM frappe/bench:latest

USER root

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-mysql-client \
    redis-tools \
    && rm -rf /var/lib/apt/lists/*

USER frappe

# Copy app code into bench apps directory
COPY fde_component/ /home/frappe/frappe-bench/apps/fde_component/

# Set up test site and install app
# Note: app is already copied, so no need for `bench get-app`
RUN bench setup requirements && \
    bench new-site test.localhost \
        --mariadb-root-password root \
        --admin-password admin \
        --no-mq && \
    bench --site test.localhost install-app fde_component && \
    bench --site test.localhost migrate && \
    bench clear-cache

# Run M2 tests
CMD bash -c "\
    service mysql start && \
    service redis-server start && \
    sleep 5 && \
    bench --site test.localhost run-tests --app fde_component \
  "
