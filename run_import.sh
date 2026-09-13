#!/usr/bin/env bash
# run_import.sh
#
# Chạy import Repair từ file Excel (nhiều sheet, mỗi sheet = 1 board) vào
# database.
#
# Cho chọn 1 trong 2 chế độ:
#   1) Thêm bản ghi mới  - chỉ insert, giữ nguyên data cũ trong bảng
#   2) Xóa hết, thêm mới hoàn toàn - XÓA TOÀN BỘ Repair + ảnh cũ, rồi import
#      lại từ đầu (KHÔNG THỂ HOÀN TÁC, cần gõ xác nhận riêng). Bảng Board
#      KHÔNG bị xóa ở cả 2 mode - board mới sẽ tự được tạo nếu code trong
#      sheet chưa từng tồn tại.
#
# Usage:
#   ./run_import.sh /path/to/file.xlsx
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ "$#" -ne 1 ]; then
    echo "❌ Thiếu tham số. Cách dùng: ./run_import.sh /path/to/file.xlsx"
    exit 1
fi

XLSX_FILE="$1"

if [ ! -f "$XLSX_FILE" ]; then
    echo "❌ Không tìm thấy file: $XLSX_FILE"
    exit 1
fi

echo "Chọn chế độ import cho file: $XLSX_FILE"
echo "  1) Thêm bản ghi mới       - chỉ insert, KHÔNG đụng tới data cũ"
echo "  2) Xóa hết, thêm mới hoàn toàn - XÓA SẠCH data + ảnh cũ, rồi import lại từ đầu"
echo ""
read -r -p "Nhập lựa chọn (1 hoặc 2): " CHOICE

case "$CHOICE" in
    1)
        MODE_ARGS=(--mode insert)
        ;;
    2)
        echo ""
        echo "======================================================================"
        echo "⚠️  CẢNH BÁO"
        echo ""
        echo "Thao tác này sẽ:"
        echo "  1. XÓA TOÀN BỘ bản ghi hiện có trong bảng 'repairs'"
        echo "  2. XÓA TOÀN BỘ file ảnh trong '$SCRIPT_DIR/storage/repair_photos'"
        echo "  3. Import lại từ đầu theo file: $XLSX_FILE"
        echo ""
        echo "Bảng 'boards' (danh mục board) KHÔNG bị xóa - board mới sẽ tự"
        echo "được tạo cho mỗi sheet nếu code đó chưa từng tồn tại."
        echo ""
        echo "Hành động này KHÔNG THỂ HOÀN TÁC."
        echo "======================================================================"
        read -r -p "Gõ chính xác XOA (viết hoa, không dấu) để xác nhận, hoặc Enter để hủy: " CONFIRM

        if [ "$CONFIRM" != "XOA" ]; then
            echo "❎ Đã hủy. Không có gì bị xóa hay import."
            exit 0
        fi
        MODE_ARGS=(--mode reset --yes)
        ;;
    *)
        echo "❌ Lựa chọn không hợp lệ, phải là 1 hoặc 2."
        exit 1
        ;;
esac

echo ""
echo "Bắt đầu xử lý ..."
echo ""

# Activate virtualenv "env" nếu có (khớp với (env) đang thấy trong terminal bạn dùng)
if [ -f "$SCRIPT_DIR/env/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/env/bin/activate"
fi

# Đảm bảo Python tìm được package "app" dù chạy từ đâu
export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"

python3 scripts/import_repair.py "$XLSX_FILE" "${MODE_ARGS[@]}"