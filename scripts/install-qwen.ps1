# Install + jalankan Qwen3-0.6B-Q4_K_M lokal (CPU-only) untuk Ask AI rephrase.
# Sumber default: https://huggingface.co/unsloth/Qwen3-0.6B-GGUF (~397 MB)
# Jalankan: powershell -ExecutionPolicy Bypass -File scripts\install-qwen.ps1
#
# Alternatif khusus Bahasa Indonesia (SEA-tuned, 0.5B, ~407 MB):
#   $env:HF_REPO = "sail/Sailor-0.5B-Chat-gguf"; $env:HF_FILE = "ggml-model-Q4_K_M.gguf"
#   powershell -ExecutionPolicy Bypass -File scripts\install-qwen.ps1

$ErrorActionPreference = "Stop"
$Repo = if ($env:HF_REPO) { $env:HF_REPO } else { "unsloth/Qwen3-0.6B-GGUF" }
$File = if ($env:HF_FILE) { $env:HF_FILE } else { "Qwen3-0.6B-Q4_K_M.gguf" }
$Port = "8081"
$ModelDir = if ($env:MODEL_DIR) { $env:MODEL_DIR } else { "$env:USERPROFILE\.cache\ask-ai-llm" }
$ModelPath = Join-Path $ModelDir $File

New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null

if (-not (Test-Path -LiteralPath $ModelPath)) {
    Write-Host "[1/3] Download $File dari Hugging Face ($Repo)..."
    $Url = "https://huggingface.co/$Repo/resolve/main/$File"
    # Coba huggingface-cli dulu, fallback ke Invoke-WebRequest
    $hf = Get-Command huggingface-cli -ErrorAction SilentlyContinue
    if ($hf) {
        & huggingface-cli download $Repo $File --local-dir $ModelDir
    } else {
        Invoke-WebRequest -Uri $Url -OutFile $ModelPath -UseBasicParsing
    }
} else {
    Write-Host "[1/3] Model sudah ada: $ModelPath"
}

$llama = Get-Command llama-server -ErrorAction SilentlyContinue
if (-not $llama) {
    Write-Host "[2/3] llama-server belum ada."
    Write-Host "      Opsi A (disarankan): docker compose -f docker-compose.yml -f docker-compose.llm.yml up -d llm"
    Write-Host "      Opsi B: download binary dari https://github.com/ggml-org/llama.cpp/releases lalu ulangi script ini."
    exit 1
}

Write-Host "[3/3] Jalankan llama-server (CPU-only, single-slot, 2 thread) di port $Port ..."
& llama-server -m $ModelPath --port $Port --ctx-size 1024 --threads 2 --parallel 1 --reasoning off
