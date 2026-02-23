# -*- coding: utf-8 -*-
"""SQLite + SQLAlchemy: краткосрочная/долгосрочная память, настройки, чат, вложения, архивы журнала мыслей."""
import json
import logging
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey, create_engine
from sqlalchemy.orm import sessionmaker, relationship, joinedload
from sqlalchemy.ext.declarative import declarative_base

from app.config import Config

logger = logging.getLogger(__name__)

# Создаём директорию для БД при необходимости
Path(Config.DBPATH).parent.mkdir(parents=True, exist_ok=True)

BASE = declarative_base()


class ShortTermMemory(BASE):
    """Краткосрочная память — последние саморазмышления (ответ LLM + посыл, с которым спрашивали)."""
    __tablename__ = "short_term_memory"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    text = Column(Text, nullable=False)
    tags = Column(Text, default="")  # comma-separated
    trigger_text = Column(Text, default="")  # посыл (вопрос к себе), отправленный в LLM
    model_name = Column(Text, default="")  # модель на момент генерации
    meta_json = Column(Text, default="")  # JSON: context_limit, intro_max_tokens и т.п.


class LongTermMemory(BASE):
    """Долгосрочная память — то, что бот или пользователь пометил как важное."""
    __tablename__ = "long_term_memory"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    text = Column(Text, nullable=False)
    tags = Column(Text, default="")


class Settings(BASE):
    """Настройки: периодичность саморазмышлений, лимит токенов, первичный промпт."""
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(Text, unique=True, nullable=False)
    value = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ThoughtArchive(BASE):
    """Архив ветки размышлений: снимок журнала мыслей (data — JSON список записей {text, tags, trigger_text})."""
    __tablename__ = "thought_archive"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    data = Column(Text, nullable=False)  # JSON array of {"text", "tags", "trigger_text"}


class ChatMessage(BASE):
    """Сообщение в чате (веб): от пользователя или от бота."""
    __tablename__ = "chat_message"
    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(Text, nullable=False)  # "user" | "assistant"
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    model_name = Column(Text, default="")  # для assistant: модель на момент ответа
    meta_json = Column(Text, default="")  # JSON: context_limit, chat_max_tokens и т.п.
    attachments = relationship("Attachment", back_populates="message", cascade="all, delete-orphan")


class Attachment(BASE):
    """Вложение к сообщению чата — файл как «пища для размышлений»."""
    __tablename__ = "attachment"
    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(Integer, ForeignKey("chat_message.id", ondelete="CASCADE"), nullable=False)
    message = relationship("ChatMessage", back_populates="attachments")
    filename = Column(Text, nullable=False)
    # Храним путь к файлу на диске или текст извлечённого содержимого
    content_path = Column(Text, default="")
    content_text = Column(Text, default="")  # прочитанный текст для контекста LLM
    created_at = Column(DateTime, default=datetime.utcnow)


def _engine():
    return create_engine(
        f"sqlite:///{Path(Config.DBPATH).resolve()}",
        connect_args={"check_same_thread": False},
        echo=Config.DEBUG,
    )


engine = _engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    """Создание таблиц при первом запуске и миграция (добавление trigger_text при необходимости)."""
    BASE.metadata.create_all(engine)
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE short_term_memory ADD COLUMN trigger_text TEXT"))
            conn.commit()
    except Exception:
        pass
    for table, col in [("short_term_memory", "model_name"), ("short_term_memory", "meta_json"), ("chat_message", "model_name"), ("chat_message", "meta_json")]:
        try:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE {} ADD COLUMN {} TEXT".format(table, col)))
                conn.commit()
        except Exception:
            pass
    # Строка для межпроцессной блокировки саморазмышления (один цикл на всё приложение)
    _ensure_introspection_lock_row()


def _ensure_introspection_lock_row():
    """Создаёт запись introspection_lock в settings, если её нет (значение 0 = свободно)."""
    session = SessionLocal()
    try:
        row = session.query(Settings).filter(Settings.key == "introspection_lock").first()
        if not row:
            session.add(Settings(key="introspection_lock", value="0"))
            session.commit()
    finally:
        session.close()


def try_acquire_introspection_lock() -> bool:
    """Атомарно захватывает блокировку саморазмышления (межпроцессная). True — захвачено, False — уже занято."""
    session = SessionLocal()
    try:
        n = session.query(Settings).filter(
            Settings.key == "introspection_lock",
            Settings.value == "0",
        ).update({"value": "1", "updated_at": datetime.utcnow()}, synchronize_session=False)
        session.commit()
        return n > 0
    finally:
        session.close()


def release_introspection_lock() -> None:
    """Освобождает блокировку саморазмышления."""
    set_setting("introspection_lock", "0")


def release_introspection_lock_if_stale(max_age_seconds: int = 300) -> bool:
    """Если блокировка занята дольше max_age_seconds (например после падения процесса), снимаем. Возвращает True если сняли."""
    session = SessionLocal()
    try:
        row = session.query(Settings).filter(Settings.key == "introspection_lock").first()
        if not row or row.value != "1":
            return False
        if not row.updated_at:
            row.value = "0"
            row.updated_at = datetime.utcnow()
            session.commit()
            return True
        age = (datetime.utcnow() - row.updated_at).total_seconds()
        if age > max_age_seconds:
            row.value = "0"
            row.updated_at = datetime.utcnow()
            session.commit()
            return True
        return False
    finally:
        session.close()


# ---------- Репозитории: краткосрочная память ----------
def add_short_term_memory(text: str, tags: str = "", trigger_text: str = "", model_name: str = "", meta_json: str = "") -> int:
    """Сохраняет запись в краткосрочную память (ответ LLM, опционально посыл, модель и мета). Возвращает id."""
    session = SessionLocal()
    try:
        rec = ShortTermMemory(
            text=text,
            tags=tags or "",
            trigger_text=trigger_text or "",
            model_name=model_name or "",
            meta_json=meta_json or "",
        )
        session.add(rec)
        session.commit()
        return rec.id
    finally:
        session.close()


def get_recent_short_term_memory(limit: int = 20):
    """Последние N записей краткосрочной памяти (для контекста)."""
    session = SessionLocal()
    try:
        return (
            session.query(ShortTermMemory)
            .order_by(ShortTermMemory.timestamp.desc())
            .limit(limit)
            .all()
        )
    finally:
        session.close()


def get_short_term_memory_count() -> int:
    """Общее количество записей в журнале размышлений (для пагинации)."""
    session = SessionLocal()
    try:
        return session.query(ShortTermMemory).count()
    finally:
        session.close()


def get_short_term_memory_paged(offset: int = 0, limit: int = 50):
    """Записи журнала размышлений в хронологическом порядке (старые первые) для пагинации. offset, limit — страница."""
    session = SessionLocal()
    try:
        return (
            session.query(ShortTermMemory)
            .order_by(ShortTermMemory.timestamp.asc(), ShortTermMemory.id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    finally:
        session.close()


def get_all_short_term_memory(max_records: int = 50000):
    """Все записи журнала размышлений в хронологическом порядке (для экспорта и архивации без обрезки)."""
    session = SessionLocal()
    try:
        return (
            session.query(ShortTermMemory)
            .order_by(ShortTermMemory.timestamp.asc(), ShortTermMemory.id.asc())
            .limit(max_records)
            .all()
        )
    finally:
        session.close()


def get_last_intro_thoughts(limit: int = 2):
    """Последние N записей с тегом intro (для проверки повторяемости)."""
    session = SessionLocal()
    try:
        return (
            session.query(ShortTermMemory)
            .filter(ShortTermMemory.tags.contains("intro"))
            .order_by(ShortTermMemory.timestamp.desc())
            .limit(limit)
            .all()
        )
    finally:
        session.close()


def clear_short_term_memory() -> int:
    """Удаляет все записи краткосрочной памяти (журнал мыслей). Возвращает количество удалённых."""
    session = SessionLocal()
    try:
        rows = session.query(ShortTermMemory).all()
        count = len(rows)
        for rec in rows:
            session.delete(rec)
        session.commit()
        return count
    finally:
        session.close()


# ---------- Репозитории: архивы журнала мыслей ----------
def add_thought_archive(name: str, entries: List[Dict[str, Any]]) -> int:
    """Сохранить архив ветки размышлений. entries — список {"text", "tags", "trigger_text"} в хронологическом порядке."""
    session = SessionLocal()
    try:
        data_json = json.dumps(entries, ensure_ascii=False)
        rec = ThoughtArchive(name=name, data=data_json)
        session.add(rec)
        session.commit()
        return rec.id
    finally:
        session.close()


def get_thought_archives_list(limit: int = 100) -> List[Any]:
    """Список архивов (новые первые). Каждый объект: id, name, created_at, entries_count."""
    session = SessionLocal()
    try:
        rows = (
            session.query(ThoughtArchive)
            .order_by(ThoughtArchive.created_at.desc())
            .limit(limit)
            .all()
        )
        out = []
        for r in rows:
            try:
                entries = json.loads(r.data) if r.data else []
            except Exception:
                entries = []
            out.append({
                "id": r.id,
                "name": r.name or "",
                "created_at": r.created_at,
                "entries_count": len(entries),
            })
        return out
    finally:
        session.close()


def get_thought_archive(archive_id: int) -> Optional[Dict[str, Any]]:
    """Один архив по id: { id, name, created_at, entries: [{ text, tags, trigger_text }] } или None."""
    session = SessionLocal()
    try:
        rec = session.query(ThoughtArchive).filter(ThoughtArchive.id == archive_id).first()
        if not rec:
            return None
        try:
            entries = json.loads(rec.data) if rec.data else []
        except Exception:
            entries = []
        return {
            "id": rec.id,
            "name": rec.name or "",
            "created_at": rec.created_at,
            "entries": entries,
        }
    finally:
        session.close()


def delete_thought_archive(archive_id: int) -> bool:
    """Удалить архив. Возвращает True если удалён."""
    session = SessionLocal()
    try:
        rec = session.query(ThoughtArchive).filter(ThoughtArchive.id == archive_id).first()
        if not rec:
            return False
        session.delete(rec)
        session.commit()
        return True
    finally:
        session.close()


# ---------- Репозитории: долгосрочная память ----------
def add_long_term_memory(text: str, tags: str = "") -> int:
    session = SessionLocal()
    try:
        rec = LongTermMemory(text=text, tags=tags or "")
        session.add(rec)
        session.commit()
        logger.info("[db] add_long_term_memory: saved id=%s, tags=%s, text_len=%s.", rec.id, tags or "(empty)", len(text or ""))
        return rec.id
    finally:
        session.close()


def get_long_term_memory_list(limit: int = 100):
    """Список записей долгосрочной памяти (для журнала и контекста)."""
    session = SessionLocal()
    try:
        return (
            session.query(LongTermMemory)
            .order_by(LongTermMemory.timestamp.desc())
            .limit(limit)
            .all()
        )
    finally:
        session.close()


# ---------- Репозитории: настройки ----------
def get_setting(key: str, default: str = "") -> str:
    session = SessionLocal()
    try:
        row = session.query(Settings).filter(Settings.key == key).first()
        return row.value if row else default
    finally:
        session.close()


def set_setting(key: str, value: str) -> None:
    session = SessionLocal()
    try:
        row = session.query(Settings).filter(Settings.key == key).first()
        if row:
            row.value = value
            row.updated_at = datetime.utcnow()
        else:
            session.add(Settings(key=key, value=value))
        session.commit()
    finally:
        session.close()


# ---------- Репозитории: чат и вложения ----------
def add_chat_message(role: str, text: str, model_name: str = "", meta_json: str = "") -> int:
    """Сохраняет сообщение чата. Для assistant можно передать model_name и meta_json (параметры на момент ответа)."""
    session = SessionLocal()
    try:
        msg = ChatMessage(role=role, text=text, model_name=model_name or "", meta_json=meta_json or "")
        session.add(msg)
        session.commit()
        return msg.id
    finally:
        session.close()


def add_attachment(message_id: int, filename: str, content_path: str = "", content_text: str = "") -> int:
    session = SessionLocal()
    try:
        att = Attachment(
            message_id=message_id,
            filename=filename,
            content_path=content_path or "",
            content_text=content_text or "",
        )
        session.add(att)
        session.commit()
        return att.id
    finally:
        session.close()


def get_recent_chat_messages(limit: int = 50):
    """Последние сообщения чата с вложениями (для контекста и отображения), в хронологическом порядке."""
    session = SessionLocal()
    try:
        rows = (
            session.query(ChatMessage)
            .options(joinedload(ChatMessage.attachments))
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(rows))
    finally:
        session.close()


def get_chat_messages_paged(offset: int = 0, limit: int = 50):
    """Сообщения чата в хронологическом порядке (старые первые) для пагинации. offset, limit — страница."""
    session = SessionLocal()
    try:
        return (
            session.query(ChatMessage)
            .options(joinedload(ChatMessage.attachments))
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    finally:
        session.close()


def get_chat_message_count() -> int:
    """Общее количество сообщений в чате (для суммаризации раз в N)."""
    session = SessionLocal()
    try:
        return session.query(ChatMessage).count()
    finally:
        session.close()


def clear_chat_messages() -> int:
    """Удаляет все сообщения чата (и вложения по cascade). Возвращает количество удалённых сообщений."""
    session = SessionLocal()
    try:
        rows = session.query(ChatMessage).all()
        count = len(rows)
        for msg in rows:
            session.delete(msg)
        session.commit()
        return count
    finally:
        session.close()


def get_last_user_message_time() -> Optional[datetime]:
    """Время последнего сообщения от пользователя в чате (для таймера простоя). None, если сообщений не было."""
    session = SessionLocal()
    try:
        row = (
            session.query(ChatMessage)
            .filter(ChatMessage.role == "user")
            .order_by(ChatMessage.created_at.desc())
            .first()
        )
        return row.created_at if row else None
    finally:
        session.close()
