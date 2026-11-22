from datetime import datetime

from sqlalchemy import ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base
from src.utils.time import now


class TrainCall(Base):
    """可更正的列车到离站记录，按站台和车次唯一。"""

    __tablename__ = 'train_calls'
    __table_args__ = (
        UniqueConstraint('platform', 'train_no'),
        Index('ix_train_calls_platform_arrival', 'platform', 'arrival_at'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(300))
    train_no: Mapped[str] = mapped_column(String(64))
    arrival_at: Mapped[datetime]
    departure_at: Mapped[datetime]
    revision: Mapped[int] = mapped_column(default=1)
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class RunTimetable(Base):
    """扫描受理时的到离站时刻表快照，随运行不可变。"""

    __tablename__ = 'run_timetables'

    run_id: Mapped[int] = mapped_column(ForeignKey('runs.id'), primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    captured_at: Mapped[datetime]
