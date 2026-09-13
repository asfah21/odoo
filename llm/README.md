# Qwen 0.6B untuk Ask AI (rephrase-only, CPU-only)
#
# Model default: unsloth/Qwen3-0.6B-GGUF, file Qwen3-0.6B-Q4_K_M.gguf (~397 MB)
# Fungsi di Odoo: HANYA memoles bahasa jawaban (rephrase_enabled),
# BUKAN decide intent, BUKAN baca database. Tanpa Qwen pun Ask AI jalan
# karena kamus lokal + rule NLU yang paham typo.
#
# Cara pakai:
#   1. Install & jalankan sekali (Windows PowerShell):
#        powershell -ExecutionPolicy Bypass -File scripts\install-qwen.ps1
#      atau Linux:
#        bash scripts/install-qwen.sh
#      atau Docker sidecar (server yang sama dengan Odoo):
#        docker compose -f docker-compose.yml -f docker-compose.llm.yml up -d llm
#   2. Cek: http://127.0.0.1:8081/health  -> {"status":"ok"}
#   3. Di Odoo: IT -> Ask AI -> Setting
#      - centang "Aktifkan Rephrase Qwen"
#      - klik "Test Qwen" (harus OK)
#      - klik "Generate Kamus" (wajib, agar typo tetap paham tanpa Qwen)
#
# Spesifikasi runtime hemat (dual-core, CPU-only, single-slot):
#   llama-server -m Qwen3-0.6B-Q4_K_M.gguf --port 8081 \
#     --ctx-size 1024 --threads 2 --parallel 1 --reasoning off
#
# Alternatif khusus Bahasa Indonesia (opsional, bisa dicoba):
#   Sailor 0.5B Chat (SEA-tuned, fokus Indonesia/Melayu/Thai/Viet):
#     repo: sail/Sailor-0.5B-Chat-gguf, file: ggml-model-Q4_K_M.gguf (~407 MB)
#   Pakai via environment (tanpa ubah file):
#     Windows: $env:HF_REPO="sail/Sailor-0.5B-Chat-gguf"; $env:HF_FILE="ggml-model-Q4_K_M.gguf"
#     Linux:   HF_REPO=sail/Sailor-0.5B-Chat-gguf HF_FILE=ggml-model-Q4_K_M.gguf bash scripts/install-qwen.sh
#     Docker:  HF_REPO=sail/Sailor-0.5B-Chat-gguf HF_FILE=ggml-model-Q4_K_M.gguf docker compose -f docker-compose.yml -f docker-compose.llm.yml up -d llm
#   Catatan jujur: Sailor 0.5B berbasis Qwen1.5 (lebih tua dari Qwen3) dan
#   instruction-following-nya lebih lemah. Untuk tugas rephrase terkekang
#   (poles kalimat, fakta dikunci guardrail) Qwen3-0.6B umumnya lebih patuh.
#   Coba Sailor hanya bila hasil Bahasa Indonesianya terasa lebih natural di
#   data lapanganmu — bandingkan via tombol Test Qwen + chat langsung.
#
# Model yang TIDAK disarankan untuk spek ini:
#   SahabatAI 8B/9B (GoTo, khusus Indonesia tapi Q4 ~4.5-5 GB + butuh
#   8+ core agar tidak timeout) dan Nusantara-0.8B (tanpa GGUF resmi).
