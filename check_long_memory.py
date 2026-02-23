# -*- coding: utf-8 -*-
"""Проверка таблицы long_term_memory в БД (запуск в Docker: docker exec thinker python /app/check_long_memory.py или локально)."""
import os
import sqlite3

def main():
    db_path = os.environ.get("DB_PATH", "/app/data/db.sqlite")
    if not os.path.isfile(db_path):
        print("DB not found:", db_path)
        return
    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='long_term_memory'")
    if not cur.fetchone():
        print("Table long_term_memory does not exist")
        conn.close()
        return
    cur = conn.execute("SELECT id, timestamp, tags, text FROM long_term_memory ORDER BY timestamp DESC")
    rows = cur.fetchall()
    print("Rows in long_term_memory:", len(rows))
    for r in rows:
        tid, ts, tags, text = r[0], r[1], r[2], (r[3] or "")[:100]
        print("  id=%s ts=%s tags=%s text=%s" % (tid, ts, tags, text))
    conn.close()

if __name__ == "__main__":
    main()
