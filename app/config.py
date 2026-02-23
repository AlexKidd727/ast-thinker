# -*- coding: utf-8 -*-
"""Конфигурация приложения. Переменные из .env. LLM: OpenAI-совместимый API (локальный сервер)."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Локальный OpenAI-совместимый API: GET /v1/models, POST /v1/chat/completions и др.
    API_BASE_URL: str = os.getenv("API_BASE_URL", "http://192.168.1.250:1234")
    API_BASE_URL_V1: str = API_BASE_URL.rstrip("/") + "/v1"
    API_KEY: str = os.getenv("API_KEY", "dummy")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "")

    # OpenRouter.ai: токен для API (можно также задать в настройках в БД)
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

    # Периодичность и лимиты (поддержка обоих вариантов имен в .env)
    INTRO_INTERVAL: int = int(os.getenv("INTRO_INTERVAL") or os.getenv("INTROINTERVAL", "300"))
    MAX_TOKENS: int = int(os.getenv("MAX_TOKENS") or os.getenv("MAXTOKENS", "1024"))
    MIN_TOKENS: int = int(os.getenv("MIN_TOKENS") or os.getenv("MINTOKENS", "256"))
    # Лимит токенов именно на саморазмышление (отдельная настройка)
    INTRO_MAX_TOKENS: int = int(os.getenv("INTRO_MAX_TOKENS", "512"))
    # Макс. размер контекста модели (токенов); при превышении старые мысли суммаризируются (LM Studio часто 4096)
    CONTEXT_LIMIT: int = int(os.getenv("CONTEXT_LIMIT", "4096"))

    # Штраф за повторения (frequency_penalty): база; в запросе к LLM передаётся 2x (увеличен в 2 раза)
    FREQUENCY_PENALTY: float = float(os.getenv("FREQUENCY_PENALTY", "0.5"))

    # База и отладка
    DBPATH: str = os.getenv("DB_PATH") or os.getenv("DBPATH", "./data/db.sqlite")
    DEBUG: bool = os.getenv("DEBUG", "True").lower() == "true"

    # Первичный промпт для саморазмышлений (по умолчанию; переопределяется в настройках)
    DEFAULT_INTRO_PROMPT: str = os.getenv(
        "DEFAULT_INTRO_PROMPT",
        "Ты — мыслящая система. Кратко поразмышляй о текущем состоянии знаний и приоритетах. Будь лаконичен."
    )

    TAG_SEPARATOR = ","

    # Папка для загружаемых файлов (пища для размышлений)
    UPLOAD_FOLDER: str = os.getenv("UPLOAD_FOLDER", "./uploads")
