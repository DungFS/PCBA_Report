#!/usr/bin/env bash
# run_build_embeddings.sh
#
# Backfill embedding (RAG) cho toàn bộ repair đã có sẵn trong DB - chạy 1
# lần khi mới bật tính năng RAG cho /ask, hoặc sau khi đổi model embedding.
# Xem chi tiết trong scripts/build_repair_embeddings.py.
#
# Usage:
#   ./run_build_embeddings.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtualenv "env" nếu có (khớp với run_import.sh)
if [ -f "$SCRIPT_DIR/env/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/env/bin/activate"
fi

# Đảm bảo Python tìm được package "app" dù chạy từ đâu
export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"

PYTHON_CMD="python"

if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    echo "❌ Không tìm thấy Python."
    exit 1
fi

echo "Python đang sử dụng:"
"$PYTHON_CMD" --version
echo ""

"$PYTHON_CMD" scripts/build_repair_embeddings.py
