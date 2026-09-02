# Klasifikasi Barang & Entitas — IT vs Operation

## 1. Tujuan

Dokumen ini menetapkan standar klasifikasi barang dan entitas operasional di Odoo modul **IT Department (PT GSI – Site Wolo)** serta integrasi dengan sistem **Go Kiosk**.

Klasifikasi dibagi menjadi **2 Domain Utama**:
1. **Domain IT (5 Tipe):** Untuk barang-barang teknologi informasi, perkantoran, dan infrastruktur IT.
2. **Domain Operation (3 Tipe):** Untuk aset komunikasi, material lapangan, dan armada tambang yang dikelola oleh tim IT.

Prinsip utama:

> **Category = Barangnya / Unitnya apa?** *(Contoh: Laptop, Radio Rig, SSD, Dump Truck, Antena, Konektor BNC)*  
> **Domain & Type = Dikelola di ranah apa dan sebagai apa?** *(IT vs Operation)*  
> **Tracking = Bagaimana dilacak fisiknya?** *(Serial Number / Unit ID vs Quantity)*  

---

## 2. Struktur Klasifikasi

```text
SISTEM MANAGEMENT IT & OPERASIONAL (SITE WOLO)
│
├── 🏢 1. DOMAIN IT (5 Tipe Pengelolaan)
│   ├── [1] Asset          — Perangkat IT bernilai / User Personal (Laptop, PC, Monitor, Printer)
│   ├── [2] Accessory      — Perlengkapan pendukung (Keyboard, Mouse, HDMI, Charger)
│   ├── [3] Spare Part     — Komponen pengganti / upgrade (RAM, SSD, PSU, Fan)
│   ├── [4] Tool           — Alat kerja teknisi / Borrow & Return (Multimeter, Solder, Crimper)
│   └── [5] Consumable     — Bahan habis pakai IT (Timah, RJ45, Kabel UTP, Isolasi)
│
└── 🚜 2. DOMAIN OPERATION (3 Tipe Pengelolaan)
    ├── [1] Asset          — Aset operasional lapangan (Radio Rig, Radio HT, CCTV, Repeater)
    ├── [2] Consumable     — Material habis pakai operasional (Kabel Coaxial RG58, Konektor BNC/PL, Fuse)
    └── [3] Fleet (Unit)   — Unit armada & alat berat tambang (DT, EX, LV, WT — induk pasang radio)
```

---

## 3. Detail Domain IT (5 Tipe)

### [IT-1] Asset
* **Definisi:** Perangkat keras IT bernilai tinggi yang diperuntukkan bagi kebutuhan kantor/operasional personal karyawan.
* **Target:** Ditugaskan ke **Employee / User** (`hr.employee`).
* **Tracking:** **Serial Number** (`stock.lot`) & **Asset Tag**.
* **Contoh:** Laptop, PC Desktop, Monitor, Printer Kantor, Server, Network Switch/Router, UPS.
* **Alur Hidup:** `Available` $\rightarrow$ `Assigned to User` $\rightarrow$ `Maintenance` $\rightarrow$ `Retired`.
* **Model Odoo:** `it_asset.asset` (`asset_type = 'it'`).

---

### [IT-2] Accessory
* **Definisi:** Barang pendukung perangkat kerja yang tidak perlu dicatat sebagai aset bernilai tinggi per unit.
* **Target:** Diberikan langsung ke **User / Workstation**.
* **Tracking:** **Quantity Tracking** (atau Serial Number jika ada part bernilai khusus).
* **Contoh:** Keyboard, Mouse, Kabel HDMI / DisplayPort, Adapter Laptop, USB Hub, Headset.
* **Alur Hidup:** `Stock Gudang` $\rightarrow$ `Issued to User`.
* **Model Odoo:** `product.product` / `it_asset.handover.accessory`.

---

### [IT-3] Spare Part
* **Definisi:** Komponen cadangan yang disimpan di gudang untuk perbaikan (*maintenance*) atau upgrade perangkat IT.
* **Target:** Dipasang ke **Target Asset IT**.
* **Tracking:** **Quantity** atau **Serial Number**.
* **Contoh:** RAM, SSD / HDD, Power Supply (PSU), Fan, Motherboard, Panel LCD Laptop.
* **Alur Hidup:** `Stock Gudang` $\rightarrow$ `Installed on Asset (Swap/Maintenance)` $\rightarrow$ `Scrapped`.
* **Model Odoo:** `product.product` / `it_asset.swap`.

---

### [IT-4] Tool
* **Definisi:** Peralatan kerja teknis IT yang dipakai berulang kali untuk pekerjaan maintenance, instalasi, dan perbaikan.
* **Karakteristik:** **Bukan consumable** dan **tidak diserahkan permanen**.
* **Alur Utama:** **Peminjaman & Pengembalian (*Borrow & Return*)**.
* **Tracking:** **Individual Tool ID** atau **Quantity**.
* **Contoh:** Multimeter Digital, Solder Station, Blower Hot Air, Tang Crimping RJ45, LAN Tester, Obeng Set, Bor Listrik.
* **Alur Hidup:** `Available di Workshop IT` $\leftrightarrow$ `Borrowed oleh Teknisi` $\rightarrow$ `Returned`.

---

### [IT-5] Consumable
* **Definisi:** Bahan material habis pakai untuk instalasi dan perawatan IT yang berkurang/habis saat digunakan.
* **Target:** Digunakan dalam pekerjaan IT (mengurangi stok gudang).
* **Tracking:** **Quantity Tracking** (Satuan / UoM).
* **Contoh:** Timah Solder, Flux Solder, Thermal Paste, RJ45 Modular Connector, Kabel UTP Roll, Cable Tie, Isolasi.
* **Alur Hidup:** `Stock Gudang IT` $\rightarrow$ `Consumed / Issued` (memicu `stock.move`).
* **Model Odoo:** `it_asset.consumable`.

---

## 4. Detail Domain Operation (3 Tipe)

### [OP-1] Operation Asset
* **Definisi:** Perangkat keras komunikasi, monitoring, dan telemetri yang dipasang di lapangan/area tambang atau terpasang pada unit armada (*Fleet*).
* **Target:** Terpasang pada **Fleet Unit** (`it_asset.unit`) atau **Lokasi Site / Pos Jaga**.
* **Tracking:** **Serial Number** (`stock.lot`) & **Asset Tag**, serta parameter khusus (seperti `radio_mode`: *Analog / Digital / Dual*).
* **Contoh:** Radio Rig (Motorola/Kenwood), Radio HT / Digital, CCTV Lapangan/Workshop, Repeater Radio, GPS Telemetri.
* **Alur Hidup:** `Available` $\rightarrow$ `Installed on Fleet Unit / Site` $\rightarrow$ `Swap / Maintenance` $\rightarrow$ `Retired`.
* **Model Odoo:** `it_asset.asset` (`asset_type = 'operation'`).

---

### [OP-2] Operation Consumable
* **Definisi:** Material dan komponen pendukung instalasi operasional lapangan yang habis pakai atau dipotong sesuai kebutuhan lapangan.
* **Target:** Pemasangan & perawatan radio lapangan / unit armada.
* **Tracking:** **Quantity Tracking** (Satuan: Pcs, Meter, Roll).
* **Contoh:**
  - Kabel Coaxial (RG58 / RG8 roll untuk antena radio)
  - Konektor Radio (Konektor PL-259, BNC, N-Type)
  - Bracket Radio Rig / Bracket Antena Unit
  - Fuse / Sekring DC Radio Rig (15A, 20A)
  - Antena Radio Standar (Larsen, Ring-O, Whip)
  - Rubber Duck Antenna HT
  - Isolasi Rubber Waterproof (Self-amalgamating tape untuk sambungan antena luar)
* **Alur Hidup:** `Stock Gudang IT/Radio` $\rightarrow$ `Used on Unit Installation`.
* **Model Odoo:** `it_asset.consumable` / `product.product`.

---

### [OP-3] Fleet (Operation Unit)
* **Definisi:** Unit armada kendaraan operasional dan alat berat tambang yang menjadi tempat terpasangnya aset operasional (terutama Radio Rig & CCTV).
* **Klasifikasi Fleet (`it_asset.unit.category`):** Dump Truck (DT), Excavator (EX), Water Truck (WT), Light Vehicle (LV), Dozer, Grader, dll.
* **Tracking:** **Unit Code / Unit Name** (contoh: `DT-01`, `EX-05`, `LV-02`).
* **Karakteristik & Status:** `Ready` $\leftrightarrow$ `Standby` $\leftrightarrow$ `Breakdown`.
* **Kaitan Sistem:** Memiliki daftar aset terpasang (*Installed Assets*). Saat radio dipasang, diperbaiki, atau ditukar (*Swap*), riwayatnya tercatat pada unit ini.
* **Model Odoo:** `it_asset.unit`.

---

## 5. Matriks Ringkasan Klasifikasi

| Domain | Type | Definisi Singkat | Target / Peruntukan | Tracking Utama | Model Odoo Utama |
|---|---|---|---|---|---|
| **IT** | **Asset** | Perangkat IT personal & kantor | Employee / User | Serial Number | `it_asset.asset` (`it`) |
| **IT** | **Accessory** | Perlengkapan pendukung IT | User / Workstation | Quantity | `product.product` |
| **IT** | **Spare Part** | Komponen ganti / upgrade IT | Target Asset IT | Qty / Serial | `it_asset.swap` |
| **IT** | **Tool** | Peralatan kerja teknisi IT | Peminjam (Teknisi) | Individual / Qty | *Borrow & Return* |
| **IT** | **Consumable** | Material habis pakai IT | Pekerjaan IT | Quantity (UoM) | `it_asset.consumable` |
| **Operation** | **Asset** | Radio & perangkat lapangan | Fleet Unit / Site | Serial Number | `it_asset.asset` (`operation`) |
| **Operation** | **Consumable** | Material instalasi radio/lapangan | Pemasangan Unit | Quantity (UoM) | `it_asset.consumable` |
| **Operation** | **Fleet** | Alat berat & kendaraan tambang | Induk Pasang Radio | Unit Name / Code | `it_asset.unit` |

---

## 6. Pemetaan Workflow Transaksi & Kiosk

```text
┌───────────────┬───────────────────────┬───────────────────────────────┬───────────────────────────────────┐
│    DOMAIN     │         TYPE          │        WORKFLOW / AKSI        │         DATA YANG DIINPUT         │
├───────────────┼───────────────────────┼───────────────────────────────┼───────────────────────────────────┤
│ **IT**        │ Asset                 │ Handover / Assignment         │ Serial Number + Employee (User)   │
│ **IT**        │ Accessory             │ Issue / Handover              │ Qty + User Penerima               │
│ **IT**        │ Spare Part            │ Part Replacement / Swap       │ Qty/Serial + Asset Target IT      │
│ **IT**        │ Tool                  │ Borrow & Return               │ Tool ID/Qty + Peminjam + Durasi   │
│ **IT**        │ Consumable            │ Material Usage / Issue        │ Qty + Keperluan Pekerjaan IT      │
├───────────────┼───────────────────────┼───────────────────────────────┼───────────────────────────────────┤
│ **Operation** │ Asset (Radio/CCTV)    │ Installation / Swap / Site    │ Serial Number + Fleet Unit / Site │
│ **Operation** │ Consumable            │ Material Usage (Kabel/BNC)    │ Qty + Unit / Keperluan Radio      │
│ **Operation** │ Fleet                 │ Unit Status & Installed Asset │ Unit Code + Category + State      │
└───────────────┴───────────────────────┴───────────────────────────────┴───────────────────────────────────┘
```

---

## 7. Kesimpulan & Penyelarasan Sistem

1. **Kejelasan Tanggung Jawab:**
   * Memperjelas bahwa aset dan material **Operation** (Radio, Kabel Antena, Bracket, dan Unit Fleet) tetap dikelola secara teknis oleh tim IT, namun peruntukan operasionalnya khusus untuk lapangan tambang.
2. **Sinkronisasi Odoo & Kiosk:**
   * Di Odoo:
     - Menu **Asset Management $\rightarrow$ IT** menangani IT Assets.
     - Menu **Asset Management $\rightarrow$ Operational** menangani Radio Rig/HT & Operational Assets.
     - Menu **Fleet** menangani Unit Alat Berat / Kendaraan Tambang.
     - Menu **Consumables** membedakan material IT vs material Operation.
   * Di Kiosk:
     - Alur transaksi memfilter input sesuai domain (IT vs Operation) dan tipe pengelolaannya.
