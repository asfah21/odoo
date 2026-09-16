# AIMODE — Dokumentasi Mesin Ask AI (IT Department, Odoo 18)

> Satu dokumen untuk memahami **semua** cara kerja Ask AI: file per file,
> alur Alpha & Beta, flow percakapan, kamus, Qwen, persona, guard
> anti-halusinasi, plus saran perbaikan. Diperbarui mengikuti kode
> (`addons/it_asset/`).

---

## 1. Gambaran Besar

Ask AI adalah **chatbot inventaris read-only di dalam Odoo** (menu `IT → AI → Chat`).
Ia menjawab dari **data live modul IT**: stok, aset, pengguna aset, kondisi,
riwayat, maintenance, handover, damage, request, dan unit/fleet.

Prinsip arsitektur (diadopsi dari pola Rasa CALM + metode WACS):

- **Pemahaman boleh fleksibel, eksekusi harus deterministik.** Penebak maksud
  (rule / TF-IDF / Qwen) hanya menghasilkan `{intent, slots, confidence}`.
  Yang menyentuh database dan merangkai jawaban hanyalah tool ORM Python.
- **Qwen tidak menyentuh DB dan tidak merangkai fakta.** Fakta selalu dari
  evidence ORM; tanpa evidence → `data_miss`/klarifikasi jujur, bukan karangan.
- **Read-only keras.** Tidak ada tool tulis. Semua perubahan data tetap lewat
  form Odoo (handover, request, damage report, dsb).
- **Ringan.** Tanpa Qwen pun bot jalan penuh. Qwen hanya
  opsional: menebak maksud cadangan (decide) dan memoles bahasa (rephrase).

Satu jalur pemahaman (mode Beta eksperimen sudah dihapus):

| | **Ask AI** |
|---|---|
| Penebak maksud | Rule regex → override → TF-IDF → Qwen cadangan |
| Butuh Qwen? | Tidak (opsional: decide cadangan + rephrase) |
| Kecepatan | Milidetik (lokal) |
| Qwen mati? | Tetap jalan normal |

---

## 2. Peta File

```
addons/it_asset/
├── models/
│   ├── ask_ai_nlu.py        # Otak deterministik: rule, override, OOD, TF-IDF, kontrak LLM
│   ├── ask_ai_commands.py   # Polisi: skema command, flow murni, repair, guard anti-halu
│   ├── ask_ai_persona.py    # Persona Alya/Raka, deteksi bahasa, kupas nama
│   ├── ask_ai.py            # Orkestra: tool ORM, flow runner, rephrase, riwayat
│   ├── ask_ai_dictionary.py # Model kamus (it_asset.ask_ai.term)
│   ├── ask_ai_settings.py   # Model setting singleton (it_asset.ask_ai.setting)
│   └── ask_ai_history.py    # Model sesi + pesan chat
├── views/
│   ├── ask_ai_views.xml         # Menu IT → AI → Chat/Setting/Dictionary
│   ├── ask_ai_setting_views.xml # Layar Setting (English)
│   ├── ask_ai_dictionary_views.xml # Layar Dictionary (English)
│   └── ask_ai_history_views.xml # Layar riwayat sesi (+ grup debug flow)
├── static/src/components/ask_ai/
│   ├── ask_ai.js    # Thin client OWL: render + kirim mode
│   ├── ask_ai.xml   # Template: topbar, sidebar (mode picker), hero, bubbles
│   └── ask_ai.scss  # Style chat
├── tests/
│   └── test_ask_ai_flows.py # Conversation tests multi-turn (15 skenario, tanpa Odoo)
├── data/ask_ai_setting_data.xml # 1 baris Setting default
├── data/ask_ai_cron.xml         # Cron hapus riwayat > 10 hari
├── aimode.md                    # <-- dokumen ini
├── docker-compose.llm.yml       # Service llama-server (repo root)
├── llm/README.md                # Panduan install Qwen (repo root)
└── scripts/install-qwen.*       # Installer Windows/Linux (repo root)
```

---

## 3. `ask_ai_nlu.py` — Otak Deterministik (murni stdlib)

Tanpa `import odoo`, tanpa akses DB → bisa diuji mandiri (`python ask_ai_nlu.py`).

**3.1 Sumber kebenaran tunggal.** `ALL_INTENTS`, `INTENT_CATEGORY`,
`CONF_THRESHOLDS`. Kategori → ambang: faktual (`check_stock`, `asset_user`,
`asset_history`) = **0.90**; umum (`greeting`, `thanks`, `goodbye`, `help`,
`identity`, `creator`) = **0.75**; lainnya = **0.80**. Floor handover =
`min(0.70, threshold)`.

**3.2 Normalisasi Bahasa Indonesia.** Tabel slang (`brp→berapa`, `stokc→stok`,
`pritner→printer`, `availabe→available`, `daptor→adaptor`, …) + lipat
huruf kecil + buang tanda baca. Plus toleransi kode berantakan V2:
`itlt-002` = `itlt02` = `ITLT 002`, `dt 02` = `dt.02` = `DT-02`, alias
`exca` = excavator, toleransi nol depan (`LV 2` → `LV-02`).

**3.3 Pipeline `classify()` (L1→L2→L3).**
- **L1 `llm_decide`** — titik ekstensi Qwen, kontrak `{intent, entities,
  constraints, confidence}`. Nonaktif default → selalu menyerah.
- **L2 `asset_query_override`** — sinyal kuat → intent langsung confidence 1.0
  (kode tag/unit, kata stok, kata rekap, riwayat+ref). Menolak topik non-IT.
- **L3 `Classifier`** — (a) rule regex teks mentah, (b) rule pada teks
  ternormalisasi, (c) rule luar-domain eksplisit `ood_match` (cuaca, matematika,
  jam, identitas-diri, personal, trivia, **langit/biru/bintang/… dengan guard
  kata-benda in-domain**), (d) TF-IDF cosine + gate
  (`MIN_NLU_SIMILARITY 0.30`, `NLU_WINNER_MARGIN 1.4`, `LONE_MATCH_FACTOR 1.5`,
  `MIN_DISTINCTIVE_IDF 3.8`, skala kalibrasi ×1.15): unigram+bigram+char
  3–4gram. Aturan tetap: korpus ukur (`_SELF_TEST_CASES`) ≠ korpus latih
  (`EXEMPLARS`) — contoh uji tidak boleh disalin ke exemplar.

**3.4 Entity extractor** (kanal terpisah dari constraints): `asset_refs`,
`category` (+alias fleet, +prefix tag `itct→cctv`), `radio_kind` (HT vs Rig),
`asset_type` (IT vs Operasional), `form_kind/status/period`, `top_kind`,
`employee_name`, `item`, `state`, `condition`; constraints: `low_only`.

**3.5 Routing & feedback.** `route()`: `< floor` → handover staff;
`floor–threshold` → klarifikasi; `≥ threshold` → eksekusi tool.
`capture_reason()` memilih pesan tak-yakin masuk antrean kurasi
(`it_asset.ask_ai.feedback`).

**3.6 Kontrak LLM.** `decision_system_prompt()` dibangun dari `ALL_INTENTS`
(tambah intent otomatis masuk prompt); `parse_decision()` memvalidasi JSON
(intent valid, bukan `unknown`, confidence 0–1, kunci entities/constraints
tertutup).

---

## 4. `ask_ai_commands.py` — Polisi + Flow Murni (murni stdlib)

**4.1 Command schema (A).** Allowlist `COMMAND_TOOLS` + slot per command
(`COMMAND_SLOTS`) + syarat minimal (`COMMAND_ANY_OF`) + `validate_slot()`
(format ref, enum status/periode/jenis, panjang nama). `build_command()`
→ `{command, slots, valid, errors}`. Slot tak dikenal dibuang; perintah
tak-valid tak pernah mencapai tool.

**4.2 Repair detection (B).** `detect_cancel` (hanya kata batal eksplisit —
"sudah fulfilled" **bukan** batal), `detect_resume` (`lanjut/kembali`),
`detect_correction` (`ralat/maksud saya/bukan X` → payload), `detect_show_all`
(`semua/ya`), `detect_self_correct` (`halu/ngawur/ngaco` → momen minta maaf),
`social_grounded()` (tebakan sosial LLM wajib punya jangkar kata),
`ood_wins_over()` (preseden OOD, lihat §7).

**4.3 Flow engine data-driven (B+F).** `FLOW_DEFS`: `confirm_asset`
(tag spesifik atau semua?), `unit_clarify` (merek/status/aset/riwayat),
`guided_broken` (tanya kategori → eksekusi `asset_search` + `condition=broken`).
`flow_next()` murni (tanpa I/O, tanpa Odoo) → teruji penuh. `flow_signal()`
memetakan pesan ke sinyal — dipakai backend DAN test agar selalu sejalan.

**4.4 Guard anti-halu (F1/F2).** `is_grounded_code()` (kode hanya boleh disebut
bila substring pertanyaan asli), `invented_codes()` (kode/angka di hasil poles
yang tak ada di aslinya).

---

## 5. `ask_ai_persona.py` — Alya & Raka (murni stdlib)

- **Alya:** ramah, sopan, perhatian; short, casual, emoji ringan.
- **Raka:** laki-laki, remaja, friendly, helpful (+santai, natural, youthful,
  conversational); nama bisa dikonfigurasi (`persona_name`).
- Kontrak bersama: Indonesia (atau English bila user English), tidak memaksa,
  menawarkan bantuan, mengakui jika tidak tahu, minta klarifikasi bila kurang,
  tidak mengarang. Restrictions (dipetakan ke guardrail backend, bukan janji
  kosong): no hallucination / invented stock-product-price (= evidence ORM +
  `data_miss` jujur), no fake discount (= bot tak pernah tampilkan diskon),
  no false promises (= tak-yakin → klarifikasi/handover).
- Util: `detect_language()` (heuristik ID/EN), `strip_persona_name()`
  ("Alya, stok…?" → "stok…?"; nama saja → dibaca greeting),
  `identity_text()`, `rephrase_persona_block()` (blok persona hemat-token
  untuk system prompt rephrase), varian sosial English.

---

## 6. `ask_ai.py` — Orkestra + Tool ORM

**Preprocess** (`_preprocess`): kosong →
teguran-halu (F4) → strip nama + bahasa → koreksi kamus → vonis OOD pra-kamus.

**Jalur jawab** (`_answer_inner`): rule/override → OOD (jenis percakapan dulu;
hanya unknown yang di-redirect) → resolusi konteks sesi (relation
NEW/FOLLOW_UP/REFINE/CORRECT/CONTINUE + warisan intent/entity turn lalu,
goals.md) → LLM cadangan (opsional)
→ demote sosial-tak-grounded → route → command → validasi DB → tool →
rephrase (opsional). Konteks (intent + entity) disimpan per sesi
(`last_intent`/`last_entities`, kedaluwarsa 60 mnt, jejak log `Ask AI ctx`).

**Tool ORM** (evidence → HTML, semua di-escape): `recap`, `check_stock`
(cerdas: kategori berupa aset → stok unit tersedia, bukan consumable),
`asset_search` (+fleet ke `it_asset.unit`), `asset_detail` (profil + riwayat),
`asset_user`, `asset_history`, `asset_top` (tertua/terbaru/sering pindah/rusak),
`maintenance_list`, `handover_list`, `damage_list`, `request_status`,
`unit_detail`, `human_agent`, `identity`, `creator`. Multi-kandidat →
konfirmasi ("balas tag/SN atau *semua*").

**Flow runner** (`_consume_flow`, dipakai kedua mode): repair dulu
(batal/ralat/lanjut), sinyal via `flow_signal()`, delegasi render ke logika
pending, topik baru → push flow lama ke stack (cap 3, bisa `kembali`).
State flow (`flow_name/step/slots/stack`) tersimpan di sesi; sesi legacy
`pending_*` dijembatani otomatis.

**Qwen.** Dua peran terpisah: *decide* (tebak `{intent, confidence}`,
temperature 0.1, max 192 token, JSON mode, cooldown 60 dtk saat gagal) dan
*rephrase* (poles pembuka/penutup, temperature 0.7, max 150 token, timeout
8 dtk; guard menolak fakta hilang DAN fakta baru; gagal → jawaban asli).
Runtime: `llama-server`, Qwen3-0.6B-Q4_K_M (~397 MB), CPU-only
(`--ctx-size 1024 --threads 2 --parallel 1 --reasoning off`, port 8081).
Entitas LLM hanya diadopsi bila grounded (substring teks user).

**Riwayat.** Sesi + pesan per user, retensi cron 10 hari (parameter
`history_retention_days`), ingatan topik 60 menit, ingatan kandidat flow.

---

## 7. Kamus, Setting, OOD-Preseden, Guard ( Formula Anti-Ngaco )

- **Kamus** (`it_asset.ask_ai.term`): snapshot produk, kode, kategori, harga,
  stok, aset, unit, karyawan via tombol **Generate Dictionary** di Setting.
  Pencocokan typo-tolerant (edit-distance ≤2, huruf awal sama, cache 120 dtk)
  — tanpa token API, tanpa Qwen.
- **Setting** (`it_asset.ask_ai.setting`, singleton via server action):
  Qwen Decide, Qwen Rephrase, Persona (Alya/Raka + nama + traits), Auto
  Dictionary (statistik + generate + test koneksi), retensi. Semua nilai
  dicerminkan ke `ir.config_parameter`.
- **F3b OOD-precedence:** vonis OOD dihitung pada teks pra-kamus dan menang
  atas jalur lemah (`empty`/`dict`/`nlu`) serta LLM ragu; hanya rule/override
  dan LLM yakin-EXECUTE boleh menang.
- **F1/F2/F4/F6b:** kode disebut hanya bila grounded; poles dilarang
  memunculkan kode/angka baru; teguran halu → minta maaf + kurasi; tebakan
  sosial LLM tanpa jangkar kata → turun ke klarifikasi.

---

## 8. Frontend & Operasional

- `ask_ai.js` (thin client, tanpa logika bisnis): kirim `answer(text,
  session_id)`, render HTML backend, quick replies, riwayat backend.
- `ask_ai.xml`: topbar, sidebar (riwayat + retensi), hero, bubbles, composer.
- Menu: `IT → AI → Chat / Setting / Dictionary`.
- Operasional: `docker-compose.llm.yml` (image resmi
  `ghcr.io/ggml-org/llama.cpp:server` + healthcheck), `scripts/install-qwen.*`,
  `MODEL_DIR`/`HF_REPO`/`HF_FILE` dapat dioverride (alternatif Indonesia:
  `sail/Sailor-0.5B-Chat-gguf`).
- Setelah ubah kode: `-u it_asset` + hard-refresh browser. Setelah ubah
  seating jaringan Docker: URL di Setting menunjuk nama service
  (`http://odoo-llm-qwen06:8081`), bukan `127.0.0.1`.

---

## 9. Saran Perbaikan (Prioritas)

1. **Vonis eksperimen Alpha vs Beta.** Kumpulkan 30–50 pertanyaan nyata
   (termasuk typo, kode berantakan, OOD), jalankan di kedua mode, skor
   akurasi + latency. Hapus yang kalah — kode eksperimen yang abadi menjadi
   utang.
2. **Kalibrasi threshold per intent.** Ambang kini statis per kategori; pakai
   data antrean kurasi untuk menaikkan/menurunkan ambang per intent
   (mis. intent yang sering false-positive → ambang naik).
3. **Kamus hidup.** Auto-refresh terjadwal + hapus istilah basi + ranking
   saran memakai `usage_count`. Pertimbangkan bobot frekuensi pemakaian.
4. **OOD & slang dari data nyata.** Perluas pola OOD dan tabel slang dari
   pertanyaan kurasi (tanpa menyalin korpus ukur ke exemplar).
5. **Slot filling multi-turn penuh.** Simpan slot terkumpul antar-pesan
   (bukan hanya kategori), mis. "yang rusak?" → "laptop" → "di gudang mana?"
   dalam satu alur terpandu.
6. **Dashboard observabilitas.** Agregat dari `feedback` + log: distribusi
   intent, fallback rate, win-rate Alpha vs Beta, latensi Qwen, top
   pertanyaan gagal — untuk kurasi mingguan yang terarah.
7. **Cache jawaban.** Cache singkat (mis. 60 detik) untuk pertanyaan identik
   berulang (rekap/stok) agar hemat CPU dual-core.
8. **Keamanan.** Rate-limit per user, redaksi PII di log, audit trail untuk
   jawaban berisi data karyawan.
9. **Beta bila kalah telak.** Coba decide 1.7B hanya untuk intent (tetap
   rephrase 0.6B), atau fine-tune kecil di atas data kurasi — jalan panjang,
   lakukan hanya bila data evaluasi membenarkannya.
10. **Disiplin dokumen.** Perbarui `aimode.md` ini tiap perubahan perilaku —
    dokumen basi lebih berbahaya daripada tak ada dokumen.

---

## 10. Cara Verifikasi Cepat (tanpa Odoo)

```bash
python addons/it_asset/models/ask_ai_nlu.py       # target 113/113 + OOD 5/5
python addons/it_asset/models/ask_ai_commands.py  # target 89/89
python addons/it_asset/models/ask_ai_persona.py   # target 17/17
python addons/it_asset/tests/test_ask_ai_flows.py # target 15/15
python -m py_compile addons/it_asset/models/ask_ai.py
```

Aturan suci: korpus ukur ≠ korpus latih — contoh uji tidak boleh disalin ke
`EXEMPLARS` atau pola shorthand yang menghafalnya.
