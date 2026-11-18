from sqlalchemy import Column,Integer,String,Float,JSON,Text,ForeignKey,UniqueConstraint
from src.config.database import Base


class ProcessReading(Base):
    """One process's individually numbered colour-difference reading."""
    __tablename__='process_readings'
    __table_args__=(UniqueConstraint('study_id','process','label'),)
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('studies.id'),nullable=False)
    process=Column(String(10),nullable=False)          # 'before' 原工艺 / 'after' 新工艺
    label=Column(String(100),nullable=False)           # 读数编号；两道工艺各自编号，允许对不上
    value=Column(Float,nullable=False)                 # 色差 ΔE
    measured_at=Column(String(40),nullable=False)      # 规范化的 UTC ISO 8601 时刻
    created_at=Column(String(40),nullable=False)


class PairingPreview(Base):
    """Auditable pairing proposal; confirmation guarded by input_fingerprint."""
    __tablename__='pairing_previews'
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('studies.id'),nullable=False)
    max_gap_seconds=Column(Float,nullable=False)
    input_fingerprint=Column(String(64),nullable=False)
    readings_snapshot=Column(JSON,nullable=False)
    result=Column(JSON,nullable=False)
    status=Column(String(20),default='preview',nullable=False)  # preview / confirmed / invalidated
    created_by=Column(String(120),nullable=False)
    created_at=Column(String(40),nullable=False)
    confirmed_at=Column(String(40),nullable=True)
    invalidated_at=Column(String(40),nullable=True)
    invalidate_reason=Column(Text,default='',nullable=False)


class ActivePairing(Base):
    """The one pairing a batch currently exposes to analysis. Switching is a single row update."""
    __tablename__='active_pairings'
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('studies.id'),unique=True,nullable=False)
    preview_id=Column(Integer,ForeignKey('pairing_previews.id'),nullable=False)
    confirmed_at=Column(String(40),nullable=False)
