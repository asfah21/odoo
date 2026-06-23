FROM odoo:18

USER root

# Install python3-lxml untuk ZIP Surgery pada template Excel
# lxml digunakan untuk manipulasi XML sheet langsung di dalam file .xlsx
# (tanpa merusak gambar/logo seperti openpyxl)
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3-lxml && \
    rm -rf /var/lib/apt/lists/*

USER odoo
