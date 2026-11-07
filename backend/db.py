from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "task"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    prompt_text: Mapped[str] = mapped_column(Text)
    cookie_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    cookie_expire_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    videos: Mapped[list["Video"]] = relationship(back_populates="task", order_by="Video.id")


class Video(Base):
    __tablename__ = "video"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    material_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(500), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    encode_url: Mapped[str] = mapped_column(Text, default="")
    duration_s: Mapped[float] = mapped_column(Float, default=0)
    # resolve_failed | queued | running | done | failed
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(20), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    artifacts_json: Mapped[str] = mapped_column(Text, default="{}")
    pending_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_generation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    task: Mapped[Task] = relationship(back_populates="videos")
    generations: Mapped[list["Generation"]] = relationship(back_populates="video", order_by="Generation.version_no")


class Generation(Base):
    __tablename__ = "generation"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    prompt_text: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))
    asr_model: Mapped[str] = mapped_column(String(100))
    llm_raw_json: Mapped[str] = mapped_column(Text, default="{}")
    excluded_json: Mapped[str] = mapped_column(Text, default="[]")
    target_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    video: Mapped[Video] = relationship(back_populates="generations")
    marks: Mapped[list["Mark"]] = relationship(back_populates="generation", order_by="Mark.time_s")


class Mark(Base):
    __tablename__ = "mark"
    id: Mapped[int] = mapped_column(primary_key=True)
    generation_id: Mapped[int] = mapped_column(ForeignKey("generation.id"), index=True)
    time_s: Mapped[float] = mapped_column(Float)
    question: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text, default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(10), default="ai")
    ai_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    generation: Mapped[Generation] = relationship(back_populates="marks")

    @property
    def edited(self) -> bool:
        return (self.source == "manual" or self.deleted or self.confirmed
                or self.time_s != self.ai_time_s or self.question != self.ai_question
                or self.answer != self.ai_answer)


class DefaultPrompt(Base):
    __tablename__ = "default_prompt"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSession(Base):
    __tablename__ = "app_session"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


engine = None
SessionLocal: sessionmaker | None = None


def init_db() -> None:
    global engine, SessionLocal
    s = get_settings()
    url = s.database_url
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}
                           if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragma(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
