# app/services/ticket_api.py
"""
Service lấy thông tin ticket từ hệ thống bên thứ 3.

HIỆN TẠI CHƯA IMPLEMENT GỌI API THẬT - đây chỉ là interface/stub để nơi khác
(command_board_command.py) gọi vào mà không cần biết chi tiết bên trong. Khi
có endpoint thật, chỉ cần điền phần gọi HTTP bên trong get_ticket_info(), giữ
nguyên chữ ký hàm và cấu trúc dict trả về (TicketInfo) để không phải sửa code
ở nơi gọi.
"""
from typing import Optional, TypedDict


class TicketInfo(TypedDict, total=False):
    sn: Optional[str]
    date_onsite: Optional[str]
    original_phenomenon: Optional[str]
    station_code: Optional[str]
    failure_verification_photo_path: Optional[str]


def get_ticket_info(ticket_id: str) -> Optional[TicketInfo]:
    """
    Lấy thông tin ticket từ API bên thứ 3, dựa theo ticket_id.

    TODO: implement gọi API thật ở đây, ví dụ:

        import requests
        response = requests.get(f"https://third-party.example.com/tickets/{ticket_id}", timeout=10)
        if response.status_code != 200:
            return None
        data = response.json()
        return TicketInfo(
            sn=data.get("sn"),
            date_onsite=data.get("date_onsite"),
            original_phenomenon=data.get("original_phenomenon"),
            station_code=data.get("station_code"),
            failure_verification_photo_path=data.get("failure_verification_photo_path"),
        )

    Args:
        ticket_id: mã ticket cần tra cứu.

    Returns:
        TicketInfo (dict) chứa sn, date_onsite, original_phenomenon,
        station_code, failure_verification_photo_path - hoặc None nếu không
        tìm thấy ticket / lỗi gọi API / chưa implement.
    """
    # Chưa có API thật - trả None. Nơi gọi (command_board_command.py) phải xử
    # lý được trường hợp None (không có gì để điền thêm, vẫn tạo record bình
    # thường với các field còn lại trống).
    return None