FROM odoo:18

USER root

# Install python3-openpyxl dan pillow via package manager resmi OS
# pillow diperlukan openpyxl untuk mempertahankan gambar bawaan template Excel
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3-openpyxl python3-pil && \
    rm -rf /var/lib/apt/lists/*

USER odoo
