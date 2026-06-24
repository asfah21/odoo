# IT Asset Management - Odoo Module

Modul **IT Asset Management** untuk Odoo 18. Digunakan untuk mengelola aset IT dan Operasional di perusahaan, termasuk pelacakan inventaris, serah terima (handover/BAST), permintaan aset, pelaporan kerusakan, dan lainnya.

---

## Fitur Utama

### 1. Asset Management
- **IT Assets**: Laptop, PC, Monitor, Printer, dll.
- **Operational Assets**: Radio Rig, GPS Tracker, dan perangkat operasional lainnya.
- Setiap aset memiliki: Nama, Model, Serial Number (SN), Asset Tag, Kategori, Kondisi, Status.
- Status aset: Available, In Use, Out of Service, Retired.
- Kondisi aset: Good, Degraded, Broken.

### 2. Handover / BAST (Berita Acara Serah Terima)
- Serah terima aset dari satu pegawai ke pegawai lain.
- **Accessories / Perintilan**: Input perintilan langsung di form handover (Battery, Charger, Antenna, dll).
- Export Excel otomatis ke template BAST (7 baris: 1 baris asset utama + 6 baris perintilan).
- Tanda tangan digital (signature widget).

### 3. Item Handover
- Serah terima multiple items (Asset + Consumable) dalam satu form.
- Otomatis mengurangi stok consumable saat ditandatangani.
- Export Excel ke template BAST multi-item.

### 4. Asset Request
- Form permintaan aset baru oleh pegawai.
- Workflow: Draft → Submitted → Approved → Fulfilled / Rejected.
- Export Excel otomatis ke template permintaan aset.

### 5. Account Request
- Form permintaan akun (Email, Google Workspace, Accurate).
- Workflow: Draft → Submitted → Approved → Fulfilled / Rejected.
- Export Excel otomatis ke template permintaan akun.

### 6. Damage Report
- Pelaporan kerusakan aset.
- Workflow: Draft → Confirmed → Resolved.
- Saat dikonfirmasi, otomatis mengubah kondisi aset menjadi **Broken** dan status menjadi **Out of Service**.
- Export Excel otomatis ke template laporan kerusakan.

### 7. Printer Usage Tracking
- Mencatat pemakaian printer (hitam-putih dan warna).
- Dashboard statistik pemakaian printer.

### 8. Dashboard
- Statistik total aset, aset terpakai, tersedia, rusak.
- Distribusi kondisi laptop (Good, Degraded, Broken).
- Perbandingan aset operasional vs unit fleet.
- Statistik pemakaian printer.

### 9. Stock Integration
- Terintegrasi dengan modul **Inventory** Odoo.
- Setiap aset yang menggunakan produk storable akan otomatis dipindahkan dari lokasi IT ke lokasi User saat ditugaskan.
- Consumable stock otomatis berkurang saat di-handover.

---

## Struktur Menu

```
IT (App)
├── Asset Management
│   ├── IT
│   │   └── IT Assets (list/form)
│   └── Operational
│       └── Operational Assets (list/form)
├── Fleet
│   └── Units / Vehicles
└── Form
    ├── Asset Request
    ├── Account Request
    ├── Handover
    ├── Item Handover
    └── Damage Report
```

---

## Model Data

| Model | Description |
|-------|-------------|
| `it_asset.asset` | Data utama aset (IT & Operational) |
| `it_asset.category` | Kategori aset (Laptop, Printer, Radio Rig, dll) |
| `it_asset.handover` | Serah terima aset (BAST) |
| `it_asset.handover.accessory` | Perintilan dalam handover |
| `it_asset.item.handover` | Serah terima multiple items |
| `it_asset.item.handover.line` | Line items dalam item handover |
| `it_asset.request` | Permintaan aset |
| `it_asset.account_request` | Permintaan akun |
| `it_asset.damage_report` | Laporan kerusakan |
| `it_asset.assignment` | Riwayat penugasan aset |
| `it_asset.maintenance` | Riwayat maintenance |
| `it_asset.printer.usage` | Pemakaian printer |
| `it_asset.consumable` | Data consumable |
| `it_asset.unit` | Unit/vehicle untuk operational assets |
| `it_asset.swap` | Riwayat swap aset antar unit |
| `it_asset.excel_template` | Abstract model untuk export Excel |

---

## Export Excel

Modul ini menggunakan metode **ZIP Surgery** (edit XML langsung di file .xlsx) untuk mengisi data ke template Excel tanpa merusak layout, gambar, atau logo yang sudah ada.

Template Excel diletakkan di:
```
addons/it_asset/static/excel_templates/
```

Template yang tersedia:
- `handover_template.xlsx` — Template BAST handover
- `bast_template.xlsx` — Template BAST item handover
- `asset_request_template.xlsx` — Template permintaan aset
- `damage_report_template.xlsx` — Template laporan kerusakan
- `account_request_template.xlsx` — Template permintaan akun

---

## Cara Install

1. Copy folder `it_asset` ke direktori `addons/` Odoo Anda.
2. Update module list di Odoo (Apps → Update Modules List).
3. Install module **IT Asset Management**.
4. Setup template Excel di `addons/it_asset/static/excel_templates/`.

### Dependencies
- Odoo 18
- `lxml` (pip install lxml) — untuk ZIP Surgery Excel

---

## Penggunaan

### Handover (BAST)
1. Buka **Form → Handover**.
2. Pilih Asset, Sender, Receiver.
3. Di tab **Accessories / Perintilan**, tambahkan perintilan (Battery, Charger, dll).
4. Klik **Sign & Confirm**.
5. Klik **Export Excel** untuk download BAST.

### Item Handover
1. Buka **Form → Item Handover**.
2. Tambahkan items (Asset dan/atau Consumable).
3. Klik **Sign & Confirm** — consumable stock otomatis berkurang.
4. Klik **Export Excel** untuk download BAST multi-item.

### Asset Request
1. Buka **Form → Asset Request**.
2. Isi data permintaan.
3. Submit → Approve → Fulfill.
4. Klik **Export Excel** untuk download form permintaan.

---

## Lisensi

Proprietary / Internal Use — GSI IT Department
