# -*- coding: utf-8 -*-
"""Временный тест: вывести самое раннее сообщение из журнала размышлений (short_term_memory)."""
import sys
import os
import sqlite3
from pathlib import Path

if sys.stdout.encoding is None or sys.stdout.encoding.upper() != "UTF-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app.config import Config

def main():
    db_path = Path(Config.DBPATH).resolve()
    if not db_path.is_file():
        print("БД не найдена:", db_path)
        return
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT id, timestamp, text, tags FROM short_term_memory ORDER BY timestamp ASC, id ASC LIMIT 1"
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        print("Журнал пуст, записей нет.")
        return
    text = row["text"] or ""
    print("--- Самое раннее сообщение в журнале размышлений ---")
    print("id:", row["id"])
    print("timestamp:", row["timestamp"] or "")
    print("tags:", row["tags"] or "")
    print("text:", text[:500] + ("..." if len(text) > 500 else ""))
    print("---")

if __name__ == "__main__":
    main()
