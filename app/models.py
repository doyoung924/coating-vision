"""
SQLAlchemy ORM 모델 (§3단계 3-1).

sql/schema.sql 의 7 테이블과 1:1 대응.
테이블 생성은 하지 않는다 (DDL 은 schema.sql / init_db.py 로 관리).
Alembic 도 아직 도입 안 함.

import 방향: models → db → config.
"""

from sqlalchemy import (
    Column,
    ForeignKey,
    Numeric,
    String,
    TIMESTAMP,
    text,
)
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()


class User(Base):
    __tablename__ = "USERS"

    id = Column("ID", Numeric, primary_key=True)
    username = Column("USERNAME", String(50), unique=True, nullable=False)
    password_hash = Column("PASSWORD_HASH", String(255), nullable=False)
    email = Column("EMAIL", String(120), unique=True)
    full_name = Column("FULL_NAME", String(100))
    role = Column("ROLE", String(20), server_default="inspector")
    is_active = Column("IS_ACTIVE", Numeric(1), server_default=text("1"))
    created_at = Column("CREATED_AT", TIMESTAMP, server_default=text("SYSTIMESTAMP"))
    last_login_at = Column("LAST_LOGIN_AT", TIMESTAMP)

    inspections = relationship(
        "Inspection", back_populates="user",
        foreign_keys="Inspection.user_id",
    )
    posts = relationship("Post", back_populates="user")

    def __repr__(self):
        return "<User id={} username={!r} role={!r}>".format(
            self.id, self.username, self.role,
        )


class Inspection(Base):
    __tablename__ = "INSPECTIONS"

    id = Column("ID", Numeric, primary_key=True)
    user_id = Column("USER_ID", Numeric, ForeignKey("USERS.ID"))
    file_name = Column("FILE_NAME", String(255))
    image_path = Column("IMAGE_PATH", String(512))
    heatmap_path = Column("HEATMAP_PATH", String(512))
    annotated_path = Column("ANNOTATED_PATH", String(512))
    seg_path = Column("SEG_PATH", String(512))
    source = Column("SOURCE", String(20))
    created_at = Column("CREATED_AT", TIMESTAMP, server_default=text("SYSTIMESTAMP"))
    a3_ratio = Column("A3_RATIO", Numeric(10, 6))
    a3_cells = Column("A3_CELLS", Numeric(5))
    seg_crack = Column("SEG_CRACK", Numeric(10, 6))
    seg_delam = Column("SEG_DELAM", Numeric(10, 6))
    pinhole_count = Column("PINHOLE_COUNT", Numeric(5))
    ms_a3 = Column("MS_A3", Numeric(10, 3))
    ms_yolo = Column("MS_YOLO", Numeric(10, 3))
    ms_seg = Column("MS_SEG", Numeric(10, 3))
    ms_total = Column("MS_TOTAL", Numeric(10, 3))
    is_cold_start = Column("IS_COLD_START", Numeric(1), server_default=text("0"))
    status = Column("STATUS", String(20), server_default="normal")
    reviewed_by = Column("REVIEWED_BY", Numeric, ForeignKey("USERS.ID"))
    reviewed_at = Column("REVIEWED_AT", TIMESTAMP)

    user = relationship(
        "User", back_populates="inspections",
        foreign_keys=[user_id],
    )
    reviewer = relationship("User", foreign_keys=[reviewed_by])
    detections = relationship(
        "Detection", back_populates="inspection", cascade="all, delete-orphan",
    )
    spc_points = relationship(
        "SpcPoint", back_populates="inspection", cascade="all, delete-orphan",
    )
    findings = relationship(
        "Finding", back_populates="inspection", cascade="all, delete-orphan",
    )
    posts = relationship("Post", back_populates="inspection")

    def __repr__(self):
        return "<Inspection id={} file_name={!r} status={!r}>".format(
            self.id, self.file_name, self.status,
        )


class Detection(Base):
    __tablename__ = "DETECTIONS"

    id = Column("ID", Numeric, primary_key=True)
    inspection_id = Column("INSPECTION_ID", Numeric, ForeignKey("INSPECTIONS.ID"), nullable=False)
    x1 = Column("X1", Numeric(7, 2))
    y1 = Column("Y1", Numeric(7, 2))
    x2 = Column("X2", Numeric(7, 2))
    y2 = Column("Y2", Numeric(7, 2))
    conf = Column("CONF", Numeric(5, 4))
    aspect = Column("ASPECT", Numeric(7, 3))
    roundness = Column("ROUNDNESS", Numeric(5, 4))

    inspection = relationship("Inspection", back_populates="detections")

    def __repr__(self):
        return "<Detection id={} inspection_id={} conf={}>".format(
            self.id, self.inspection_id, self.conf,
        )


class SpcPoint(Base):
    __tablename__ = "SPC_POINTS"

    id = Column("ID", Numeric, primary_key=True)
    inspection_id = Column("INSPECTION_ID", Numeric, ForeignKey("INSPECTIONS.ID"), nullable=False)
    metric = Column("METRIC", String(20))
    seq_no = Column("SEQ_NO", Numeric(7))
    value = Column("VALUE", Numeric(10, 6))
    ewma = Column("EWMA", Numeric(10, 6))
    center = Column("CENTER", Numeric(10, 6))
    ucl = Column("UCL", Numeric(10, 6))
    is_alarm = Column("IS_ALARM", Numeric(1), server_default=text("0"))

    inspection = relationship("Inspection", back_populates="spc_points")

    def __repr__(self):
        return "<SpcPoint id={} metric={} seq_no={} alarm={}>".format(
            self.id, self.metric, self.seq_no, self.is_alarm,
        )


class Finding(Base):
    __tablename__ = "FINDINGS"

    id = Column("ID", Numeric, primary_key=True)
    inspection_id = Column("INSPECTION_ID", Numeric, ForeignKey("INSPECTIONS.ID"), nullable=False)
    layer = Column("LAYER", String(20))
    content = Column("CONTENT", String(4000))
    confidence = Column("CONFIDENCE", String(10))
    source = Column("SOURCE", String(100))

    inspection = relationship("Inspection", back_populates="findings")

    def __repr__(self):
        return "<Finding id={} inspection_id={} layer={}>".format(
            self.id, self.inspection_id, self.layer,
        )


class Post(Base):
    __tablename__ = "POSTS"

    id = Column("ID", Numeric, primary_key=True)
    user_id = Column("USER_ID", Numeric, ForeignKey("USERS.ID"))
    inspection_id = Column("INSPECTION_ID", Numeric, ForeignKey("INSPECTIONS.ID"), nullable=True)
    category = Column("CATEGORY", String(20), server_default="free")
    title = Column("TITLE", String(200), nullable=False)
    content = Column("CONTENT", String(4000))
    view_count = Column("VIEW_COUNT", Numeric(7), server_default=text("0"))
    created_at = Column("CREATED_AT", TIMESTAMP, server_default=text("SYSTIMESTAMP"))
    updated_at = Column("UPDATED_AT", TIMESTAMP)

    user = relationship("User", back_populates="posts")
    inspection = relationship("Inspection", back_populates="posts")
    comments = relationship(
        "Comment", back_populates="post", cascade="all, delete-orphan",
    )

    def __repr__(self):
        return "<Post id={} category={} title={!r}>".format(
            self.id, self.category, self.title,
        )


class Comment(Base):
    __tablename__ = "COMMENTS"

    id = Column("ID", Numeric, primary_key=True)
    post_id = Column("POST_ID", Numeric, ForeignKey("POSTS.ID"), nullable=False)
    user_id = Column("USER_ID", Numeric, ForeignKey("USERS.ID"))
    content = Column("CONTENT", String(1000))
    created_at = Column("CREATED_AT", TIMESTAMP, server_default=text("SYSTIMESTAMP"))

    post = relationship("Post", back_populates="comments")
    user = relationship("User")

    def __repr__(self):
        return "<Comment id={} post_id={}>".format(self.id, self.post_id)
