#!/bin/bash
# Ollama를 GPU 0 전용으로 설정하는 스크립트
# 실행: sudo bash setup_ollama_gpu0.sh

set -e

echo "[1/3] Ollama systemd override 설정..."
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
Environment="CUDA_VISIBLE_DEVICES=0"
EOF

echo "[2/3] systemd 데몬 리로드 + Ollama 재시작..."
systemctl daemon-reload
systemctl restart ollama

echo "[3/3] 확인 중 (5초 대기)..."
sleep 5
echo "Ollama 상태: $(systemctl is-active ollama)"
nvidia-smi --query-compute-apps=pid,gpu_bus_id,used_memory,process_name --format=csv,noheader 2>/dev/null || true

echo ""
echo "완료! Ollama가 GPU 0에서만 실행됩니다."
echo "테스트: curl -s http://localhost:11434/api/tags | python3 -m json.tool"
