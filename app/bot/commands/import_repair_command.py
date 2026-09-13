# # app/bot/commands/import_repair_command.py
# import uuid
# from pathlib import Path
# from datetime import datetime

# import openpyxl
# from openpyxl.utils import get_column_letter
# from app.bot.base_command import BaseCommand
# from app.core.database import SessionLocal
# from app.models.repairs import Repair
# from app.models import TestResult


# # Map tên cột hiển thị trong file Excel -> tên field trong model Repair.
# # Cột "Number" không map vì không dùng (chỉ là số thứ tự trong file, không lưu DB).
# HEADER_TO_FIELD = {
#     "Date receive": "date_receive",
#     "Board ID": "board_id",
#     "Test Tool": "test_tool_result",
#     "Failure Cause": "failure_cause",
#     "Disposition": "disposition",
#     "After Repair": "after_repair_result",
#     "PUSS F": "puss_f",
#     "Charging Station Test": "charging_station_test_status",
#     "Detailed Remarks": "detailed_remarks",
#     "Ticket ID": "ticket_id",
#     "SN": "sn",
#     "Date Ons": "date_onsite",
#     "Original Phenomenon": "original_phenomenon",
#     "Station Code": "station_code",
# }

# # Các cột bắt buộc phải có trong header row (tên hiển thị thực tế trong Excel)
# REQUIRED_COLUMNS = [
#     "Date receive", "Board ID", "Test Tool", "Failure Cause",
#     "Disposition", "After Repair", "Ticket ID", "SN",
#     "Date Ons", "Original Phenomenon", "Station Code",
#     "Charging Station Test", "Detailed Remarks",
# ]

# # Các cột chứa ảnh (ảnh được neo/nổi trên ô, không phải giá trị cell)
# # nên không nằm trong REQUIRED_COLUMNS. Nếu file không có cột nào trong
# # đây thì đơn giản là không lưu ảnh cho field tương ứng, không raise lỗi.
# IMAGE_COLUMNS = {
#     "Repair photo": "repair_photo_path",
#     "Failure Verification": "failure_verification_photo_path",
# }

# # Thư mục lưu ảnh trên local (nên là volume mounted nếu chạy Docker).
# # Mặc định dùng path tương đối trong project để không cần quyền root khi tạo.
# # Nếu muốn dùng path tuyệt đối (vd /data/repair_photos), phải đảm bảo user
# # chạy bot có quyền ghi vào đó trước (mkdir + chown), không thì sẽ bị
# # PermissionError khi tạo thư mục lần đầu.
# REPAIR_PHOTO_STORAGE_DIR = "storage/repair_photos"

# # Số dòng lỗi / cảnh báo tối đa hiển thị trong report, tránh spam tin nhắn Telegram
# MAX_ITEMS_IN_REPORT = 15

# # Commit theo batch thay vì 1 transaction duy nhất cho toàn bộ file - quan trọng
# # với file lớn (vd ~10.000 dòng): tránh giữ 1 transaction khổng lồ quá lâu, giảm
# # áp lực bộ nhớ của session, và Postgres/MySQL cũng xử lý tốt hơn nhiều insert
# # nhỏ theo lô thay vì 1 lô cực lớn.
# BATCH_COMMIT_SIZE = 500


# class ImportRepairCommand(BaseCommand):
#     def register(self):
#         @self.register_handler(commands=['import_repair'], admin=True)
#         def handle(message):
#             msg = self.bot.send_message(
#                 message.chat.id,
#                 "📤 Gửi file Excel (.xlsx) chứa dữ liệu Repair cần import.\n"
#                 "Dòng đầu tiên phải là tên cột (header), khớp với các trường trong hệ thống.\n"
#                 "Gõ /cancel để hủy."
#             )
#             self.bot.register_next_step_handler(msg, self.process_file)

#     def process_file(self, message):
#         if message.text and message.text.strip().lower() == "/cancel":
#             self.bot.reply_to(message, "❎ Đã hủy import.")
#             return

#         if not message.document:
#             self.bot.reply_to(message, "❌ Vui lòng gửi 1 file Excel (.xlsx), hoặc /cancel để hủy.")
#             return

#         file_name = message.document.file_name or ""
#         if not file_name.lower().endswith((".xlsx", ".xlsm")):
#             self.bot.reply_to(message, "❌ Chỉ hỗ trợ file .xlsx / .xlsm")
#             return

#         try:
#             file_info = self.bot.get_file(message.document.file_id)
#             downloaded = self.bot.download_file(file_info.file_path)
#         except Exception as e:
#             self.bot.reply_to(message, f"❌ Lỗi tải file từ Telegram: {e}")
#             return

#         tmp_path = f"/tmp/import_repair_{message.chat.id}.xlsx"
#         try:
#             with open(tmp_path, "wb") as f:
#                 f.write(downloaded)
#         except Exception as e:
#             self.bot.reply_to(message, f"❌ Lỗi lưu file tạm trên server: {e}")
#             return

#         try:
#             # data_only=True chỉ ảnh hưởng tới cache giá trị công thức, không ảnh
#             # hưởng tới ảnh nhúng - ws._images vẫn đọc được bình thường.
#             wb = openpyxl.load_workbook(tmp_path, data_only=True)
#             ws = wb.active
#         except Exception as e:
#             self.bot.reply_to(message, f"❌ Không đọc được file Excel: {e}")
#             return

#         headers = [str(c.value).strip() if c.value else "" for c in ws[1]]
#         missing = [col for col in REQUIRED_COLUMNS if col not in headers]
#         if missing:
#             self.bot.reply_to(
#                 message,
#                 "❌ File thiếu các cột bắt buộc:\n" + "\n".join(f"- {c}" for c in missing)
#             )
#             return

#         col_idx = {name: headers.index(name) for name in headers if name}

#         # Vị trí cột (1-indexed, khớp với anchor.col của ảnh) cho từng field ảnh
#         image_col_positions = {
#             model_field: headers.index(excel_col) + 1
#             for excel_col, model_field in IMAGE_COLUMNS.items()
#             if excel_col in headers
#         }
#         missing_image_columns = [c for c in IMAGE_COLUMNS if c not in headers]

#         # warnings: các vấn đề không làm fail cả dòng/cả import, nhưng user cần biết
#         # failed: các dòng bị bỏ qua hoàn toàn do lỗi
#         warnings = []
#         failed = []

#         if missing_image_columns:
#             warnings.append(
#                 "Không tìm thấy cột ảnh trong file: " + ", ".join(missing_image_columns) +
#                 " -> các field ảnh tương ứng sẽ không được lưu."
#             )

#         # Build map ảnh + thu thập cảnh báo trong lúc build (ảnh lỗi, không xác định
#         # được vị trí, thiếu thư viện đọc ảnh, ...)
#         image_map, image_build_warnings = self._build_image_map(ws)
#         warnings.extend(image_build_warnings)

#         raw_image_count = len(getattr(ws, "_images", []))
#         if image_col_positions and raw_image_count == 0:
#             warnings.append(
#                 "File có cấu hình cột ảnh nhưng không đọc được ảnh nhúng nào "
#                 "(ws._images rỗng). Nguyên nhân thường gặp: thiếu thư viện Pillow "
#                 "trên server (`pip install Pillow`), hoặc ảnh trong file không phải "
#                 "dạng nhúng chuẩn OOXML."
#             )

#         created = 0
#         updated = 0
#         rows_since_commit = 0
#         with SessionLocal() as db:
#             # --- Prefetch 1 lần toàn bộ ticket_id trong file có thể trùng với DB ---
#             # Tránh N+1 query (10.000 dòng = 10.000 SELECT riêng lẻ nếu query
#             # trong vòng lặp). Duyệt sheet 1 lần trước (chỉ đọc cột ticket_id,
#             # rất nhẹ) để gom danh sách, sau đó query 1 lần bằng IN (...).
#             ticket_col_idx = col_idx.get("Ticket ID")
#             existing_by_ticket = {}
#             if ticket_col_idx is not None:
#                 wanted_ticket_ids = set()
#                 for row in ws.iter_rows(min_row=2, values_only=True):
#                     if all(v is None for v in row):
#                         continue
#                     norm = self._normalize_ticket_id(row[ticket_col_idx])
#                     if norm:
#                         wanted_ticket_ids.add(norm)

#                 if wanted_ticket_ids:
#                     # Với vài chục nghìn ticket_id, nên chia nhỏ IN(...) để tránh
#                     # query quá dài - 1000 phần tử/lần là mức an toàn phổ biến.
#                     wanted_list = list(wanted_ticket_ids)
#                     for i in range(0, len(wanted_list), 1000):
#                         chunk = wanted_list[i:i + 1000]
#                         for record in db.query(Repair).filter(Repair.ticket_id.in_(chunk)).all():
#                             existing_by_ticket[record.ticket_id] = record

#             for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
#                 if all(v is None for v in row):
#                     continue  # bỏ qua dòng trống

#                 # row_dict key theo tên field model (vd "board_id"), không phải
#                 # tên cột hiển thị trong Excel (vd "Board ID")
#                 row_dict = {
#                     field_name: row[col_idx[excel_col]]
#                     for excel_col, field_name in HEADER_TO_FIELD.items()
#                     if excel_col in col_idx
#                 }
#                 try:
#                     board_id = row_dict.get("board_id")
#                     if not board_id:
#                         raise ValueError("board_id trống")
#                     board_id_str = str(board_id).strip()

#                     # --- Ảnh: lỗi lưu ảnh chỉ tạo warning, KHÔNG làm fail cả dòng ---
#                     photo_paths, photo_warnings = self._extract_row_photos(
#                         row_num=row_num,
#                         board_id=board_id_str,
#                         image_col_positions=image_col_positions,
#                         image_map=image_map,
#                     )
#                     warnings.extend(photo_warnings)

#                     # --- Ngày tháng: parse lỗi -> warning, field để trống ---
#                     date_receive, w = self._parse_date(row_dict.get("date_receive"))
#                     if w:
#                         warnings.append(f"Dòng {row_num}: date_receive - {w}")

#                     date_onsite, w = self._parse_date(row_dict.get("date_onsite"))
#                     if w:
#                         warnings.append(f"Dòng {row_num}: date_onsite - {w}")

#                     # --- Enum: giá trị không khớp -> warning, field để trống ---
#                     test_tool_result, w = self._parse_enum(row_dict.get("test_tool_result"))
#                     if w:
#                         warnings.append(f"Dòng {row_num}: test_tool_result - {w}")

#                     after_repair_result, w = self._parse_enum(row_dict.get("after_repair_result"))
#                     if w:
#                         warnings.append(f"Dòng {row_num}: after_repair_result - {w}")

#                     # Các field text/scalar dùng chung cho cả create và update
#                     field_values = dict(
#                         date_receive=date_receive,
#                         board_id=board_id_str,
#                         test_tool_result=test_tool_result,
#                         failure_cause=row_dict.get("failure_cause"),
#                         disposition=row_dict.get("disposition"),
#                         after_repair_result=after_repair_result,
#                         ticket_id=row_dict.get("ticket_id"),
#                         sn=row_dict.get("sn"),
#                         date_onsite=date_onsite,
#                         original_phenomenon=row_dict.get("original_phenomenon"),
#                         station_code=row_dict.get("station_code"),
#                         charging_station_test_status=row_dict.get("charging_station_test_status"),
#                         detailed_remarks=row_dict.get("detailed_remarks"),
#                     )

#                     # --- Upsert theo ticket_id (dùng dict đã prefetch, KHÔNG query lại) ---
#                     ticket_id_str = self._normalize_ticket_id(row_dict.get("ticket_id"))
#                     existing = existing_by_ticket.get(ticket_id_str) if ticket_id_str else None

#                     if existing:
#                         for field_name, value in field_values.items():
#                             setattr(existing, field_name, value)
#                         # Ảnh: ghi đè toàn bộ theo dữ liệu mới. Nếu bản ghi cũ đã có
#                         # ảnh và giờ bị thay bằng ảnh khác (hoặc không còn ảnh), xóa
#                         # luôn file ảnh cũ trên disk để tránh rác trong storage.
#                         for image_field in ("repair_photo_path", "failure_verification_photo_path"):
#                             old_path = getattr(existing, image_field, None)
#                             new_path = photo_paths.get(image_field)
#                             if old_path and old_path != new_path:
#                                 del_warning = self._delete_photo_file(old_path)
#                                 if del_warning:
#                                     warnings.append(f"Dòng {row_num}: {del_warning}")
#                             setattr(existing, image_field, new_path)
#                         updated += 1
#                     else:
#                         repair = Repair(
#                             **field_values,
#                             repair_photo_path=photo_paths.get("repair_photo_path"),
#                             failure_verification_photo_path=photo_paths.get("failure_verification_photo_path"),
#                         )
#                         db.add(repair)
#                         # Bản ghi mới tạo cũng cần được nhớ trong map, phòng trường
#                         # hợp file có 2 dòng trùng ticket_id với nhau (dòng sau sẽ
#                         # update lên chính dòng vừa tạo, thay vì tạo thêm bản ghi mới).
#                         if ticket_id_str:
#                             existing_by_ticket[ticket_id_str] = repair
#                         created += 1
#                 except Exception as e:
#                     failed.append(f"Dòng {row_num}: {e}")
#                     continue

#                 rows_since_commit += 1
#                 if rows_since_commit >= BATCH_COMMIT_SIZE:
#                     try:
#                         db.commit()
#                     except Exception as e:
#                         db.rollback()
#                         self.bot.reply_to(
#                             message,
#                             f"❌ Import dừng giữa chừng: lỗi khi ghi batch vào database "
#                             f"(đã xử lý xong tới dòng {row_num}, phần này bị rollback).\n"
#                             f"Chi tiết: {e}"
#                         )
#                         return
#                     rows_since_commit = 0

#             try:
#                 db.commit()
#             except Exception as e:
#                 db.rollback()
#                 self.bot.reply_to(
#                     message,
#                     f"❌ Import thất bại: lỗi khi ghi vào database, đã rollback toàn bộ.\nChi tiết: {e}"
#                 )
#                 return

#         self.bot.reply_to(message, self._build_report(created, updated, failed, warnings))

#     @staticmethod
#     def _delete_photo_file(path: str):
#         """
#         Xóa file ảnh cũ trên disk khi bị thay bằng ảnh mới (hoặc bị bỏ trống).
#         Không raise ra ngoài - lỗi xóa file (file đã bị xóa trước đó, không có
#         quyền, ...) chỉ nên là warning, không được làm fail cả việc update record.
#         Trả về warning message nếu xóa lỗi, None nếu ok.
#         """
#         try:
#             file_path = Path(path)
#             if file_path.exists():
#                 file_path.unlink()
#             return None
#         except Exception as e:
#             return f"không xóa được ảnh cũ '{path}': {e}"

#     @staticmethod
#     def _normalize_ticket_id(value):
#         """Chuẩn hóa ticket_id để dùng làm khóa upsert. Trả về None nếu rỗng
#         hoặc là placeholder không có ý nghĩa định danh (vd 'n/a', '-')."""
#         if value is None:
#             return None
#         text = str(value).strip()
#         if not text or text.lower() in ("n/a", "na", "-", "none", "null"):
#             return None
#         return text

#     # ------------------------------------------------------------------
#     # Báo cáo kết quả
#     # ------------------------------------------------------------------

#     @staticmethod
#     def _build_report(created, updated, failed, warnings):
#         lines = [f"✅ Import xong: {created} bản ghi mới, {updated} bản ghi được cập nhật (trùng ticket_id)."]

#         if failed:
#             lines.append(f"\n❌ {len(failed)} dòng lỗi (bị bỏ qua hoàn toàn):")
#             lines.extend(failed[:MAX_ITEMS_IN_REPORT])
#             if len(failed) > MAX_ITEMS_IN_REPORT:
#                 lines.append(f"... và {len(failed) - MAX_ITEMS_IN_REPORT} dòng lỗi khác (không hiển thị hết).")

#         if warnings:
#             lines.append(f"\n⚠️ {len(warnings)} cảnh báo (dữ liệu vẫn được lưu, nhưng thiếu 1 phần):")
#             lines.extend(warnings[:MAX_ITEMS_IN_REPORT])
#             if len(warnings) > MAX_ITEMS_IN_REPORT:
#                 lines.append(f"... và {len(warnings) - MAX_ITEMS_IN_REPORT} cảnh báo khác (không hiển thị hết).")

#         return "\n".join(lines)

#     # ------------------------------------------------------------------
#     # Xử lý ảnh nhúng
#     # ------------------------------------------------------------------

#     @classmethod
#     def _build_image_map(cls, ws):
#         """
#         Build map (row, col) 1-indexed -> (bytes ảnh, format) dựa trên vị trí
#         từng ảnh trong sheet. Trả về (image_map, warnings) - warnings ghi lại
#         những ảnh không xử lý được (không xác định vị trí, đọc data lỗi...)
#         thay vì bỏ qua âm thầm như trước.

#         Có 2 kiểu anchor cần xử lý:
#         - OneCellAnchor / TwoCellAnchor: ảnh gắn theo ô, có anchor._from.row/col
#           -> đây là trường hợp chuẩn khi tạo/sửa file bằng Excel.
#         - AbsoluteAnchor: ảnh định vị tuyệt đối theo tọa độ (EMU), KHÔNG gắn ô
#           -> thường gặp khi file được export ra .xlsx từ Google Sheets. Trường
#           hợp này phải ước lượng dòng/cột dựa vào tọa độ pixel/EMU so với chiều
#           cao/rộng thực tế của từng dòng/cột trong sheet.
#         """
#         image_map = {}
#         warnings = []

#         for i, img in enumerate(getattr(ws, "_images", [])):
#             anchor = getattr(img, "anchor", None)
#             if anchor is None:
#                 warnings.append(f"Ảnh #{i} trong file không xác định được vị trí neo -> bị bỏ qua.")
#                 continue

#             if hasattr(anchor, "_from"):
#                 row = anchor._from.row + 1
#                 col = anchor._from.col + 1
#             else:
#                 pos = getattr(anchor, "pos", None)
#                 if pos is None:
#                     warnings.append(
#                         f"Ảnh #{i} có anchor kiểu {type(anchor).__name__} không hỗ trợ "
#                         f"xác định vị trí -> bị bỏ qua."
#                     )
#                     continue
#                 row = cls._estimate_row_from_emu(ws, pos.y)
#                 col = cls._estimate_col_from_emu(ws, pos.x)
#                 warnings.append(
#                     f"Ảnh #{i} không gắn theo ô (anchor tuyệt đối) -> vị trí dòng/cột "
#                     f"(dòng {row}, cột {col}) chỉ là ước lượng, có thể không chính xác."
#                 )

#             try:
#                 data = img._data()
#             except Exception as e:
#                 warnings.append(f"Ảnh #{i} (dòng ~{row}, cột ~{col}) lỗi khi đọc dữ liệu: {e} -> bị bỏ qua.")
#                 continue

#             # Nếu nhiều ảnh neo cùng 1 ô (hiếm gặp) thì giữ ảnh đầu tiên
#             if (row, col) in image_map:
#                 warnings.append(f"Có nhiều hơn 1 ảnh ở vị trí (dòng {row}, cột {col}) -> chỉ giữ ảnh đầu tiên.")
#             else:
#                 image_map[(row, col)] = (data, getattr(img, "format", None))

#         return image_map, warnings

#     # EMU (English Metric Units): đơn vị tọa độ nội bộ trong xlsx.
#     # 1 point = 12700 EMU, 1 pixel = 9525 EMU.
#     _EMU_PER_POINT = 12700
#     _DEFAULT_ROW_HEIGHT_POINTS = 15  # chiều cao dòng mặc định của Excel
#     _DEFAULT_COL_WIDTH_EMU = 640080  # ~ độ rộng cột mặc định (Excel "width" unit quy đổi)

#     @classmethod
#     def _estimate_row_from_emu(cls, ws, y_emu):
#         """Ước lượng số dòng (1-indexed) từ tọa độ Y (EMU) của ảnh, dựa trên
#         chiều cao thực tế từng dòng (nếu có set) hoặc chiều cao mặc định."""
#         cumulative = 0
#         row = 1
#         max_row = ws.max_row or 1
#         while row <= max_row:
#             height_pt = ws.row_dimensions[row].height or cls._DEFAULT_ROW_HEIGHT_POINTS
#             row_height_emu = height_pt * cls._EMU_PER_POINT
#             if cumulative + row_height_emu > y_emu:
#                 return row
#             cumulative += row_height_emu
#             row += 1
#         return max_row

#     @classmethod
#     def _estimate_col_from_emu(cls, ws, x_emu):
#         """Ước lượng số cột (1-indexed) từ tọa độ X (EMU) của ảnh."""
#         cumulative = 0
#         col = 1
#         max_col = ws.max_column or 1
#         while col <= max_col:
#             letter = get_column_letter(col)
#             width_units = ws.column_dimensions[letter].width
#             col_width_emu = (width_units * 7 + 5) * 9525 if width_units else cls._DEFAULT_COL_WIDTH_EMU
#             if cumulative + col_width_emu > x_emu:
#                 return col
#             cumulative += col_width_emu
#             col += 1
#         return max_col

#     @classmethod
#     def _extract_row_photos(cls, row_num, board_id, image_col_positions, image_map):
#         """
#         Với 1 dòng dữ liệu, tìm ảnh tương ứng ở các cột ảnh đã xác định, lưu ra
#         local. Trả về (photo_paths, warnings) - lỗi lưu ảnh không raise ra ngoài,
#         chỉ ghi warning, để không làm fail cả dòng dữ liệu chỉ vì ảnh lỗi.
#         """
#         photo_paths = {}
#         warnings = []
#         for model_field, col_pos in image_col_positions.items():
#             entry = image_map.get((row_num, col_pos))
#             if not entry:
#                 continue
#             data, img_format = entry
#             try:
#                 saved_path = cls._save_image(data, img_format, board_id)
#                 photo_paths[model_field] = saved_path
#             except Exception as e:
#                 warnings.append(
#                     f"Dòng {row_num}: không lưu được ảnh cho field '{model_field}' - {e}"
#                 )
#         return photo_paths, warnings

#     @staticmethod
#     def _save_image(data: bytes, img_format: str, board_id: str) -> str:
#         """Lưu bytes ảnh ra local, trả về path để lưu vào DB. Raise nếu ghi file lỗi
#         (hết dung lượng, không có quyền ghi thư mục, v.v.) để caller quyết định xử lý."""
#         ext = (img_format or "png").lower()
#         if ext == "jpeg":
#             ext = "jpg"

#         safe_board_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in board_id)
#         subdir = datetime.now().strftime("%Y%m")
#         dir_path = Path(REPAIR_PHOTO_STORAGE_DIR) / subdir

#         try:
#             dir_path.mkdir(parents=True, exist_ok=True)
#         except PermissionError as e:
#             raise PermissionError(
#                 f"Không có quyền tạo/ghi thư mục '{dir_path}'. "
#                 f"Kiểm tra quyền của user chạy bot trên thư mục '{REPAIR_PHOTO_STORAGE_DIR}' "
#                 f"(vd: chown/chmod), hoặc đổi REPAIR_PHOTO_STORAGE_DIR sang path khác có quyền ghi. "
#                 f"Chi tiết gốc: {e}"
#             ) from e

#         filename = f"{safe_board_id}_{uuid.uuid4().hex[:8]}.{ext}"
#         file_path = dir_path / filename

#         with open(file_path, "wb") as f:
#             f.write(data)

#         return str(file_path)

#     # ------------------------------------------------------------------
#     # Parse helpers - trả về (value, warning_message | None) thay vì âm thầm
#     # trả None khi parse lỗi, để caller báo lại cho user biết field nào bị bỏ trống.
#     # ------------------------------------------------------------------

#     # Thứ tự ưu tiên thử parse - đặt format khớp với data thực tế (Google Sheets
#     # export thường ra M/D/YYYY hoặc M/D/YY) lên trước để tránh parse nhầm ngày/tháng
#     _DATE_FORMATS = (
#         "%m/%d/%Y",   # 8/17/2026
#         "%m/%d/%y",   # 7/29/26
#         "%Y-%m-%d",   # 2026-08-17
#         "%d/%m/%Y",   # phòng trường hợp file khác định dạng dd/mm
#         "%d-%m-%Y",
#         "%Y/%m/%d",
#     )

#     @classmethod
#     def _parse_date(cls, value):
#         if value is None or value == "":
#             return None, None
#         if isinstance(value, datetime):
#             return value.date(), None
#         # openpyxl có thể trả về datetime.date thuần (không phải datetime.datetime)
#         # khi cell được format sẵn là Date trong Excel
#         if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
#             return value, None

#         text = str(value).strip()
#         for fmt in cls._DATE_FORMATS:
#             try:
#                 return datetime.strptime(text, fmt).date(), None
#             except ValueError:
#                 continue
#         return None, f"giá trị '{text}' không khớp format ngày nào được hỗ trợ -> để trống"

#     @staticmethod
#     def _parse_enum(value):
#         if not value:
#             return None, None
#         text = str(value).strip()
#         try:
#             return TestResult(text.upper()), None
#         except ValueError:
#             return None, f"giá trị '{text}' không khớp enum TestResult -> để trống"