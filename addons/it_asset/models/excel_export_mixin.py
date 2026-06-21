# -*- coding: utf-8 -*-
"""
Mixin / Inheritance untuk menambahkan tombol Export Excel
ke masing-masing model. Method ini memanggil service dari
it_asset.excel_template untuk mengisi data ke template Excel.
"""

import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class ITAssetRequestExcel(models.Model):
    """Tambahkan method Export Excel ke Asset Request"""
    _inherit = 'it_asset.request'

    def export_asset_request_excel(self):
        self.ensure_one()
        excel_service = self.env['it_asset.excel_template']
        return excel_service.export_asset_request_excel(self.id)


class ITAssetHandoverExcel(models.Model):
    """Tambahkan method Export Excel ke Handover (single asset)"""
    _inherit = 'it_asset.handover'

    def export_handover_excel(self):
        self.ensure_one()
        excel_service = self.env['it_asset.excel_template']
        return excel_service.export_handover_excel(self.id)


class ITAssetDamageReportExcel(models.Model):
    """Tambahkan method Export Excel ke Damage Report"""
    _inherit = 'it_asset.damage_report'

    def export_damage_report_excel(self):
        self.ensure_one()
        excel_service = self.env['it_asset.excel_template']
        return excel_service.export_damage_report_excel(self.id)


class ITAssetAccountRequestExcel(models.Model):
    """Tambahkan method Export Excel ke Account Request"""
    _inherit = 'it_asset.account_request'

    def export_account_request_excel(self):
        self.ensure_one()
        excel_service = self.env['it_asset.excel_template']
        return excel_service.export_account_request_excel(self.id)


class ITAssetItemHandoverExcel(models.Model):
    """Tambahkan method Export Excel ke Item Handover (multi-item)"""
    _inherit = 'it_asset.item.handover'

    def export_item_handover_excel(self):
        self.ensure_one()
        excel_service = self.env['it_asset.excel_template']
        return excel_service.export_item_handover_excel(self.id)
