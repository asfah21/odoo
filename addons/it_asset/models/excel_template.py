# -*- coding: utf-8 -*-
"""
Excel Template Export untuk IT Asset Module
- Mengisi data Odoo ke template Excel yang sudah ada (tanpa mengubah layout)
- Support: Item Handover (BAST), Asset Request, Damage Report, dll
- Menggunakan ZIP Surgery (lxml) untuk menghindari korupsi gambar/logo oleh openpyxl
"""

import base64
import logging
import os
import re
import shutil
import tempfile
import zipfile

from odoo import api, fields, models, _
from odoo.tools import format_date
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from lxml import etree
except ImportError:
    _logger.warning("lxml tidak terinstall. Install dengan: pip install lxml")
    etree = None

# Namespace Excel yang dipakai
NS_SPREADSHEET = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
NS_RELATIONSHIPS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS_RELATIONSHIPS_PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS_XML = 'http://www.w3.org/XML/1998/namespace'

NSMAP = {
    'x': NS_SPREADSHEET,
    'r': NS_RELATIONSHIPS,
}


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
    # ZIP SURGERY ENGINE
    # ============================================

    def _fill_template(self, template_name, cell_data, sheet_index=0):
        """
        Engine utama ZIP Surgery:
        - Copy template ke tempfile
        - Baca semua file dalam ZIP ke dict {filename: bytes}
        - Temukan sheet XML yang benar
        - Edit hanya XML sheet tersebut
        - Tulis ulang ZIP dengan semua file (yang tidak diedit tetap byte-for-byte sama)
        - Return base64 encoded result
        - Cleanup semua tempfile di finally block
        """
        if etree is None:
            raise UserError(_(
                "Library 'lxml' tidak terinstall. "
                "Hubungi administrator untuk menginstallnya: pip install lxml"
            ))

        template_path = self._get_template_path(template_name)
        if not os.path.exists(template_path):
            raise UserError(_(
                "File template '%s' tidak ditemukan. "
                "Pastikan file template sudah ada di folder static/excel_templates/"
            ) % template_name)

        tmp_copy = None
        tmp_output = None
        try:
            # Copy template ke tempfile (jangan edit asli)
            with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
                tmp_copy = tmp.name
            shutil.copy2(template_path, tmp_copy)

            # Baca semua file dalam ZIP ke dict {filename: bytes}
            all_files = {}
            with zipfile.ZipFile(tmp_copy, 'r') as z:
                for name in z.namelist():
                    all_files[name] = z.read(name)

            # Temukan sheet XML yang benar
            sheet_key = self._find_sheet_xml_key(all_files, sheet_index)

            # Edit hanya XML sheet tersebut
            sheet_xml_bytes = all_files[sheet_key]
            modified_sheet_xml = self._inject_cell_values(sheet_xml_bytes, cell_data)
            all_files[sheet_key] = modified_sheet_xml

            # Tulis ulang ZIP dengan semua file
            with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
                tmp_output = tmp.name

            with zipfile.ZipFile(tmp_output, 'w', zipfile.ZIP_DEFLATED) as z:
                for name in all_files:
                    z.writestr(name, all_files[name])

            # Baca hasil sebagai base64
            with open(tmp_output, 'rb') as f:
                file_data = base64.b64encode(f.read())

            return file_data

        finally:
            # Cleanup semua tempfile
            for tmp_path in [tmp_copy, tmp_output]:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

    def _find_sheet_xml_key(self, all_files, sheet_index=0):
        """
        Cari xl/worksheets/sheetN.xml dari all_files.keys()
        Baca urutan sheet dari xl/workbook.xml menggunakan lxml etree
        Return key yang sesuai sheet_index
        """
        # Baca workbook.xml untuk mendapatkan urutan sheet
        workbook_xml = all_files.get('xl/workbook.xml')
        if workbook_xml is None:
            # Fallback: cari langsung berdasarkan pola sheetN.xml
            sheet_keys = sorted([
                k for k in all_files.keys()
                if re.match(r'xl/worksheets/sheet\d+\.xml$', k)
            ])
            if sheet_index < len(sheet_keys):
                return sheet_keys[sheet_index]
            raise UserError(_(
                "Sheet index %s tidak ditemukan dalam template."
            ) % sheet_index)

        # Parse workbook.xml untuk mendapatkan urutan sheet
        root = etree.fromstring(workbook_xml)
        
        # Cari elemen sheets
        sheets_el = root.find('.//x:sheets', NSMAP)
        if sheets_el is None:
            # Fallback ke pola sheetN.xml
            sheet_keys = sorted([
                k for k in all_files.keys()
                if re.match(r'xl/worksheets/sheet\d+\.xml$', k)
            ])
            if sheet_index < len(sheet_keys):
                return sheet_keys[sheet_index]
            raise UserError(_(
                "Sheet index %s tidak ditemukan dalam template."
            ) % sheet_index)

        # Dapatkan semua sheet elements
        sheet_elements = sheets_el.findall('x:sheet', NSMAP)
        if sheet_index >= len(sheet_elements):
            raise UserError(_(
                "Sheet index %s tidak ditemukan dalam template."
            ) % sheet_index)

        # Dapatkan r:id dari sheet yang dituju
        sheet_el = sheet_elements[sheet_index]
        sheet_rel_id = sheet_el.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        
        if sheet_rel_id:
            # Cari relationship di xl/_rels/workbook.xml.rels
            rels_key = 'xl/_rels/workbook.xml.rels'
            rels_xml = all_files.get(rels_key)
            if rels_xml:
                rels_root = etree.fromstring(rels_xml)
                # Cari Relationship dengan Id yang sesuai
                for rel in rels_root:
                    rel_id = rel.get('Id')
                    if rel_id == sheet_rel_id:
                        target = rel.get('Target')
                        if target:
                            # Target biasanya relatif seperti "worksheets/sheet1.xml"
                            return f'xl/{target}'

        # Fallback: jika tidak bisa resolve via rels, cari berdasarkan urutan sheetN.xml
        sheet_keys = sorted([
            k for k in all_files.keys()
            if re.match(r'xl/worksheets/sheet\d+\.xml$', k)
        ])
        if sheet_index < len(sheet_keys):
            return sheet_keys[sheet_index]

        raise UserError(_(
            "Sheet index %s tidak ditemukan dalam template."
        ) % sheet_index)

    def _inject_cell_values(self, sheet_xml_bytes, cell_data):
        """
        Parse XML dengan lxml etree
        Index semua existing cell elements ke dict {ref: cell_element}
        Untuk setiap cell di cell_data:
          - Jika cell sudah ada: update nilainya via _update_cell_element()
          - Jika cell belum ada: log warning dan skip
        Return etree.tostring() dengan xml_declaration=True, encoding=UTF-8
        """
        root = etree.fromstring(sheet_xml_bytes)

        # Cari sheetData element
        sheet_data_el = root.find('.//x:sheetData', NSMAP)
        if sheet_data_el is None:
            _logger.warning("sheetData tidak ditemukan dalam sheet XML")
            return sheet_xml_bytes

        # Index semua existing cell elements ke dict {ref: cell_element}
        # Cari row elements, lalu cell elements di dalamnya
        existing_cells = {}
        for row_el in sheet_data_el.findall('x:row', NSMAP):
            for cell_el in row_el.findall('x:c', NSMAP):
                ref = cell_el.get('r')
                if ref:
                    existing_cells[ref] = cell_el

        # Untuk setiap cell di cell_data
        for cell_ref, value in cell_data.items():
            if cell_ref in existing_cells:
                self._update_cell_element(existing_cells[cell_ref], value)
            else:
                _logger.warning(
                    "Cell %s tidak ditemukan di template. Nilai tidak akan diisi.",
                    cell_ref
                )

        # Return etree.tostring() dengan xml_declaration=True, encoding=UTF-8
        return etree.tostring(
            root,
            xml_declaration=True,
            encoding='UTF-8',
            standalone=True
        )

    def _update_cell_element(self, cell_el, value):
        """
        Update nilai cell element:
        - Simpan atribut 's' (style index) sebelum modifikasi — WAJIB agar format tidak hilang
        - Hapus semua child nodes
        - Untuk int/float: set tanpa 't' attribute, tambah <v> element
        - Untuk string: set t="inlineStr", tambah <is><t> element
          * Jika ada \n dalam string: tambah xml:space="preserve"
        - Untuk None/'': kosongkan cell
        - Restore atribut 's' setelah selesai
        """
        # Simpan atribut 's' (style index) sebelum modifikasi
        style_attr = cell_el.get('s')

        # Hapus semua child nodes
        for child in list(cell_el):
            cell_el.remove(child)

        # Hapus atribut 't' (type) — akan di-set ulang sesuai tipe data
        if 't' in cell_el.attrib:
            del cell_el.attrib['t']

        # Restore atribut 's' setelah selesai
        if style_attr is not None:
            cell_el.set('s', style_attr)

        # Handle nilai berdasarkan tipe
        if value is None or value == '':
            # Kosongkan cell — tidak perlu child nodes
            pass

        elif isinstance(value, (int, float)):
            # Untuk int/float: set tanpa 't' attribute, tambah <v> element
            v_el = etree.SubElement(cell_el, '{%s}v' % NS_SPREADSHEET)
            v_el.text = str(value)

        elif isinstance(value, bool):
            # Boolean sebagai angka 0/1
            v_el = etree.SubElement(cell_el, '{%s}v' % NS_SPREADSHEET)
            v_el.text = '1' if value else '0'

        else:
            # String: set t="inlineStr", tambah <is><t> element
            cell_el.set('t', 'inlineStr')
            is_el = etree.SubElement(cell_el, '{%s}is' % NS_SPREADSHEET)
            t_el = etree.SubElement(is_el, '{%s}t' % NS_SPREADSHEET)
            
            str_value = str(value)
            
            # Jika ada \n dalam string: tambah xml:space="preserve"
            if '\n' in str_value:
                t_el.set('{%s}space' % NS_XML, 'preserve')
            
            t_el.text = str_value

    # ============================================
    # HELPER: CREATE ATTACHMENT
    # ============================================

    def _create_attachment(self, file_data, filename, res_model, res_id):
        """
        Helper membuat ir.attachment dan return action download
        """
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'datas': file_data,
            'res_model': res_model,
            'res_id': res_id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })

        return {
            'name': _('Download Excel'),
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    # ============================================
    # 1. EXPORT ITEM HANDOVER (BAST) KE EXCEL
    # ============================================

    def export_item_handover_excel(self, handover_id):
        handover = self.env['it_asset.item.handover'].browse(handover_id)
        if not handover.exists():
            raise UserError(_("Item Handover tidak ditemukan!"))

        # Tanggal
        tgl = handover.handover_date
        tgl_str = tgl.strftime('%d/%m/%Y') if tgl else ''

        # Kumpulkan semua data ke satu dict cell_data
        cell_data = {
            'K11': tgl_str,
            'K7': handover.sender_id.name if handover.sender_id else '',
            'K8': handover.sender_id.job_id.name if handover.sender_id and handover.sender_id.job_id else '',
            'K9': handover.receiver_id.name if handover.receiver_id else '',
            'K10': handover.receiver_id.job_id.name if handover.receiver_id and handover.receiver_id.job_id else '',
            'D38': handover.sender_id.name if handover.sender_id else '',
            'T38': handover.receiver_id.name if handover.receiver_id else '',

            'C39': handover.sender_id.job_id.name if handover.sender_id and handover.sender_id.job_id else '',
            'S39': handover.receiver_id.job_id.name if handover.receiver_id and handover.receiver_id.job_id else '',
            'G40': tgl_str,
        }

        # Tabel Items
        start_row = 14
        current_row = start_row
        
        for idx, line in enumerate(handover.line_ids, start=1):
            # No
            cell_data[f'B{current_row}'] = idx
            
            # Nama Barang
            if line.item_type == 'asset' and line.asset_id:
                item_name = line.asset_id.name if line.asset_id.name else ''
                if line.asset_id.asset_tag:
                    item_name += f" ({line.asset_id.asset_tag})"
            elif line.item_type == 'consumable' and line.consumable_id:
                item_name = line.consumable_id.name if line.consumable_id.name else ''
            else:
                item_name = ''
            cell_data[f'D{current_row}'] = item_name
            
            # Jumlah
            qty = line.quantity if line.quantity else 0
            cell_data[f'S{current_row}'] = qty
            
            # Kondisi
            if line.item_type == 'asset' and line.asset_id:
                kondisi = line.asset_id.condition if line.asset_id.condition else 'Baik'
            else:
                kondisi = 'Baik'
            cell_data[f'X{current_row}'] = kondisi.capitalize()
            
            # Keterangan (SN / Notes)
            keterangan = ''
            if line.item_type == 'asset' and line.asset_id and line.asset_id.lot_id:
                keterangan += f"SN: {line.asset_id.lot_id.name}"
            if line.notes:
                if keterangan:
                    keterangan += '\n'
                keterangan += line.notes
            cell_data[f'AF{current_row}'] = keterangan
            
            current_row += 1

        file_data = self._fill_template('bast_template.xlsx', cell_data)
        
        filename = f"BAST_{handover.name}_{tgl_str}.xlsx"
        filename = filename.replace('/', '_').replace('\\', '_')
        
        return self._create_attachment(
            file_data, filename,
            'it_asset.item.handover', handover.id
        )

    # ============================================
    # 2. EXPORT ASSET REQUEST KE EXCEL
    # ============================================

    def export_asset_request_excel(self, request_id):
        request = self.env['it_asset.request'].browse(request_id)
        if not request.exists():
            raise UserError(_("Asset Request tidak ditemukan!"))

        tgl = request.request_date
        state_label = dict(request._fields['state'].selection).get(request.state, '') if request.state else ''

        cell_data = {
            'B11': request.name if request.name else '',
            'N11': request.employee_id.name if request.employee_id else '',
            'AN11': request.department_id.name if request.department_id else '',
            'T6': tgl.strftime('%d/%m/%Y') if tgl else '',
            'B40': request.reason if request.reason else '',
            # 'C11': state_label,
            'B56': request.name if request.name else '',
            'D57': tgl.strftime('%d/%m/%Y') if tgl else '',
        }

        # Logic category: Laptop -> centang C21, Printer -> centang C23, lainnya -> tulis di D35
        if request.category_id:
            cat_name = request.category_id.name.lower()
            if 'laptop' in cat_name:
                cell_data['C21'] = '✓'
            elif 'printer' in cat_name:
                cell_data['C23'] = '✓'
            else:
                cell_data['D35'] = request.category_id.name

        file_data = self._fill_template('asset_request_template.xlsx', cell_data)
        filename = f"Asset_Request_{request.name}.xlsx"

        return self._create_attachment(
            file_data, filename,
            'it_asset.request', request.id
        )

    # ============================================
    # 3. EXPORT DAMAGE REPORT KE EXCEL
    # ============================================

    def export_damage_report_excel(self, report_id):
        report = self.env['it_asset.damage_report'].browse(report_id)
        if not report.exists():
            raise UserError(_("Damage Report tidak ditemukan!"))

        tgl = report.report_date

        cell_data = {
            'A9': report.name if report.name else '',
            'A11': f" Pada hari {tgl.strftime('%A, %d %B %Y')} telah terjadi kerusakan perangkat sebanyak 1 (satu) unit dengan rincian sebagai berikut:" if tgl else '',
            'K14': report.asset_id.asset_tag if report.asset_id and report.asset_id.asset_tag else '',
            'K15': report.employee_id.name if report.employee_id else '',
            'K16': report.asset_id.category_id.name if report.asset_id and report.asset_id.category_id else '',
            'K17': report.asset_id.name if report.asset_id else '',
            'K18': report.asset_id.model if report.asset_id and report.asset_id.model else '',
            'K19': report.asset_id.lot_id.name if report.asset_id and report.asset_id.lot_id else '',
            'A23': report.description if report.description else '',
            'A40': report.user_id.name if report.user_id else '',
            'J40': report.verified_by_id.name if report.verified_by_id else '',
            'S40': report.known_by_id.name if report.known_by_id else '',
            'AB40': report.approved_by_id.name if report.approved_by_id else '',
        }

        file_data = self._fill_template('damage_report_template.xlsx', cell_data)
        filename = f"Damage_Report_{report.name}.xlsx"

        return self._create_attachment(
            file_data, filename,
            'it_asset.damage_report', report.id
        )

    # ============================================
    # 4. EXPORT ACCOUNT REQUEST KE EXCEL
    # ============================================

    def export_account_request_excel(self, request_id):
        request = self.env['it_asset.account_request'].browse(request_id)
        if not request.exists():
            raise UserError(_("Account Request tidak ditemukan!"))

        tgl = request.request_date
        state_label = dict(request._fields['state'].selection).get(request.state, '') if request.state else ''

        cell_data = {
            # 'C5': request.name if request.name else '',
            'R11': request.employee_id.name if request.employee_id else '',
            'R14': request.department_id.name if request.department_id else '',
            'D49': tgl.strftime('%d/%m/%Y') if tgl else '',
            'R19': request.account_type if request.account_type else '',
            'B25': request.reason if request.reason else '',
            # 'C11': state_label,
            'B48': request.employee_id.name if request.employee_id else '',
        }

        file_data = self._fill_template('account_request_template.xlsx', cell_data)
        filename = f"Account_Request_{request.name}.xlsx"

        return self._create_attachment(
            file_data, filename,
            'it_asset.account_request', request.id
        )

    # ============================================
    # 5. EXPORT HANDOVER (OLD) KE EXCEL
    # ============================================

    def export_handover_excel(self, handover_id):
        handover = self.env['it_asset.handover'].browse(handover_id)
        if not handover.exists():
            raise UserError(_("Handover tidak ditemukan!"))

        tgl = handover.handover_date

        # Kondisi
        kondisi = ''
        if handover.asset_id and handover.asset_id.condition:
            kondisi = handover.asset_id.condition.capitalize()

        # Keterangan (SN + Notes)
        keterangan = ''
        if handover.asset_id and handover.asset_id.lot_id:
            keterangan += f"SN: {handover.asset_id.lot_id.name}"
        if handover.notes:
            if keterangan:
                keterangan += '\n'
            keterangan += handover.notes

        cell_data = {
            'A9': handover.name if handover.name else '',
            'A11': f" Pada hari {tgl.strftime('%A, %d %B %Y')} telah dilakukan serah terima perangkat dengan rincian:" if tgl else '',
            'I14': handover.sender_id.name if handover.sender_id else '',
            'I15': handover.sender_id.job_id.name if handover.sender_id and handover.sender_id.job_id else '',
            'Z14': handover.receiver_id.name if handover.receiver_id else '',
            'Z15': handover.receiver_id.job_id.name if handover.receiver_id and handover.receiver_id.job_id else '',
            'C45': handover.sender_id.name if handover.sender_id else '',
            'N45': handover.receiver_id.job_id.name if handover.receiver_id and handover.receiver_id.job_id else '',
            'B21': 1,
            'D21': handover.asset_id.name if handover.asset_id else '',
            'Q21': 1,
            'S21': kondisi,
            'X21': keterangan,
        }

        file_data = self._fill_template('handover_template.xlsx', cell_data)
        filename = f"Handover_{handover.name}.xlsx"

        return self._create_attachment(
            file_data, filename,
            'it_asset.handover', handover.id
        )
