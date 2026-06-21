============================================
 TEMPLATE EXCEL UNTUK EXPORT DATA ODOO
============================================

CARA MENGGUNAKAN:
1. Buat file Excel template dengan layout/form yang sudah kamu punya
2. Simpan file di folder ini dengan nama sesuai daftar di bawah
3. Sesuaikan mapping cell di file: models/excel_template.py
4. Upgrade module: Settings → Apps → Update Apps List → Upgrade "IT Department"

============================================
 DAFTAR TEMPLATE YANG DIBUTUHKAN:
============================================

1. bast_template.xlsx
   - Untuk: Item Handover (BAST Multi-Item)
   - Model: it_asset.item.handover
   - Mapping cell default:
     C5  = Nomor Dokumen
     C6  = Tanggal
     C7  = Yang Menyerahkan
     C8  = Posisi Penyerah
     C9  = Yang Menerima
     C10 = Posisi Penerima
     A13 = Tabel items (No, Nama Barang, Qty, Kondisi, Keterangan)

2. asset_request_template.xlsx
   - Untuk: Asset Request Form
   - Model: it_asset.request
   - Mapping cell default:
     C5  = Nomor Referensi
     C6  = Nama Pemohon
     C7  = Departemen
     C8  = Tanggal
     C9  = Kategori Aset
     C10 = Alasan
     C11 = Status

3. damage_report_template.xlsx
   - Untuk: Damage Report (BA Kerusakan)
   - Model: it_asset.damage_report
   - Mapping cell default:
     C5  = Nomor Referensi
     C6  = Tanggal Laporan
     C7  = Nama Aset
     C8  = ID Aset / Tag
     C9  = Pelapor
     C10 = Tipe Kerusakan
     C11 = Deskripsi

4. account_request_template.xlsx
   - Untuk: Account Request Form
   - Model: it_asset.account_request
   - Mapping cell default:
     C5  = Nomor Referensi
     C6  = Nama Pemohon
     C7  = Departemen
     C8  = Tanggal
     C9  = Tipe Account
     C10 = Alasan
     C11 = Status

5. handover_template.xlsx
   - Untuk: Asset Handover (single asset)
   - Model: it_asset.handover
   - Mapping cell default:
     C5  = Nomor Dokumen
     C6  = Tanggal
     C7  = Yang Menyerahkan
     C8  = Posisi Penyerah
     C9  = Yang Menerima
     C10 = Posisi Penerima
     C11 = Nama Aset
     C12 = Catatan

============================================
 SESUAIKAN MAPPING CELL
============================================

Jika layout template Excel kamu berbeda, edit file:
  addons/it_asset/models/excel_template.py

Cari method yang sesuai, lalu ubah parameter cell_ref
(misal: 'C5' → 'B3' jika data nomor dokumen ada di cell B3)

Contoh:
  # Sebelum:
  self._set_cell_value(ws, 'C5', handover.name or '')
  
  # Sesudah (jika template kamu beda):
  self._set_cell_value(ws, 'B3', handover.name or '')
