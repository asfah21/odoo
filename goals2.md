# Ultimate Goal — Ask AI Odoo IT Management Suite (PT GSI – Site Wolo)

## 1. Konteks Proyek Ini

Dokumen ini menggantikan versi generik sebelumnya dan diselaraskan dengan repo ini:

- **Produk**: *Odoo IT Management Suite* — modul **IT Department** (`it_asset`) untuk **PT GSI – Site Wolo**.
- **Framework**: Odoo 18 (Python 3.10+), PostgreSQL 16, Docker (`odoo:18`, `Dockerfile`, `docker-compose.yml`, `config/odoo.conf`).
- **Depends**: `base`, `product`, `hr`, `stock`, `web`, `mail` — lihat `addons/it_asset/__manifest__.py`.
- **Domain data** (lihat `Type Barang.md`):
  - **IT (5 tipe)**: Asset, Accessory, Spare Part, Tool, Consumable.
  - **Operation (3 tipe)**: Asset (Radio Rig/HT, CCTV, Repeater), Consumable, Fleet/Unit (`DT`, `EX`, `LV`, `WT`).
- **Fitur Ask AI**:
  - Frontend OWL: `addons/it_asset/static/src/components/ask_ai/ask_ai.js`
  - Backend orkestrasi: `addons/it_asset/models/ask_ai.py` (tool ORM, perakitan HTML, antrean feedback)
  - Backend NLU: `addons/it_asset/models/ask_ai_nlu.py` (murni stdlib, tanpa `import odoo`, tanpa akses DB)

Ask AI adalah **AI Agent read-only di dalam Odoo** yang menjawab pertanyaan tentang **data live modul IT** — stok, aset, pengguna aset, kondisi, riwayat, maintenance, handover, damage, request.

## 2. Tujuan Utama

Membangun **chatbot AI yang smart, cepat, ringan, dan dapat diandalkan** di dalam Odoo, dengan menjalankan **Qwen3-0.6B (`farpluto/Qwen3-0.6B-Q4_K_M-GGUF`) secara lokal CPU-only** (`llama-server`, port `8081`), tanpa ketergantungan API/model besar.

Meskipun memakai model kecil, Ask AI harus terasa seperti AI Agent besar, yaitu mampu:

- memahami konteks dan percakapan multi-turn secara natural (tanpa user mengulang info seperti `ITLT-007`, `PRN-01`);
- memahami maksud user walau tidak terstruktur, singkat, typo, atau bahasa sehari-hari/slang Indonesia (normalisasi `_SLANG_TABLE` di `ask_ai_nlu.py`);
- **membaca kode berantakan ala user lapangan (V2, lihat §9)**: `itlt-002` = `itlt02` = `ITLT 002`, `dt 02` = `dt.02` = `dt02` = `DT-02`, `exca` = `excavator`/`EX` — tanpa user harus hafal format resmi;
- mempertahankan konteks dan menangani pindah topik tanpa kehilangan konteks penting;
- memakai data, tools ORM, dan knowledge base Odoo secara tepat hanya saat diperlukan (Qwen tidak menyentuh DB);
- menjawab pertanyaan di luar cakupan (out-of-domain) secara natural dan bervariasi, bukan fallback kaku (scope reply + `ood_rule`);
- menghasilkan variasi jawaban natural, tidak repetitif seperti template (rotasi deterministik hash+counter);
- tetap **akurat dan grounded**: jawaban faktual **hanya** dari evidence ORM, tanpa fabrikasi; jika data tidak ada → `data_miss`/klarifikasi jujur;
- memiliki latency rendah dan ringan di server terbatas (CPU-only, single-slot, `--ctx-size 2048 --parallel 1 --reasoning off`);
- dan yang paling penting: terasa benar-benar memahami user, bukan chatbot kecil yang sekadar mencocokkan intent.

**Prinsip utama**: bukan membuat Qwen 0.6B menjadi model besar, tetapi membangun **AI orchestration (metode WACS yang diport ke Python)** sehingga model kecil memberi pengalaman layaknya AI Agent jauh lebih besar.

## 3. Pembagian Peran (Wajib Dijaga)

- **Qwen = language understanding saja.** Hanya menebak `{intent, confidence}` di `[L1] llm_decide()`. Tidak memilih tool, tidak menyentuh DB, tidak merangkai kalimat. Default nonaktif (`LLM_ENABLED = False`, `it_asset.ask_ai.llm_enabled = False`).
- **Python/Odoo = business logic.** Semua keputusan, query, dan kalimat jawaban dari backend (`ask_ai.py`).
- **NLU tidak pernah menyentuh database.** Entity dari extractor lokal; nilai model hanya diadopsi bila *grounded* (substring teks user).
- **JS hanya merender.** Frontend memanggil `orm.call("it_asset.ask_ai", "answer", [text])` dan menampilkan HTML.

Alur per pesan:

```text
USER
  ↓
[L1] llm_decide() → {intent, confidence} (opsional, cooldown 60 dtk jika gagal)
  ↓
[fast-path] sosial (greeting/thanks/goodbye/help) → canned, tanpa tool
[fast-path] ood_rule → scope reply bervariasi, tanpa tool
  ↓
[L2] asset_query_override → tool langsung (confidence 1.0)
  ↓
[L3] classify penuh → route(confidence): handover / klarifikasi / tool
  ↓
tool ORM → evidence → HTML grounded
  ↓
feedback tak-yakin → antrean kurasi `it_asset.ask_ai.feedback`
```

Routing: `confidence < floor → handover staff IT`; `floor <= c < threshold → klarifikasi`; `>= threshold → eksekusi tool`. Ambang: `factual 0.90`, `general 0.75`, `other 0.80`, floor `min(0.70, threshold)`.

## 4. Cakupan Intent & Tool (Read-Only)

Sumber kebenaran: `ALL_INTENTS` di `ask_ai_nlu.py`, pemetaan `_TOOLS` di `ask_ai.py`.

| Intent | Tool | Deskripsi |
| --- | --- | --- |
| `recap` | `_tool_recap` | Rekap jumlah aset & stok menipis |
| `check_stock` | `_tool_check_stock` | Stok consumable (`low_only` opsional); untuk barang berupa aset (mis. radio HT) dijawab dari aset **tersedia/belum assign** (`_tool_asset_stock`), bukan consumable |
| `asset_search` | `_tool_asset_search` | Pencarian aset IT/Operation |
| `asset_detail` | `_tool_asset_detail` | Detail aset (mis. `ITLT-007`) |
| `asset_user` | `_tool_asset_user` | Siapa pemakai aset |
| `asset_history` | `_tool_asset_history` | Riwayat aset (mis. `PRN-01`) |
| `maintenance_list` | `_tool_maintenance_list` | Daftar maintenance |
| `handover_list` | `_tool_handover_list` | Daftar serah-terima/BAST |
| `damage_list` | `_tool_damage_list` | Daftar kerusakan |
| `request_status` | `_tool_request_status` | Status permintaan aset/akun |
| `human_agent` | `_tool_human_agent` | Eskalasi ke staff IT |
| `greeting` / `thanks` / `goodbye` / `help` | — (canned) | Fast-path sosial |
| `unknown` | — (scope/OOD reply) | Di luar cakupan |

**Batasan keras**: Ask AI **read-only**. Tidak ada tool write. Semua perubahan (handover, request, damage report, swap, assignment) tetap lewat form Odoo.

## 5. Non-Goals

- Bukan fine-tuning / melatih model besar. Qwen tetap 0.6B; kualitas dari orkestrasi.
- Bukan LLM mengarang jawaban. Tanpa evidence → `data_miss`/klarifikasi.
- Bukan agent yang mengubah data atau mem-bypass workflow Odoo (Draft → Submitted → Approved → Fulfilled, dsb.).
- Bukan ketergantungan cloud/API eksternal. Harus jalan CPU-only di server terbatas.

## 6. Konfigurasi Runtime Qwen

1. Unduh `qwen3-0.6b-q4_k_m.gguf` dari `farpluto/Qwen3-0.6B-Q4_K_M-GGUF`.
2. Jalankan:
   ```bash
   llama-server -m qwen3-0.6b-q4_k_m.gguf --port 8081 --ctx-size 2048 --parallel 1 --reasoning off
   ```
3. System Parameters Odoo:

   | Parameter | Default |
   | --- | --- |
   | `it_asset.ask_ai.llm_enabled` | `False` |
   | `it_asset.ask_ai.llm_url` | `http://127.0.0.1:8081` |
   | `it_asset.ask_ai.llm_model` | `qwen3-0.6b` |
   | `it_asset.ask_ai.llm_timeout` | `10` |
   | `it_asset.ask_ai.llm_json_mode` | `True` |

Tanpa runtime, L1 selalu menyerah ke L2/L3 — ini desain, bukan kegagalan.

## 7. Loop Perbaikan

Pesan tak-yakin masuk `it_asset.ask_ai.feedback` via `_record_feedback` + `capture_reason`. Kurasi manual (`curated_intent` / `curated_not_intent` → **Terkurasi**). Aturan tetap: korpus ukur ≠ korpus latih — contoh nyata tidak disalin langsung ke `EXEMPLARS`; dipakai untuk perbaiki rule/threshold secara sadar.

## 8. Kriteria Keberhasilan

- Faktual (stok, aset, pengguna, riwayat) dijawab tepat dan 100% grounded dari Odoo.
- OOD dijawab bervariasi, tidak terkesan template.
- Typo/slang Indonesia tetap dipahami.
- Kode berantakan V2 (§9) tetap ketemu datanya.
- Ringan: CPU-only, single-slot, latency wajar, tanpa API eksternal.
- Antrean feedback terkelola untuk perbaikan berkelanjutan.
- Tidak ada jalur chat yang dapat mengubah data.

## 9. Versi 2 — Paham Input Berantakan ala Lapangan (Fuzzy Ref)

User lapangan mengetik cepat dan tidak hafal format resmi. Ask AI **wajib paham**, bukan menyuruh user mengetik ulang dengan format sempurna.

### 9.1 Contoh yang harus dipahami

- **Aset IT**: `carikan itlt-002` = `carikan itlt02` = `carikan ITLT 002` = `carikan itlt.002` → semua dibaca sebagai `ITLT-002`.
- **Fleet/Unit**: `dt 02` = `dt.02` = `dt02` = `DT-02`; `dt.02.02` → dibaca `DT-02` (abaikan ekor berlebih); `LV 2` → `LV-02`.
- **Alias kategori**: `exca` = `excavator` = `EX` (Excavator); `dt` = `dump truck`/`Dump Truck`; `wt` = `Water Truck`; `lv` = `Light Vehicle`; `dozer`, `grader` tetap dikenali.
- Kombinasi natural: `riwayat dt 02`, `siapa pakai itlt02?`, `cari exca yang breakdown`, `radio di dt-02`.

### 9.2 Aturan pemahaman

- **Case-insensitive**: `ITLT`, `itlt`, `ItLt` sama saja.
- **Toleran pemisah**: spasi / titik / strip / underscore sama saja (`DT 02`, `DT.02`, `DT-02`, `DT_02`, `DT02`).
- **Toleran nol depan**: `2` = `02` = `002` untuk nomor urut (`LV 2` ketemu `LV-02`).
- **Tanpa kata kunci pun paham**: pesan super singkat seperti `itlt02` atau `dt 02` langsung dianggap `asset_detail` (bukan `unknown`).
- **Tetap grounded**: kalau kode sudah dikanonikalisasi tapi tidak ada di DB → jawab `data_miss` jujur + saran format, jangan mengarang.
- **Acuan implementasi**: kanonikalisasi di `ask_ai_nlu.py` (`normalize_id` + varian ref), pencarian pakai OR antar-varian + `unit_id.name` di `_asset_domain_for` (`ask_ai.py`).

## 10. UI Interaktif + Riwayat Terhapus Otomatis 10 Hari

- **UI interaktif**: topbar (toggle sidebar mobile, brand + status online, badge Live Data, tombol Clear + New Chat), sidebar (search percakapan, daftar riwayat + hapus per sesi, tips + catatan retensi), hero welcome + 4 kartu saran, bubble avatar user/AI, typing indicator, composer auto-grow + hint keyboard.
- **Riwayat di backend, bukan memori**: sesi + pesan tersimpan di `it_asset.ask_ai.session` / `it_asset.ask_ai.message` (`addons/it_asset/models/ask_ai_history.py`). Setiap `answer()` otomatis menyimpan pasangan pesan user+AI dan mengembalikan `session_id` (kompatibel mundur dengan pemanggil lama). Klik riwayat memuat isi dari backend (bukan kirim ulang prompt).
- **Retensi 10 hari**: cron harian `Ask AI: hapus riwayat chat > 10 hari` (`data/ask_ai_cron.xml`) menghapus sesi yang `last_seen`-nya lebih tua dari N hari (default 10, diatur via System Parameter `it_asset.ask_ai.history_retention_days`); pesan ikut terhapus via ondelete cascade. Batas ini juga tertulis di UI (badge topbar + footer sidebar).
- **Penting setelah update kode**: modul **wajib di-upgrade** (`-u it_asset`) + hard-refresh browser, karena model `session`/`message`, cron, dan JS baru tidak aktif sebelum itu. Chat lama sebelum upgrade memang tidak muncul (dulu hanya dummy di memori, tidak pernah tersimpan).

## 11. Paham Domain: HT vs Rig, IT vs Operasional, Status, Kategori/Tipe/Spek

- **Radio HT vs Radio Rig**: keduanya berkategori `Radio Rig` di master data — dibedakan via `radio_kind` (`rig`/`ht`) yang menyaring nama/produk (`ask_ai_nlu.resolve_radio_kind` + filter di `_tool_asset_search`). Kalau tak ada yang namanya persis cocok, fallback jujur ke semua radio berlabel (bukan miss menyesatkan). `ht`, `rig`, `handy talky` berdiri sendiri juga dipahami.
- **Kata kategori polos selalu dijawab**: `cctv`, `desktop`, `gps`, `tablet`, `router`, … (`list cctv` → daftar CCTV, bukan klarifikasi). Prefix tag tanpa angka ikut menentukan kategori dari data asli (`itct` → CCTV, `itlt` → laptop, `prn` → printer).
- **Aset IT vs Operasional**: entitas `asset_type` (`aset it` → `it`; `operasional`/`operasi`/`operation` → `operation`) menjadi filter `asset_type` di pencarian dan pertanyaan pengguna (`_tool_asset_user`).
- **Status tersedia/available**: `tersedia`, `available`, `ready` → `available`; dikombinasikan dengan kategori/domain lain. `berapa stok radio ht` = HT yang `available`/belum di-assign (kalau kosong dan semua dipakai, ditampilkan siapa pemakainya).
- **Stok cerdas**: kategori berupa aset (radio/laptop/…) diutamakan ke data aset tersedia — `mic radio rig` tidak tertukar `radio rig`; kata produk spesifik (`mic`) tetap ke consumable. Kata material lapangan (`konektor`, `adaptor`, `antena`, `bnc`, `kabel`…) selalu ke cek stok, termasuk typo (`daptor`). Pencarian consumable berlapis frasa → AND-kata → OR-kata.
- **Kata info**: `tipe`/`type`/`jenis`/`kategori` → pencarian; `spek`/`spesifikasi` → detail per aset.
- **Detail aset menampilkan**: Tipe (`IT/Operasional • Asset/Accessory/…`), Model, Spesifikasi, Kategori, Serial, Produk, Status, Kondisi, Pengguna + riwayat.

## 12. Konfirmasi Balik + Identitas

- **Konfirmasi saat ambigu**: kalau hasil lebih dari 1 (pengguna/detail/riwayat), AI balik tanya — tampilkan daftar tag + nama, minta balas **nomor aset/SN spesifik** atau ketik **“semua”**. Berlaku umum untuk semua tool berbasis ref. Contoh: `radio rig siapa yang pakai?` → jawab daftar + `Mau detail salah satunya? Balas nomor aset/SN-nya` (+ `semua` bila >8).
- **Ingat pilihan**: kandidat disimpan di sesi (`pending_action`/`pending_ids`/`pending_label` di `it_asset.ask_ai.session`); pesan berikutnya `semua`/`ya` → tampilkan semua, tag cocok → eksekusi satu itu, pertanyaan baru → pending dibersihkan otomatis.
- **Tak yakin → tanya balik dulu**: skor di bawah floor + ada sinyal inventaris (ref/kategori/status/form/… via `has_meaningful_signal`) → klarifikasi terarah, BUKAN langsung handover. Handover ke staff hanya bila benar-benar tidak paham sama sekali (gibberish/curhat/OOD murni).
- **Ingatan topik antar-pesan**: kategori/domain terakhir tersimpan di sesi (`last_category`/`last_asset_type`/`last_radio_kind`, kedaluwarsa 60 menit) — `yang rusak?` setelah `stok cctv` dibaca sebagai CCTV rusak. Topik baru (ref/kategori disebut) selalu menang.
- **Identitas**: `kamu siapa?` / `fungsimu apa?` → jawab GSI IT Assistant + fungsinya (intent `identity`); `siapa yang buat?` / `developernya siapa?` → jawab **Azvan, IT Department PT GSI (Site Wolo)** (intent `creator`). Keduanya canned deterministik (fast-path + jaring pengaman tool).

## 13. Tanya Fleet + Baca Semua Form

- **Fleet/unit (intent `unit_detail`)**: kode `DT/EX/LV/WT` selalu soal unit — `dt 02.07 itu merek apa?` → kartu unit (merek, model, kategori, status, aset terpasang). Kode polos (`dt 02`) → tanya balik `mau tau informasi apa?` (merek/status/aset/riwayat/`semua`), diingat via pending; lanjutan `merek`/`status`/`aset`/`riwayat` menjawab fokus, `semua`/kode lagi → kartu lengkap.
- **Form handover**: baca `it_asset.handover` (BAST) + `it_asset.item.handover` sekaligus — filter status (signed/draft/belum/sudah) dan periode (hari ini/kemarin/minggu ini/bulan ini/bulan lalu), plus filter aset bila disebut tag. Contoh: `bast kemarin`, `serah terima tanggal berapa`.
- **Form request**: baca material + asset + account request sekaligus — filter jenis (`material`/`asset_request`/`account`), status (`fulfilled` vs `belum` = draft/submitted/approved/partially, plus status spesifik), dan periode. Tiap jenis diringkas `X fulfilled • Y belum`. Contoh: `pengajuan material yang belum`, `asset request approved`.
- **Form damage**: filter status (resolved/confirmed/draft/belum/sudah), jenis (fisik/sistem/hilang), periode, dan aset. Tanpa filter + data kosong → tetap kabar-baik (bukan miss).
- **Aturan status**: kata `belum/pending/menunggu` selalu menang atas kata spesifik (`belum fulfilled` → open, bukan fulfilled).

## 14. Sumber Kebenaran Domain + Anti Bot Kaku

- **IT vs Operasional dari DB, bukan tebakan kode**: `asset_type` (`it`/`operation`) + `it_type` + `category_id` dari database. Setiap baris aset berlabel lencana **IT** / **OPS** (`_asset_table`), detail menampilkan `Tipe: IT/Operasional • Asset/…`, kartu unit menyatakan `Aset operasional (fleet) — bukan IT`.
- **Prefix fleet = `DT/EX/LV/WT`** (sesuai master `it_asset.unit.category`: Dump Truck, Water Truck, Excavator, Light Vehicle; terpusat di `FLEET_PREFIXES`). Fleet selalu operasional.
- **Bervariasi biar tidak kentara bot**: sapaan (3 varian + selamat pagi/siang/sore/malam ikut jam server), terima kasih (3), selamat tinggal (2), bantuan (2), identitas (2), klarifikasi (3 template), konfirmasi multi-kandidat (3 bukaan), klarifikasi unit (2 bukaan) — semua rotasi deterministik hash+counter.
- **Variasi pertanyaan dipahami**: slang/typo diperluas (`liat`, `kasitau`, `coba`, `stokc`, `monitr`, `keybord`, `pritner`…), kata kerja tanya (`cari`, `lihat`, `tampilkan`, `kasih`…) dan kata benda umum (`aset`, `asset`) tidak mengotori keyword pencarian.
