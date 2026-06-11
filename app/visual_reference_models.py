from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()


class VisualReferenceRecord(Base):
    __tablename__ = "visual_reference_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_identifier = Column(String(255), nullable=False, index=True)
    service_type = Column(String(160), nullable=False, index=True)
    title = Column(String(255), nullable=False, default="")
    quick_reference = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    images = relationship(
        "VisualReferenceImage",
        back_populates="visual_reference",
        cascade="all, delete-orphan",
    )
    specs = relationship(
        "VisualReferenceSpec",
        back_populates="visual_reference",
        cascade="all, delete-orphan",
    )
    oem_parts = relationship(
        "VisualReferenceOemPart",
        back_populates="visual_reference",
        cascade="all, delete-orphan",
    )


class VisualReferenceImage(Base):
    __tablename__ = "visual_reference_images"
    __table_args__ = (
        CheckConstraint(
            "image_type IN ("
            "'component_location', 'exploded_view', 'belt_routing', "
            "'connector_view', 'reference_image'"
            ")",
            name="ck_visual_reference_images_type",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    visual_reference_id = Column(Integer, ForeignKey("visual_reference_records.id"), nullable=False, index=True)
    image_type = Column(String(64), nullable=False)
    image_path = Column(Text, nullable=False, default="")
    caption = Column(Text, nullable=False, default="")

    visual_reference = relationship("VisualReferenceRecord", back_populates="images")


class VisualReferenceSpec(Base):
    __tablename__ = "visual_reference_specs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    visual_reference_id = Column(Integer, ForeignKey("visual_reference_records.id"), nullable=False, index=True)
    spec_name = Column(String(160), nullable=False)
    spec_value = Column(String(120), nullable=False)
    spec_unit = Column(String(40), nullable=False, default="")

    visual_reference = relationship("VisualReferenceRecord", back_populates="specs")


class VisualReferenceOemPart(Base):
    __tablename__ = "visual_reference_oem_parts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    visual_reference_id = Column(Integer, ForeignKey("visual_reference_records.id"), nullable=False, index=True)
    part_name = Column(String(180), nullable=False)
    oem_part_number = Column(String(120), nullable=False)
    future_parts_intelligence_id = Column(Integer)

    visual_reference = relationship("VisualReferenceRecord", back_populates="oem_parts")
