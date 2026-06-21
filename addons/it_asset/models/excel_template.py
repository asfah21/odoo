# -*- coding: utf-8 -*-
"""
Excel Template Export untuk IT Asset Module
- Mengisi data Odoo ke template Excel yang sudah ada (tanpa mengubah layout)
- Support: Item Handover (BAST), Asset Request, Damage Report, dll
"""

import base64
import io
import logging
import os
from datetime import datetime

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
except ImportError:
    _logger.warning("openpyxl tidak terinstall. Install dengan: pip install openpyxl")
    openpyxl = None


class ITAssetExcelTemplate(models.AbstractModel):
    _name = 'it_asset.excel_template'
    _description = 'Excel Template Export untuk IT Asset'

    # ============================================
    # KONFIGURASI TEMPLATE
    # ============================================
    
    def _get_template_path(self, template_name):
        """
        Mendapatkan path file template Excel.
        Template diletakkan di: addons/it_asset/static/excel_templates/
        """
        module_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_dir = os.path.join(module_path, 'static', 'excel_templates')
        return os.path.join(template_dir, template_name)

    # ============================================
    # GENERIC: BUKA TEMPLATE & ISI DATA
    # ============================================

    def _load_template(self, template_name):
        """Membuka file template Excel"""
        if openpyxl is None:
            raise UserError(_(
                "Library 'openpyxl' tidak terinstall. "
                "Hubungi administrator untuk menginstallnya: pip install openpyxl"
            ))
        
        template_path = self._get_template_path(template_name)
        if not os.path.exists(template_path):
            raise UserError(_(
                "File template '%s' tidak ditemukan. "
                "Pastikan file template sudah ada di folder static/excel_templates/"
            ) % template_name)
        
        wb = openpyxl.load_workbook(template_path)
        return wb

    def _save_to_buffer(self, wb):
        """Menyimpan workbook ke buffer dan return base64"""
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return base64.b64encode(buffer.read())

    def _get_cell(self, ws, cell_ref):
        """
        Mendapatkan cell berdasarkan reference (contoh: 'B5').
        Bisa juga pakai row/column number.
        """
        return ws[cell_ref]

    def _get_merged_cell_top_left(self, ws, cell_ref):
        """
        Jika cell_ref adalah bagian dari merged range, kembalikan referensi
        cell utama (top-left) dari merged range tersebut.
        Jika tidak, kembalikan cell_ref asli.
        """
        for merged_range in ws.merged_cells.ranges:
            if cell_ref in merged_range:
                # Dapatkan koordinat top-left cell dari merged range
                top_left = merged_range.min_row, merged_range.min_col
                # Konversi ke format cell reference (contoh: 'A1')
                from openpyxl.utils import get_column_letter
                return f"{get_column_letter(top_left[1])}{top_left[0]}"
        return cell_ref

    def _set_cell_value(self, ws, cell_ref, value):
        """
        Mengisi nilai ke cell tertentu tanpa mengubah format.
        Jika cell adalah bagian dari merged range, secara otomatis
        menggunakan cell utama (top-left) dari merged range tersebut
        untuk menghindari error 'MergedCell' object attribute 'value' is read-only.
        """
        # Cek apakah cell_ref berada di dalam merged range
        actual_ref = self._get_merged_cell_top_left(ws, cell_ref)
        cell = self._get_cell(ws, actual_ref)
        cell.value = value
        return cell

    # ============================================
    # 1. EXPORT ITEM HANDOVER (BAST) KE EXCEL
    # ============================================

    def export_item_handover_excel(self, handover_id):
        """
        Export data Item Handover ke template Excel.
        Template: 'bast_template.xlsx'
        
        Mapping cell (sesuaikan dengan layout template Excel kamu):
        - C5: Nomor Dokumen (handover name)
        - K11: Tanggal
        - K7: Yang Menyerahkan
        - K8: Posisi Penyerah
        - K9: Yang Menerima
        - K10: Posisi Penerima
        - B14 dst: Tabel items (No, Nama Barang, Qty, Kondisi, Keterangan)
        """
        handover = self.env['it_asset.item.handover'].browse(handover_id)
        if not handover.exists():
            raise UserError(_("Item Handover tidak ditemukan!"))

        # Load template
        wb = self._load_template('bast_template.xlsx')
        ws = wb.active

        # --- ISI DATA KE CELL ---
        # Header / Meta
        self._set_cell_value(ws, 'C5', handover.name or '')
        
        # Format tanggal
        tgl = handover.handover_date
        tgl_str = tgl.strftime('%d/%m/%Y') if tgl else ''
        self._set_cell_value(ws, 'C6', tgl_str)
        
        # Pihak
        self._set_cell_value(ws, 'C7', handover.sender_id.name or '')
        self._set_cell_value(ws, 'C8', handover.sender_id.job_id.name or '')
        self._set_cell_value(ws, 'C9', handover.receiver_id.name or '')
        self._set_cell_value(ws, 'C10', handover.receiver_id.job_id.name or '')

        # --- TABEL ITEMS ---
        # Cari baris pertama tabel (misal baris 13)
        start_row = 13
        current_row = start_row
        
        for idx, line in enumerate(handover.line_ids, start=1):
            # No
            self._set_cell_value(ws, f'A{current_row}', idx)
            
            # Nama Barang
            if line.item_type == 'asset':
                item_name = line.asset_id.name or ''
                if line.asset_id.asset_tag:
                    item_name += f" ({line.asset_id.asset_tag})"
            else:
                item_name = line.consumable_id.name or ''
            self._set_cell_value(ws, f'B{current_row}', item_name)
            
            # Qty
            self._set_cell_value(ws, f'C{current_row}', line.quantity)
            
            # Kondisi
            if line.item_type == 'asset':
                kondisi = line.asset_id.condition or 'Baik'
            else:
                kondisi = 'Baik'
            self._set_cell_value(ws, f'D{current_row}', kondisi.capitalize())
            
            # Keterangan (SN / Notes)
            keterangan = ''
            if line.item_type == 'asset' and line.asset_id.lot_id:
                keterangan += f"SN: {line.asset_id.lot_id.name}"
            if line.notes:
                if keterangan:
                    keterangan += '\n'
                keterangan += line.notes
            self._set_cell_value(ws, f'E{current_row}', keterangan)
            
            current_row += 1

        # Simpan ke buffer
        file_data = self._save_to_buffer(wb)
        
        # Buat attachment
        filename = f"BAST_{handover.name}_{tgl_str}.xlsx"
        filename = filename.replace('/', '_').replace('\\', '_')
        
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'datas': file_data,
            'res_model': 'it_asset.item.handover',
            'res_id': handover.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })

        return {
            'name': _('Download Excel'),
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    # ============================================
    # 2. EXPORT ASSET REQUEST KE EXCEL
    # ============================================

    def export_asset_request_excel(self, request_id):
        """
        Export Asset Request ke template Excel.
        Template: 'asset_request_template.xlsx'
        """
        request = self.env['it_asset.request'].browse(request_id)
        if not request.exists():
            raise UserError(_("Asset Request tidak ditemukan!"))

        wb = self._load_template('asset_request_template.xlsx')
        ws = wb.active

        # Mapping cell (sesuaikan dengan template kamu)
        self._set_cell_value(ws, 'C5', request.name or '')
        self._set_cell_value(ws, 'C6', request.employee_id.name or '')
        self._set_cell_value(ws, 'C7', request.department_id.name or '')
        
        tgl = request.request_date
        self._set_cell_value(ws, 'C8', tgl.strftime('%d/%m/%Y') if tgl else '')
        
        self._set_cell_value(ws, 'C9', request.category_id.name or '')
        self._set_cell_value(ws, 'C10', request.reason or '')
        self._set_cell_value(ws, 'C11', dict(request._fields['state'].selection).get(request.state, ''))

        file_data = self._save_to_buffer(wb)
        filename = f"Asset_Request_{request.name}.xlsx"

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'datas': file_data,
            'res_model': 'it_asset.request',
            'res_id': request.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })

        return {
            'name': _('Download Excel'),
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    # ============================================
    # 3. EXPORT DAMAGE REPORT KE EXCEL
    # ============================================

    def export_damage_report_excel(self, report_id):
        """
        Export Damage Report ke template Excel.
        Template: 'damage_report_template.xlsx'
        """
        report = self.env['it_asset.damage_report'].browse(report_id)
        if not report.exists():
            raise UserError(_("Damage Report tidak ditemukan!"))

        wb = self._load_template('damage_report_template.xlsx')
        ws = wb.active

        self._set_cell_value(ws, 'C5', report.name or '')
        
        tgl = report.report_date
        self._set_cell_value(ws, 'C6', tgl.strftime('%d %B %Y') if tgl else '')
        
        self._set_cell_value(ws, 'C7', report.asset_id.name or '')
        self._set_cell_value(ws, 'C8', report.asset_id.asset_tag or '')
        self._set_cell_value(ws, 'C9', report.employee_id.name or '')
        self._set_cell_value(ws, 'C10', report.damage_type or '')
        self._set_cell_value(ws, 'C11', report.description or '')

        file_data = self._save_to_buffer(wb)
        filename = f"Damage_Report_{report.name}.xlsx"

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'datas': file_data,
            'res_model': 'it_asset.damage_report',
            'res_id': report.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })

        return {
            'name': _('Download Excel'),
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    # ============================================
    # 4. EXPORT ACCOUNT REQUEST KE EXCEL
    # ============================================

    def export_account_request_excel(self, request_id):
        """
        Export Account Request ke template Excel.
        Template: 'account_request_template.xlsx'
        """
        request = self.env['it_asset.account_request'].browse(request_id)
        if not request.exists():
            raise UserError(_("Account Request tidak ditemukan!"))

        wb = self._load_template('account_request_template.xlsx')
        ws = wb.active

        self._set_cell_value(ws, 'C5', request.name or '')
        self._set_cell_value(ws, 'C6', request.employee_id.name or '')
        self._set_cell_value(ws, 'C7', request.department_id.name or '')
        
        tgl = request.request_date
        self._set_cell_value(ws, 'C8', tgl.strftime('%d/%m/%Y') if tgl else '')
        
        self._set_cell_value(ws, 'C9', request.account_type or '')
        self._set_cell_value(ws, 'C10', request.reason or '')
        self._set_cell_value(ws, 'C11', dict(request._fields['state'].selection).get(request.state, ''))

        file_data = self._save_to_buffer(wb)
        filename = f"Account_Request_{request.name}.xlsx"

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'datas': file_data,
            'res_model': 'it_asset.account_request',
            'res_id': request.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })

        return {
            'name': _('Download Excel'),
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    # ============================================
    # 5. EXPORT HANDOVER (OLD) KE EXCEL
    # ============================================

    def export_handover_excel(self, handover_id):
        """
        Export Asset Handover (single asset) ke template Excel.
        Template: 'handover_template.xlsx'
        """
        handover = self.env['it_asset.handover'].browse(handover_id)
        if not handover.exists():
            raise UserError(_("Handover tidak ditemukan!"))

        wb = self._load_template('handover_template.xlsx')
        ws = wb.active

        self._set_cell_value(ws, 'A9', handover.name or '')
        
        tgl = handover.handover_date
        self._set_cell_value(ws, 'A11', tgl.strftime('%d %B %Y') if tgl else '')
        
        self._set_cell_value(ws, 'I14', handover.sender_id.name or '')
        self._set_cell_value(ws, 'I15', handover.sender_id.job_id.name or '')
        self._set_cell_value(ws, 'Z14', handover.receiver_id.name or '')
        self._set_cell_value(ws, 'Z15', handover.receiver_id.job_id.name or '')
        self._set_cell_value(ws, 'D21', handover.asset_id.name or '')
        self._set_cell_value(ws, 'X21', handover.notes or '')

        file_data = self._save_to_buffer(wb)
        filename = f"Handover_{handover.name}.xlsx"

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'datas': file_data,
            'res_model': 'it_asset.handover',
            'res_id': handover.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })

        return {
            'name': _('Download Excel'),
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }
