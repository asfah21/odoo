FROM odoo:18

USER root

RUN pip install --no-cache-dir openpyxl

# (Opsional) Library lain yang mungkin dibutuhkan
# RUN pip install --no-cache-dir xlrd xlwt

USER odoo
