# app/models/mixins.py
from sqlalchemy import Column, Integer, ForeignKey, event
from sqlalchemy.orm import relationship, declared_attr
from app.core.audit_context import current_user_id


class AuditMixin:
    @declared_attr
    def created_by_id(cls):
        return Column(Integer, ForeignKey("users.id"), nullable=True, comment="User đã tạo bản ghi")

    @declared_attr
    def updated_by_id(cls):
        return Column(Integer, ForeignKey("users.id"), nullable=True, comment="User cập nhật gần nhất")

    @declared_attr
    def created_by(cls):
        return relationship("User", foreign_keys=[cls.created_by_id])

    @declared_attr
    def updated_by(cls):
        return relationship("User", foreign_keys=[cls.updated_by_id])


@event.listens_for(AuditMixin, "before_insert", propagate=True)
def _set_created_by(mapper, connection, target):
    uid = current_user_id.get()
    print(f"[DEBUG AuditMixin] before_insert - target={type(target).__name__} - current_user_id.get() = {uid!r}")
    if uid:
        target.created_by_id = uid
        target.updated_by_id = uid


@event.listens_for(AuditMixin, "before_update", propagate=True)
def _set_updated_by(mapper, connection, target):
    uid = current_user_id.get()
    print(f"[DEBUG AuditMixin] before_update - target={type(target).__name__} - current_user_id.get() = {uid!r}")
    if uid:
        target.updated_by_id = uid