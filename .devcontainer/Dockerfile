FROM mcr.microsoft.com/vscode/devcontainers/python:3.11

RUN apt-get update && apt-get install -y \
    default-mysql-server \
    default-mysql-client \
    redis-server \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install bench
RUN pip install --no-cache-dir frappe-bench

# Pre-create bench directory
WORKDIR /workspaces/FDE-component

CMD ["bash", "-c", "service mysql start && service redis-server start && sleep 2 && bash fde_component/setup_bench.sh"]
