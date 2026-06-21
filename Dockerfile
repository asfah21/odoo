FROM odoo:18

USER root

# Install python3-openpyxl via package manager resmi OS
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3-openpyxl && \
    rm -rf /var/lib/apt/lists/*

USER odoo