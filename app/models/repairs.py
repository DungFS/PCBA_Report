import string
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, Date, DateTime,Enum,ForeignKey, func
from app.core.database import Base
from app.models import TestResult
from sqlalchemy.orm import relationship
from app.models.mixins import AuditMixin


class Repair(Base, AuditMixin):
    __tablename__ = "repairs"
    __table_args__ = {"comment": "Lịch sử tiếp nhận và sửa chữa board PCBA"}

    id = Column(Integer, primary_key=True, autoincrement=True)

    # --- Tiếp nhận ---
    date_receive = Column(
        Date, nullable=True,
        comment="Ngày board được gửi về bộ phận sửa chữa"
    )
    board_id = Column(
        String(100), nullable=False, index=True,
        comment="Mã loại board / model, vd OCPP_Three_V2.6_22KW"
    )
    board_code = Column(
        String(100), ForeignKey("boards.code"), nullable=True, index=True,
        comment="Mã board, tham chiếu tới boards.code trong danh mục board"
    )
    contractor_id = Column(
        Integer, ForeignKey("contractors.id", ondelete="SET NULL"), nullable=True, index=True,
        comment="Nhà thầu thực hiện sửa chữa, tham chiếu tới contractors.id"
    )

    # --- Kiểm tra & sửa chữa ---
    test_tool_result = Column(
        Enum(TestResult, name="test_result_enum", values_callable=lambda e: [x.value for x in e]),
        nullable=True,
        comment="Kết quả test bằng công cụ đo ban đầu, là lý do board được gửi sửa"
    )
    
    failure_cause = Column(
        Text, nullable=True,
        comment="Nguyên nhân gây lỗi sau khi phân tích, vd sai giá trị linh kiện U12, hỏng C46"
    )
    disposition = Column(
        Text, nullable=True,
        comment="Biện pháp xử lý được quyết định, vd repair U12, no repair"
    )
    after_repair_result = Column(
        Enum(TestResult, name="after_repair_result", values_callable=lambda e: [x.value for x in e]),
        nullable=True,
        comment="Kết quả test lại sau khi sửa (PASS/FAIL), xác nhận việc sửa có thành công"
    )

    # --- Ảnh ---
    repair_photo_path = Column(
        String(500), nullable=True,
        comment="Đường dẫn ảnh chụp board trong/sau khi sửa chữa"
    )
    failure_verification_photo_path = Column(
        String(500), nullable=True,
        comment="Đường dẫn ảnh xác minh hiện tượng lỗi thực tế trên board"
    )

    # Ảnh chụp TRƯỚC KHI TEST (tối đa 2 ảnh) - ghi lại tình trạng board ngay
    # khi tiếp nhận, trước khi chạy công cụ test.
    before_test_photo_1 = Column(
        String(500), nullable=True,
        comment="Ảnh thứ 1 chụp board trước khi test"
    )
    before_test_photo_2 = Column(
        String(500), nullable=True,
        comment="Ảnh thứ 2 chụp board trước khi test"
    )

    # Ảnh chụp SAU KHI SỬA XONG VÀ TEST LẠI PASS (tối đa 2 ảnh) - xác nhận
    # board đã sửa xong, hoạt động tốt trước khi trả lại.
    after_repair_photo_1 = Column(
        String(500), nullable=True,
        comment="Ảnh thứ 1 chụp board sau khi sửa xong và test lại PASS"
    )
    after_repair_photo_2 = Column(
        String(500), nullable=True,
        comment="Ảnh thứ 2 chụp board sau khi sửa xong và test lại PASS"
    )
    after_repair_photo_3 = Column(
        String(500), nullable=True,
        comment="Ảnh thứ 3 chụp board sau khi sửa xong và test lại PASS"
    )

    # --- Cột chưa xác nhận đầy đủ ý nghĩa ---
    puss_f = Column(
        String(50), nullable=True
    )

    # --- Trạng thái tại trạm sạc ---
    charging_station_test_status = Column(
        String(50), nullable=True,
        comment="Trạng thái kiểm tra vận hành tại trạm sạc sau khi lắp lại board, vd pending"
    )

    # --- Ghi chú & định danh ---
    detailed_remarks = Column(
        Text, nullable=True,
        comment="Ghi chú chi tiết về lỗi/cách sửa, có thể kèm tên người phân tích"
    )
    ticket_id = Column(
        String(100), nullable=True, index=True,
        comment="Mã phiếu yêu cầu sửa chữa, dùng để tra cứu chéo hệ thống khác"
    )
    sn = Column(
        String(100), nullable=True, index=True,
        comment="Serial Number, định danh duy nhất của board vật lý"
    )
    code = Column(
        String(100), nullable=True, index=True,
        comment="Mã định danh thiết bị (không unique - 1 thiết bị có thể xuất hiện ở nhiều lần sửa chữa khác nhau)"
    )

    date_onsite = Column(
        Date, nullable=True,
        comment="Ngày board được đưa vào vận hành thực tế tại trạm sạc, trước khi phát sinh lỗi"
    )
    original_phenomenon = Column(
        Text, nullable=True,
        comment="Hiện tượng lỗi ban đầu do hiện trường/khách hàng báo lại, trước khi phân tích sâu"
    )
    station_code = Column(
        String(50), nullable=True, index=True,
        comment="Mã trạm sạc nơi board được lắp đặt"
    )

    # --- Metadata hệ thống ---
    created_at = Column(
        DateTime, server_default=func.now(),
        comment="Thời điểm bản ghi được tạo trong hệ thống"
    )
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(),
        comment="Thời điểm bản ghi được cập nhật gần nhất"
    )

    # --- Quan hệ ---
    board = relationship("Board", backref="repairs")
    contractor = relationship("Contractor", backref="repairs")

    def __repr__(self):
        return f"<Repair id={self.id} board_id={self.board_id} ticket_id={self.ticket_id}>"

    # ------------------------------------------------------------------
    # Sinh code định danh thiết bị tự động
    # ------------------------------------------------------------------

    @staticmethod
    def generate_code(db, board_code: str) -> Optional[str]:
        """
        Tự sinh `code` định danh thiết bị = board_code + 1 ký tự chữ cái tăng
        dần (A, B, C, ..., Z, AA, AB, ...), dựa theo số lượng Repair đã có
        cùng board_code trong DB tại thời điểm gọi.

        Vd: board_code = "OCPP_Three_V2.6_22KW"
            -> thiết bị thứ 1 (chưa có repair nào cùng board_code): "OCPP_Three_V2.6_22KWA"
            -> thiết bị thứ 2: "OCPP_Three_V2.6_22KWB"
            -> ... thiết bị thứ 27: "OCPP_Three_V2.6_22KWAA"

        Trả về None nếu board_code rỗng (không có gì để sinh code từ đó).

        LƯU Ý: đây là "đếm số Repair đã có cùng board_code", KHÔNG PHẢI "đếm số
        thiết bị vật lý duy nhất". Nếu 1 thiết bị được sửa 2 lần (2 dòng Repair
        cùng SN nhưng cùng board_code), hàm này sẽ sinh 2 code KHÁC NHAU cho 2
        lần đó (vì đơn giản là đếm theo thứ tự record, không tra theo SN) - cần
        cân nhắc nếu ý đồ thực tế là mỗi thiết bị vật lý chỉ có đúng 1 code cố
        định xuyên suốt các lần sửa.
        """
        if not board_code:
            return None
        existing_count = db.query(Repair).filter(Repair.board_code == board_code).count()
        return f"{board_code}{Repair._index_to_letters(existing_count)}"

    @staticmethod
    def _index_to_letters(index: int) -> str:
        """0 -> 'A', 1 -> 'B', ..., 25 -> 'Z', 26 -> 'AA', 27 -> 'AB', ...
        (giống cách Excel đặt tên cột A, B, ..., Z, AA, AB, ...)."""
        letters = ""
        index += 1
        while index > 0:
            index, remainder = divmod(index - 1, 26)
            letters = string.ascii_uppercase[remainder] + letters
        return letters
