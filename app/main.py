# -*- coding: utf-8 -*-
"""Точка входа: инициализация БД, планировщик саморазмышлений, Flask (и опционально бот)."""
import os
import sys

# Корень проекта (родитель каталога app/) в sys.path — чтобы работал и запуск python app/main.py
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

import logging

from app.db import init_db
from app.scheduler import start_scheduler
from app.api import create_app

_log_level = logging.DEBUG if os.getenv("DEBUG", "True").lower() == "true" else logging.INFO
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
logger.info("AST-Thinker starting (log level=%s).", logging.getLevelName(_log_level))

if __name__ == "__main__":
    init_db()
    start_scheduler()
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "7111")), debug=os.getenv("DEBUG", "True").lower() == "true")
