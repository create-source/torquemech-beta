from __future__ import annotations

from sqlalchemy import Column, String, Text, DateTime, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class OBDCode(Base):
    __tablename__ = "obd_codes"

    code = Column(String(10), primary_key=True)  # P0300
    title = Column(String(255), nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    severity = Column(String(16), nullable=False, default="medium")

    causes = Column(JSONB, nullable=False, default=list)
    symptoms = Column(JSONB, nullable=False, default=list)
    recommended_services = Column(JSONB, nullable=False, default=list)
    keywords = Column(Text, nullable=False, default="")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)