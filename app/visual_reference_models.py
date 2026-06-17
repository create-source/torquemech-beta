from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
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
    hotspots = relationship(
        "VisualReferenceHotspot",
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


class VisualReferenceHotspot(Base):
    __tablename__ = "visual_reference_hotspots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    visual_reference_id = Column(Integer, ForeignKey("visual_reference_records.id"), nullable=False, index=True)
    label = Column(String(120), nullable=False)
    hotspot_type = Column(String(64), nullable=False)
    x_percent = Column(Float, nullable=False)
    y_percent = Column(Float, nullable=False)
    title = Column(String(180), nullable=False)
    description = Column(Text, nullable=False, default="")
    torque_spec = Column(String(120), nullable=False, default="")
    fastener_size = Column(String(80), nullable=False, default="")
    tool_size = Column(String(80), nullable=False, default="")
    oem_part_number = Column(String(120), nullable=False, default="")
    related_part_name = Column(String(180), nullable=False, default="")
    parts_intelligence_id = Column(Integer)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    visual_reference = relationship("VisualReferenceRecord", back_populates="hotspots")
