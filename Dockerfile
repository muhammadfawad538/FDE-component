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

# Install app and set up test site
RUN bench setup requirements && \
    bench get-app fde_component && \
    bench new-site test.localhost \
        --mariadb-root-password root \
        --admin-password admin \
        --no-mq && \
    bench --site test.localhost install-app fde_component && \
    bench --site test.localhost migrate && \
    bench build && \
    bench clear-cache

# Run M2 tests
CMD bash -c "\
    service mysql start && \
    service redis-server start && \
    sleep 5 && \
    bench --site test.localhost run-tests --app fde_component \
  "
