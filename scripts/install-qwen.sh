#!/usr/bin/env bash
# Install + jalankan Qwen3-0.6B-Q4_K_M lokal (CPU-only) untuk Ask AI rephrase.
# Sumber default: https://huggingface.co/unsloth/Qwen3-0.6B-GGUF (~397 MB)
#
# Alternatif khusus Bahasa Indonesia (SEA-tuned, 0.5B, ~407 MB):
#   HF_REPO=sail/Sailor-0.5B-Chat-gguf HF_FILE=ggml-model-Q4_K_M.gguf bash scripts/install-qwen.sh
set -euo pipefail

REPO="${HF_REPO:-unsloth/Qwen3-0.6B-GGUF}"
FILE="${HF_FILE:-Qwen3-0.6B-Q4_K_M.gguf}"
PORT="8081"
MODEL_DIR="${MODEL_DIR:-$HOME/.cache/ask-ai-llm}"
MODEL_PATH="$MODEL_DIR/$FILE"

mkdir -p "$MODEL_DIR"

if [ ! -f "$MODEL_PATH" ]; then
  echo "[1/3] Download $FILE dari Hugging Face ($REPO)..."
  URL="https://huggingface.co/$REPO/resolve/main/$FILE"
  if command -v hf >/dev/null 2>&1; then
    hf download "$REPO" "$FILE" --local-dir "$MODEL_DIR"
  elif command -v huggingface-cli >/dev/null 2>&1; then
    huggingface-cli download "$REPO" "$FILE" --local-dir "$MODEL_DIR"
  else
    curl -L --progress-bar -o "$MODEL_PATH" "$URL"
  fi
else
  echo "[1/3] Model sudah ada: $MODEL_PATH"
fi

if ! command -v llama-server >/dev/null 2>&1; then
  echo "[2/3] llama-server belum ada. Install via: https://github.com/ggml-org/llama.cpp/releases"
  echo "      atau pakai docker: docker compose -f docker-compose.yml -f docker-compose.llm.yml up -d llm"
  exit 1
fi

echo "[3/3] Jalankan llama-server (CPU-only, single-slot, 2 thread) di port $PORT ..."
exec llama-server -m "$MODEL_PATH" --port "$PORT" --ctx-size 1024 --threads 2 --parallel 1 --reasoning off
