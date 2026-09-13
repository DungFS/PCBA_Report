from app.bot.commands.start_cmd import StartCommand
from app.bot.commands.help_cmd import HelpCommand
from app.bot.commands.admin_cmd import AdminCommand
from app.bot.commands.add_user_cmd import AddUserCommand
from app.bot.commands.delete_user_cmd import DeleteUserCommand
# from app.bot.commands.import_repair_command import ImportRepairCommand
from app.bot.commands.board_command import BoardCommand
from app.bot.commands.repair_command import RepairCommand
from app.bot.commands.contractor_command import ContractorCommand
from app.bot.commands.contractor_report_command import ContractorReportCommand
from app.bot.commands.user_report_command import UserReportCommand
from app.bot.commands.repair_detail_report_command import RepairDetailReportCommand
from app.bot.commands.report_daily_command import ReportDailyCommand

def register_handlers(bot):
    # Khởi tạo và đăng ký từng class lệnh độc lập
    StartCommand(bot).register()
    HelpCommand(bot).register()
    AdminCommand(bot).register()
    AddUserCommand(bot).register()
    DeleteUserCommand(bot).register()
    # ImportRepairCommand(bot).register()
    BoardCommand(bot).register()
    RepairCommand(bot).register()
    ContractorCommand(bot).register()
    ContractorReportCommand(bot).register()
    UserReportCommand(bot).register()
    RepairDetailReportCommand(bot).register()
    ReportDailyCommand(bot).register()
    
    # (Tùy chọn) Bắt tin nhắn rác hoặc echo chung ở cuối cùng nếu muốn
    @bot.message_handler(func=lambda message: True)
    def fallback_echo(message):
        pass # Hoặc xử lý tin nhắn không khớp lệnh