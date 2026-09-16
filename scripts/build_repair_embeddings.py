#!/usr/bin/env python3
"""
Backfill embedding (RAG) cho TOÀN BỘ repair đã có sẵn trong DB - chạy 1 lần
khi mới bật tính năng RAG cho /ask, hoặc sau khi đổi model embedding (vector
của model cũ và mới không tương thích, phải tạo lại toàn bộ).

Chạy qua terminal (KHÔNG phải command Telegram - có thể mất nhiều thời gian
nếu DB có nhiều repair, mỗi repair cần chạy qua model embedding local 1
lần), giống cách import_repair.py cũng chạy qua terminal (./run_import.sh):

    ./run_build_embeddings.sh

hoặc trực tiếp:

    PYTHONPATH=. python scripts/build_repair_embeddings.py

Yêu cầu: đã cài `sentence-transformers` (xem requirements.txt) và có internet
ở LẦN CHẠY ĐẦU TIÊN để tải model embedding từ Hugging Face Hub (xem
app/services/embedding_service.py) - các lần sau chạy offline được.

Sau lần backfill đầu tiên này, các repair mới/sửa sau đó được tự động
re-index qua hook trong app/bot/commands/repair_command.py - không cần chạy
lại script này trừ khi đổi model embedding.
"""
from app.services.rag_service import reindex_all


def main():
    def progress(done, total):
        print(f"[{done}/{total}] đã index...", end="\r", flush=True)

    total = reindex_all(progress_callback=progress)
    print(f"\n✅ Hoàn tất. Đã index {total} repair.")


if __name__ == "__main__":
    main()
