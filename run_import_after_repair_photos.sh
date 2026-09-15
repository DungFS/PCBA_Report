#!/usr/bin/env bash
# run_import_after_repair_photos.sh
#
# Chạy import file Excel (2 cột: board_id, img) -> tạo Repair mới với ảnh
# lưu vào after_repair_photo_1, mặc định test_tool_result=FAIL,
# after_repair_result=PASS.
#
# Script này CHỈ INSERT (không xóa gì cả) - không cần xác nhận trước khi
# chạy, khác với run_import.sh (import_repair.py) vốn có mode xóa sạch data.
#
# Usage:
#   ./run_import_after_repair_photos.sh /path/to/file.xlsx
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ "$#" -ne 1 ]; then
    echo "❌ Thiếu tham số. Cách dùng: ./run_import_after_repair_photos.sh /path/to/file.xlsx"
    exit 1
fi

XLSX_FILE="$1"

if [ ! -f "$XLSX_FILE" ]; then
    echo "❌ Không tìm thấy file: $XLSX_FILE"
    exit 1
fi

echo "Bắt đầu import file: $XLSX_FILE"
echo ""

# Activate virtualenv "env" nếu có (khớp với (env) đang thấy trong terminal bạn dùng)
if [ -f "$SCRIPT_DIR/env/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/env/bin/activate"
fi

# Đảm bảo Python tìm được package "app" dù chạy từ đâu
export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"

python3 scripts/import_after_repair_photos.py "$XLSX_FILE"