# Dockerfile for fde_component M2 testing
# Build: docker compose build
# Run:   docker compose up
#
# The bench container has its own MariaDB + Redis installed locally.
# Entrypoint creates the site and runs tests after services are ready.

FROM frappe/bench:latest

USER root

# Install MariaDB server, Redis server, and client tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    mariadb-server \
    redis-server \
    default-mysql-client \
    redis-tools \
    && rm -rf /var/lib/apt/lists/*

# Prepare MariaDB directories
RUN mkdir -p /run/mysqld /var/lib/mysql && \
    chown -R mysql:mysql /run/mysqld /var/lib/mysql

# Prepare Redis directory
RUN mkdir -p /var/lib/redis && \
    chown -R redis:redis /var/lib/redis

# Clone the app from GitHub
ARG REPO_URL=https://github.com/muhammadfawad538/FDE-component.git
USER frappe
RUN git clone "$REPO_URL" /tmp/repo && \
    cp -r /tmp/repo/fde_component /home/frappe/frappe-bench/apps/ && \
    rm -rf /tmp/repo

# Copy entrypoint script
COPY entrypoint.sh /home/frappe/entrypoint.sh
RUN chmod +x /home/frappe/entrypoint.sh

# Set the entrypoint
ENTRYPOINT ["/home/frappe/entrypoint.sh"]
