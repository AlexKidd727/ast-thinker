# -*- coding: utf-8 -*-
"""Вызовы LLM по HTTP: OpenAI-совместимый API (GET /v1/models, POST /v1/chat/completions). Без openai/pydantic — обход ошибки by_alias."""
import json
import logging
import urllib.request
from typing import List, Dict, Optional

from app.config import Config
from app import db as db_module

logger = logging.getLogger(__name__)

# Последняя ошибка list_models (для API, чтобы показать в настройках)
_last_list_models_error: Optional[str] = None

# Максимальный контекст модели по умолчанию (токенов), если API не возвращает — для суммаризации используем весь доступный объём
_DEFAULT_MODEL_CONTEXT_MAX = 128000

# Знаки завершения предложения: если последний абзац не заканчивается ими — считаем неполным и убираем
_SENTENCE_END_CHARS = (".", "!", "?")

# Тег размышлений модели (<think>...</think>): вырезаем весь блок, оставляем только ответ пользователю
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def _strip_think_blocks(text: str) -> str:
    """Удалить из ответа блоки <think>...</think> — внутренние «размышления» модели не показываем пользователю."""
    if not text or _THINK_OPEN not in text:
        return text
    out_parts = []
    rest = text
    while rest:
        idx_open = rest.find(_THINK_OPEN)
        if idx_open == -1:
            out_parts.append(rest)
            break
        out_parts.append(rest[:idx_open])
        idx_close = rest.find(_THINK_CLOSE, idx_open + len(_THINK_OPEN))
        if idx_close == -1:
            # незакрытый блок — обрезаем до конца
            rest = ""
            break
        rest = rest[idx_close + len(_THINK_CLOSE) :].lstrip("\n\r")
    result = "".join(out_parts).strip()
    if result != text.strip():
        logger.debug("[llm] Stripped <think> block(s) from response.")
    return result


def _extract_think_content(text: str) -> str:
    """Извлечь содержимое первого блока <think>...</think> или <think>... (если </think> нет).
    Используется, когда после _strip_think_blocks ответ пустой — у моделей вроде GLM-4.6v весь вывод идёт в <think>."""
    if not text or _THINK_OPEN not in text:
        return ""
    idx = text.find(_THINK_OPEN)
    start = idx + len(_THINK_OPEN)
    idx_close = text.find(_THINK_CLOSE, start)
    end = idx_close if idx_close != -1 else len(text)
    return text[start:end].strip()


def _visible_response(raw: str) -> str:
    """Итоговый текст ответа для показа/сохранения: убираем <think>-блоки, но если после этого пусто
    (или после отбрасывания неполного абзаца пусто) — берём содержимое <think> как ответ (qwen3-4b, GLM-4.6v и др.)."""
    stripped = _strip_think_blocks(raw)
    if (stripped or "").strip():
        candidate = _drop_incomplete_last_paragraph(stripped)
        if (candidate or "").strip():
            return candidate
    think_content = _extract_think_content(raw)
    if think_content:
        logger.debug("[llm] Using think block content as visible response (%s chars).", len(think_content))
        out = _drop_incomplete_last_paragraph(think_content)
        return out if (out or "").strip() else think_content
    return ""


def _drop_incomplete_last_paragraph(text: str) -> str:
    """Если последний абзац не заканчивается на . ! ? — считаем его неполным (обрезка по лимиту), убираем из ответа.
    Исключение: если перед последним абзацем стоит двоеточие (например «Список:»), хвост не обрезаем."""
    if not text or not text.strip():
        return text
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not parts:
        return text.strip()
    last = parts[-1]
    if last and last[-1] not in _SENTENCE_END_CHARS:
        rest = "\n\n".join(parts[:-1]).strip()
        if rest.endswith(":"):
            return text.strip()
        parts = parts[:-1]
        result = "\n\n".join(parts).strip()
        logger.debug("[llm] Dropped incomplete last paragraph (%s chars).", len(last))
        return result
    return text.strip()


def _get_llm_provider() -> str:
    """Провайдер LLM: lm_studio или openrouter (из настроек БД)."""
    v = (db_module.get_setting("llm_provider", "lm_studio") or "lm_studio").strip().lower()
    return v if v in ("lm_studio", "openrouter") else "lm_studio"


def _get_openrouter_api_key() -> str:
    """Токен OpenRouter: из настроек БД, иначе из Config (.env)."""
    v = (db_module.get_setting("openrouter_api_key", "") or "").strip()
    if v:
        return v
    return (Config.OPENROUTER_API_KEY or "").strip()


def _base_url() -> str:
    """Базовый URL API: для OpenRouter — openrouter.ai/api/v1, иначе LM Studio (например http://host.docker.internal:1234/v1)."""
    if _get_llm_provider() == "openrouter":
        return "https://openrouter.ai/api/v1"
    return Config.API_BASE_URL_V1.rstrip("/")


def _api_key_for_request() -> str:
    """Ключ/токен для заголовка Authorization: Bearer (в зависимости от провайдера)."""
    if _get_llm_provider() == "openrouter":
        key = _get_openrouter_api_key()
        return key if key else "dummy"
    return Config.API_KEY or "dummy"


def _sort_model_ids(ids: List[str]) -> List[str]:
    """Сортировка id моделей: сначала с :free в имени, затем без :free; внутри каждой группы — по алфавиту."""
    if not ids:
        return []
    free = sorted([i for i in ids if ":free" in (i or "")])
    rest = sorted([i for i in ids if ":free" not in (i or "")])
    return free + rest


def _get_intro_interval() -> int:
    v = db_module.get_setting("intro_interval", str(Config.INTRO_INTERVAL))
    try:
        return int(v)
    except ValueError:
        return Config.INTRO_INTERVAL


def _get_intro_max_tokens() -> int:
    v = db_module.get_setting("intro_max_tokens", str(Config.INTRO_MAX_TOKENS))
    try:
        return int(v)
    except ValueError:
        return Config.INTRO_MAX_TOKENS


def _get_chat_max_tokens() -> int:
    """Лимит токенов для ответа в чате (из настроек или Config). При малом значении ответы обрезаются по лимиту API."""
    v = db_module.get_setting("chat_max_tokens", str(Config.MAX_TOKENS))
    try:
        return max(64, int(v))
    except ValueError:
        return Config.MAX_TOKENS


def _get_intro_prompt() -> str:
    return db_module.get_setting("intro_prompt", Config.DEFAULT_INTRO_PROMPT)


def _get_temperature() -> float:
    """Температура ответов из настроек (0..2)."""
    v = db_module.get_setting("temperature", "0.7")
    try:
        t = float(v)
        return max(0.0, min(2.0, t))
    except ValueError:
        return 0.7


def _estimate_tokens(text: str) -> int:
    """Грубая оценка числа токенов (~4 символа на токен для смешанного текста)."""
    if not text:
        return 0
    return max(0, len(text) // 4)


def _get_intro_context_limit() -> int:
    """Макс. токенов на промпт саморазмышления: контекст модели минус запас на ответ."""
    limit = Config.CONTEXT_LIMIT
    v = (db_module.get_setting("context_limit", "") or "").strip()
    if v.isdigit():
        limit = int(v)
    reserve = _get_intro_max_tokens()
    return max(512, limit - reserve)


def _summarize_intro_context(old_thoughts: List[str], chat_lines: List[str]) -> str:
    """Суммаризировать старые мысли и диалог в 2–4 предложения для контекста саморазмышления."""
    parts = []
    if old_thoughts:
        parts.append("Мысли:\n" + "\n".join(f"- {t[:400]}" for t in old_thoughts[:30]))
    if chat_lines:
        parts.append("Диалог:\n" + "\n".join(chat_lines[-20:]))
    if not parts:
        return ""
    block = "\n\n".join(parts)[:6000]
    messages = [{"role": "user", "content": "Кратко суммаризируй в 2–4 предложениях для контекста саморазмышления (темы, выводы, без обращений):\n\n" + block}]
    out = call_llm(messages, max_tokens=250, temperature=0.3)
    return (out or "").strip() or ""


def _build_messages_for_intro() -> List[Dict[str, str]]:
    """
    Саморазмышление: в режиме размышлений ответы LLM сохраняются и отправляются
    как новые запросы (вопрос к себе). При превышении лимита контекста старые мысли и диалог суммаризируются.
    """
    recent = db_module.get_recent_short_term_memory(limit=24)
    last_thought = None
    for m in recent:
        if (m.tags or "").strip() == "intro":
            last_thought = m
            break
    if last_thought:
        previous_text = last_thought.text
        rest = list(reversed([m for m in recent if m.id != last_thought.id]))
        context_lines = [m.text for m in rest]
    else:
        previous_text = None
        context_lines = [m.text for m in list(reversed(recent))] if recent else []

    if previous_text and previous_text.strip():
        user_content = previous_text.strip()
    else:
        # Первый запуск: использовать текст пользователя (Think now или из чата), если есть — тогда в журнале будет ответ на него
        seed_text = None
        for m in recent:
            t = (m.tags or "").strip()
            if t == "user_seed" and (m.text or "").strip():
                seed_text = (m.text or "").strip()
                break
        if not seed_text:
            for m in recent:
                t = (m.tags or "").strip()
                if t == "chat" and (m.text or "").strip():
                    seed_text = (m.text or "").strip()
                    break
        user_content = seed_text or _get_intro_prompt()

    anchor = (db_module.get_setting("intro_anchor", "") or "").strip()
    if not anchor:
        anchor = user_content[:500] if user_content else ""
        if anchor:
            db_module.set_setting("intro_anchor", anchor)

    anchor_block = ""
    if anchor:
        anchor_block = (
            "ПЕРВОНАЧАЛЬНЫЙ ВОПРОС (вся цепочка мыслей должна быть связана с ним; не уходи к посторонним темам):\n\n"
            "{}\n\n".format(anchor)
        )

    system_base = (
        "Ты — мыслящая система. САМОРАЗМЫШЛЕНИЕ: ты «спрашиваешь себя» (посыл приходит отдельным сообщением) "
        "и твой ответ ниже — это твоя новая мысль, её сохранят в журнал. Не обращайся к пользователю; это диалог с самим собой.\n\n"
        "ЗАПРЕЩЕНО: обращение к пользователю, «Предлагаю», примеры реплик в кавычках для чата; повторять или перефразировать посыл (вопрос к себе). "
        "Отвечай своей мыслью, выводом — не копируй вопрос. Разрешено: наблюдения, приоритеты, сомнения, идеи — без готовых фраз для чата.\n\n"
    )
    if anchor_block:
        system_base += anchor_block
        system_base += (
            "Правила: каждое следующее сообщение должно развивать или уточнять первоначальный вопрос; "
            "можно смотреть на него с разных сторон (ограничения, сомнения, идеи, приоритеты), но не уходить от темы. Лаконично.\n\n"
        )
    else:
        system_base += (
            "КРИТИЧНО: не зацикливайся на одной узкой теме. Приветствия («привет» и т.п.), отдельные слова или мелочи диалога НЕ должны становиться главной темой размышлений. "
            "Если посыл в основном про одно и то же — в ответе смени тему: подумай о своих ограничениях, сомнениях, идеях, приоритетах, памяти.\n\n"
            "Правила: чередуй темы; лаконично; каждый ответ — по возможности новая грань.\n\n"
        )

    chat_msgs = db_module.get_recent_chat_messages(limit=20)
    chat_lines = []
    if chat_msgs:
        chat_lines = [("Пользователь" if m.role == "user" else "Ассистент") + ": " + (m.text or "") for m in chat_msgs]

    max_tokens = _get_intro_context_limit()
    keep_recent = 5
    context_block = ""
    full_context = "\n".join(f"- {t}" for t in context_lines) if context_lines else ""
    full_system = system_base
    if full_context:
        full_system += "\n\nПредыдущие мысли (контекст, не дублируй):\n" + full_context
    if chat_lines:
        full_system += "\n\nНедавний диалог (только общий контекст):\n" + "\n".join(chat_lines)
    total_tokens = _estimate_tokens(full_system) + _estimate_tokens(user_content) + 100

    if total_tokens <= max_tokens:
        if full_context:
            context_block = "\n\nПредыдущие мысли (контекст, не дублируй):\n" + full_context
        if chat_lines:
            context_block += "\n\nНедавний диалог (только общий контекст):\n" + "\n".join(chat_lines)
    else:
        old_thoughts = context_lines[:-keep_recent] if len(context_lines) > keep_recent else []
        recent_thoughts = context_lines[-keep_recent:] if len(context_lines) > keep_recent else context_lines
        summary = _summarize_intro_context(old_thoughts, chat_lines)
        if summary:
            logger.info("[intro] Context exceeded limit (%s tokens), using summary (%s chars).", total_tokens, len(summary))
            context_block = "\n\nКраткое содержание более ранних мыслей и диалога:\n" + summary
        if recent_thoughts:
            context_block += "\n\nПоследние мысли (контекст, не дублируй):\n" + "\n".join(f"- {t}" for t in recent_thoughts)
        elif chat_lines and not summary:
            context_block += "\n\nНедавний диалог:\n" + "\n".join(chat_lines[-10:])

    system = system_base + context_block
    return [
        {"role": "user", "content": "Инструкция для саморазмышления:\n\n" + system + "\n\n---\n\nВопрос к себе:\n\n" + user_content},
    ]


def _build_messages_for_chat(user_text: str, attachment_texts: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """Ответ в чат: реакция на сообщения пользователя; размышления — только контекст, не инструкции."""
    system_parts = [
        "Ты — мыслящий помощник. Отвечай на реплики пользователя естественно, по контексту диалога. "
        "Не повторяй вопрос пользователя — сразу давай ответ по существу. "
        "Ниже — твои недавние размышления (наблюдения, приоритеты). Они только контекст: не копируй из них готовые фразы или вопросы, не «выполняй указания» из мыслей — просто реагируй на сообщения и веди диалог."
    ]
    chat_summary = (db_module.get_setting("chat_summary", "") or "").strip()
    if chat_summary:
        system_parts.append("\nКраткое содержание предыдущего диалога (контекст):\n" + chat_summary)
    short = db_module.get_recent_short_term_memory(limit=10)
    if short:
        system_parts.append("\nНедавние размышления (контекст для тона и приоритетов, не скрипт ответа):")
        for m in reversed(short):
            system_parts.append(f"- {m.text}")
    long_list = db_module.get_long_term_memory_list(limit=15)
    if long_list:
        system_parts.append("\nДолгосрочная память (важное):")
        for m in long_list[:15]:
            system_parts.append(f"- {m.text}")
    system = "\n".join(system_parts)

    messages = [{"role": "system", "content": system}]
    chat_msgs = db_module.get_recent_chat_messages(limit=30)
    current = user_text
    if attachment_texts:
        current += "\n\nТекст из приложенных файлов:\n" + "\n---\n".join(attachment_texts)
    # Не дублировать последнее сообщение: оно уже в БД (api добавил перед вызовом), потом мы добавляли current ещё раз
    last_is_same_user = (
        chat_msgs
        and chat_msgs[-1].role == "user"
        and (chat_msgs[-1].text or "").strip() == (user_text or "").strip()
    )
    if last_is_same_user and len(chat_msgs) > 0:
        chat_msgs = chat_msgs[:-1]
    for msg in chat_msgs:
        role = "user" if msg.role == "user" else "assistant"
        content = msg.text
        if msg.attachments:
            for a in msg.attachments:
                if a.content_text:
                    content += f"\n[Вложение: {a.filename}]\n{a.content_text}"
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": current})
    return messages


def list_models() -> List[str]:
    """Список id моделей: LM Studio — GET /v1/models; OpenRouter — GET openrouter.ai/api/v1/models. Сортировка: :free сначала, затем по алфавиту."""
    global _last_list_models_error
    provider = _get_llm_provider()
    if provider == "openrouter":
        return _list_models_openrouter()
    return _list_models_lm_studio()


def _list_models_lm_studio() -> List[str]:
    """Список моделей LM Studio (GET /v1/models). Без сортировки :free — у локальных моделей обычно нет такого суффикса."""
    global _last_list_models_error
    base = Config.API_BASE_URL_V1.rstrip("/")
    url = base + "/models"
    try:
        req = urllib.request.Request(url)
        req.add_header("Authorization", "Bearer " + (Config.API_KEY or "dummy"))
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        items = data.get("data") if isinstance(data, dict) else []
        ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
        ids = _sort_model_ids(ids)
        logger.info("[llm] list_models (LM Studio): %s models from %s", len(ids), base)
        _last_list_models_error = None
        return ids
    except Exception as e:
        _last_list_models_error = str(e)
        logger.warning("[llm] Could not list models LM Studio (url=%s): %s", url, e, exc_info=True)
        return []


def _list_models_openrouter() -> List[str]:
    """Список моделей OpenRouter (GET https://openrouter.ai/api/v1/models). Сортировка: :free сначала, затем по алфавиту."""
    global _last_list_models_error
    base = "https://openrouter.ai/api/v1"
    url = base + "/models"
    key = _get_openrouter_api_key()
    if not key or key == "dummy":
        _last_list_models_error = "Укажите токен OpenRouter в настройках или OPENROUTER_API_KEY в .env"
        logger.warning("[llm] OpenRouter: no API key")
        return []
    try:
        req = urllib.request.Request(url)
        req.add_header("Authorization", "Bearer " + key)
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        # OpenRouter: может быть data[] или верхнеуровневый список
        items = data.get("data") if isinstance(data, dict) else []
        if not items and isinstance(data, list):
            items = data
        ids = []
        for m in items:
            if isinstance(m, dict) and m.get("id"):
                ids.append(m["id"])
            elif isinstance(m, str):
                ids.append(m)
        ids = _sort_model_ids(ids)
        logger.info("[llm] list_models (OpenRouter): %s models", len(ids))
        _last_list_models_error = None
        return ids
    except Exception as e:
        _last_list_models_error = str(e)
        logger.warning("[llm] Could not list models OpenRouter (url=%s): %s", url, e, exc_info=True)
        return []


def list_models_openrouter_with_key(api_key: str) -> List[str]:
    """Загрузка списка моделей OpenRouter с указанным токеном (для запроса из настроек до сохранения). Сортировка: :free сначала, по алфавиту."""
    global _last_list_models_error
    key = (api_key or "").strip()
    if not key:
        _last_list_models_error = "Токен OpenRouter не указан"
        return []
    base = "https://openrouter.ai/api/v1"
    url = base + "/models"
    try:
        req = urllib.request.Request(url)
        req.add_header("Authorization", "Bearer " + key)
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        items = data.get("data") if isinstance(data, dict) else []
        if not items and isinstance(data, list):
            items = data
        ids = []
        for m in items:
            if isinstance(m, dict) and m.get("id"):
                ids.append(m["id"])
            elif isinstance(m, str):
                ids.append(m)
        ids = _sort_model_ids(ids)
        logger.info("[llm] list_models_openrouter_with_key: %s models", len(ids))
        _last_list_models_error = None
        return ids
    except Exception as e:
        _last_list_models_error = str(e)
        logger.warning("[llm] Could not list models OpenRouter (url=%s): %s", url, e, exc_info=True)
        return []


def get_current_meta_dict() -> Dict[str, str]:
    """Текущие метаданные из настроек (модель, контекст и т.п.) для сохранения с сообщением/записью."""
    ctx_limit = Config.CONTEXT_LIMIT
    v = (db_module.get_setting("context_limit", "") or "").strip()
    if v.isdigit():
        ctx_limit = int(v)
    return {
        "model": (db_module.get_setting("model_name", "") or "").strip() or "(авто)",
        "context_limit": ctx_limit,
        "chat_max_tokens": (db_module.get_setting("chat_max_tokens", str(Config.MAX_TOKENS)) or "").strip(),
        "intro_max_tokens": (db_module.get_setting("intro_max_tokens", str(Config.INTRO_MAX_TOKENS)) or "").strip(),
        "temperature": (db_module.get_setting("temperature", "0.7") or "").strip(),
        "repetition_threshold": (db_module.get_setting("repetition_threshold", "0.7") or "").strip(),
        "summarize_every_n": (db_module.get_setting("summarize_every_n", "20") or "").strip(),
    }


def get_model_name() -> str:
    """Имя модели: из настроек (БД), затем из конфига, затем первый из GET /v1/models."""
    saved = (db_module.get_setting("model_name", "") or "").strip()
    if saved:
        return saved
    if Config.MODEL_NAME:
        return Config.MODEL_NAME
    ids = list_models()
    if ids:
        return ids[0]
    return "local"


def _model_thinking_disabled() -> bool:
    """Отключить режим размышлений модели (reasoning/thinking): все токены идут в ответ, не во внутренние размышления."""
    v = (db_module.get_setting("model_thinking_disabled", "0") or "").strip().lower()
    return v in ("1", "true", "yes")


def _model_thinking_max_tokens() -> int:
    """Лимит токенов на размышления модели до ответа (0 = не ограничивать). Передаётся в API как max_reasoning_tokens."""
    v = (db_module.get_setting("model_thinking_max_tokens", "0") or "").strip()
    try:
        return max(0, int(v))
    except ValueError:
        return 0


def call_llm(messages: List[Dict[str, str]], max_tokens: Optional[int] = None, temperature: Optional[float] = None) -> str:
    """Запрос к API POST /v1/chat/completions по HTTP. Возвращает текст ответа или пустую строку при ошибке. Без openai/pydantic."""
    max_tokens = max_tokens or Config.MAX_TOKENS
    temp = temperature if temperature is not None else _get_temperature()
    frequency_penalty = min(2.0, max(-2.0, 2 * Config.FREQUENCY_PENALTY))
    model = get_model_name()
    base = _base_url()
    url = base + "/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temp,
        "frequency_penalty": frequency_penalty,
    }
    if _model_thinking_disabled():
        body["reasoning"] = {"effort": "none"}
        logger.debug("[llm] model_thinking_disabled=1: reasoning.effort=none")
    thinking_limit = _model_thinking_max_tokens()
    if thinking_limit > 0:
        body["max_reasoning_tokens"] = thinking_limit
        logger.debug("[llm] model_thinking_max_tokens=%s", thinking_limit)
    try:
        logger.debug("[llm] POST %s model=%s max_tokens=%s", url, model, max_tokens)
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + _api_key_for_request())
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        choices = data.get("choices") if isinstance(data, dict) else []
        if choices and isinstance(choices[0], dict):
            first = choices[0]
            finish_reason = first.get("finish_reason") if isinstance(first.get("finish_reason"), str) else None
            if finish_reason == "length":
                logger.warning("[llm] Ответ обрезан по лимиту max_tokens=%s (finish_reason=length). Увеличьте лимит токенов для чата в настройках.", max_tokens)
            msg = first.get("message")
            if isinstance(msg, dict):
                content = msg.get("content")
                raw_out = (content or "").strip()
                out = _visible_response(raw_out)
                logger.debug("[llm] Response %s chars.", len(out))
                return out
        logger.warning("[llm] No choices in response.")
        return ""
    except Exception as e:
        logger.exception("[llm] Request failed: %s", e)
        return ""


def get_model_context_limit() -> int:
    """Максимальный размер контекста текущей модели в токенах (из API, если есть, иначе большой fallback). Для суммаризации — не из настроек приложения."""
    model_id = get_model_name()
    if not model_id or model_id == "local":
        return _DEFAULT_MODEL_CONTEXT_MAX
    provider = _get_llm_provider()
    try:
        if provider == "openrouter":
            url = "https://openrouter.ai/api/v1/models"
            req = urllib.request.Request(url)
            key = _get_openrouter_api_key()
            if key and key != "dummy":
                req.add_header("Authorization", "Bearer " + key)
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
            items = data.get("data") if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for m in items:
                if isinstance(m, dict) and (m.get("id") == model_id or (m.get("id") or "").startswith(model_id)):
                    ctx = m.get("context_length") or m.get("context_length_limit")
                    if ctx is not None:
                        try:
                            n = int(float(ctx))
                            if n > 0:
                                logger.debug("[llm] Model context_length from OpenRouter: %s", n)
                                return n
                        except (TypeError, ValueError):
                            pass
                    break
        else:
            base = Config.API_BASE_URL_V1.rstrip("/")
            req = urllib.request.Request(base + "/models")
            req.add_header("Authorization", "Bearer " + (Config.API_KEY or "dummy"))
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
            items = data.get("data") if isinstance(data, dict) else []
            for m in items:
                if isinstance(m, dict) and (m.get("id") == model_id or (m.get("id") or "").startswith(model_id)):
                    ctx = m.get("context_length") or m.get("context_length_limit")
                    if ctx is not None:
                        try:
                            n = int(float(ctx))
                            if n > 0:
                                logger.debug("[llm] Model context_length from API: %s", n)
                                return n
                        except (TypeError, ValueError):
                            pass
                    break
    except Exception as e:
        logger.debug("[llm] Could not get model context_length: %s, using default.", e)
    return _DEFAULT_MODEL_CONTEXT_MAX


def summarize_chat_messages(chat_messages) -> str:
    """Суммаризация списка сообщений чата. Использует максимальный контекст модели (из API), не настройки приложения."""
    logger.info("[llm] summarize_chat_messages: input count=%s.", len(chat_messages) if chat_messages else 0)
    if not chat_messages:
        return ""
    lines = []
    for m in chat_messages:
        who = "Пользователь" if getattr(m, "role", "") == "user" else "Ассистент"
        text = (getattr(m, "text", None) or "").strip()
        if text:
            lines.append(f"{who}: {text}")
    if not lines:
        logger.warning("[llm] summarize_chat_messages: no non-empty lines, return empty.")
        return ""
    ctx_tokens = get_model_context_limit()
    reserve_tokens = 2048
    chars_per_token = 4
    max_input_chars = max(8000, (ctx_tokens - reserve_tokens) * chars_per_token)
    summary_max_tokens = min(2048, max(512, ctx_tokens // 4))
    full_block = "\n".join(lines)
    block = full_block[:max_input_chars]
    if len(full_block) > len(block):
        logger.info("[llm] summarize_chat_messages: dialog truncated to %s chars (model context %s tokens).", len(block), ctx_tokens)
    logger.info("[llm] summarize_chat_messages: block length=%s chars, max_tokens=%s (model context %s).", len(block), summary_max_tokens, ctx_tokens)
    messages = [{"role": "user", "content": "Кратко суммаризируй этот диалог:\n\n" + block}]
    out = call_llm(messages, max_tokens=summary_max_tokens, temperature=0.3)
    result = (out or "").strip()
    logger.info("[llm] summarize_chat_messages: LLM returned %s chars.", len(result))
    return result


def summarize_journal_entries(entries: list, chunk_size: int = 10, progress_callback=None) -> tuple:
    """Обобщение журнала мыслей: разбить на чанки по N записей, суммаризировать каждый, затем обобщить все в один итог.
    progress_callback(phase, current, total) вызывается для отображения прогресса: phase in ("chunk", "final")."""
    if not entries:
        return ("", "")
    # Первоначальный запрос: первая запись с тегом user_seed или первая запись по порядку
    first_user_text = ""
    for m in entries:
        tags = (getattr(m, "tags", None) or "").strip()
        if "user_seed" in tags:
            first_user_text = (getattr(m, "text", None) or "").strip()[:500]
            break
    if not first_user_text and entries:
        first_user_text = (getattr(entries[0], "text", None) or "").strip()[:500]

    ctx_tokens = get_model_context_limit()
    reserve = 2048
    max_chars_block = max(4000, (ctx_tokens - reserve) * 4)
    max_tok_out = min(1024, max(256, ctx_tokens // 8))

    chunk_summaries = []
    starts = list(range(0, len(entries), chunk_size))
    total_chunks = sum(
        1 for s in starts
        if any((getattr(m, "text", None) or "").strip() for m in entries[s : s + chunk_size])
    )
    chunk_index = 0
    for start in starts:
        chunk = entries[start : start + chunk_size]
        block_lines = []
        for i, m in enumerate(chunk):
            text = (getattr(m, "text", None) or "").strip()
            if text:
                block_lines.append("[{}] {}".format(start + i + 1, text))
        if not block_lines:
            continue
        if progress_callback:
            progress_callback("chunk", chunk_index + 1, total_chunks)
        block = "\n\n".join(block_lines)[:max_chars_block]
        prompt = (
            "Тезисно обобщи этот блок записей журнала размышлений. "
            "Только суть, без вводных слов. Краткие пункты или 2–3 предложения.\n\n" + block
        )
        out = call_llm([{"role": "user", "content": prompt}], max_tokens=max_tok_out, temperature=0.2)
        s = (out or "").strip()
        if s:
            chunk_summaries.append(s)
        chunk_index += 1
        logger.info("[llm] summarize_journal_entries: chunk %s–%s -> %s chars.", start + 1, start + len(chunk), len(s))

    if not chunk_summaries:
        return ("", first_user_text)

    if progress_callback:
        progress_callback("final", 1, 1)
    # Финальное обобщение всех чанков
    combined = "\n\n---\n\n".join(chunk_summaries)
    combined = combined[:max_chars_block]
    prompt_final = (
        "По следующим обобщениям блоков журнала размышлений сделай единый итоговый вывод. "
        "Максимально ёмко и тезисно, без лишних слов, максимум информации. Только вывод, без предисловий.\n\n" + combined
    )
    final = call_llm([{"role": "user", "content": prompt_final}], max_tokens=max_tok_out, temperature=0.2)
    result = (final or "").strip()
    logger.info("[llm] summarize_journal_entries: final summary %s chars.", len(result))
    return (result, first_user_text)


def extract_main_thought(summary: str) -> str:
    """Выделить главную мысль из краткого содержания диалога (одно предложение). Для сохранения в долговременную память."""
    logger.info("[llm] extract_main_thought: input summary length=%s.", len(summary) if summary else 0)
    if not summary or not summary.strip():
        return ""
    prompt = (
        "Выдели главную мысль из этого краткого содержания диалога в одном предложении. "
        "Только одно предложение, без кавычек и предисловий вроде «Главная мысль:».\n\n"
        + summary.strip()
    )
    messages = [{"role": "user", "content": prompt}]
    logger.info("[llm] extract_main_thought: calling LLM (max_tokens=80).")
    out = call_llm(messages, max_tokens=80, temperature=0.2)
    out = (out or "").strip()
    out = _drop_incomplete_last_paragraph(out)
    logger.info("[llm] extract_main_thought: LLM returned %s chars.", len(out))
    return out


def run_introspection() -> Optional[str]:
    """
    Один цикл саморазмышления: берём последний ответ LLM из БД -> отправляем в LLM как новый запрос ->
    получаем новый ответ -> сохраняем его; следующий цикл отправит этот ответ как следующий запрос.
    """
    logger.info("[intro] Building context for self-reflection...")
    messages = _build_messages_for_intro()  # внутри: последний ответ LLM уже подставлен как «вопрос к себе»
    max_tok = _get_intro_max_tokens()
    model = get_model_name()
    logger.info("[intro] Calling LLM (model=%s, max_tokens=%s).", model, max_tok)
    text = call_llm(messages, max_tokens=max_tok)
    if not text:
        logger.warning("[intro] LLM returned empty response.")
        return None
    logger.info("[intro] Response received, %s chars.", len(text))
    last = db_module.get_recent_short_term_memory(limit=1)
    if last and (last[0].text or "").strip() == (text or "").strip():
        logger.info("[intro] Skip save: same as last thought (duplicate).")
        return text
    meta_dict = get_current_meta_dict()
    mem_id = db_module.add_short_term_memory(
        text,
        tags="intro",
        model_name=model,
        meta_json=json.dumps(meta_dict),
    )  # этот ответ станет следующим запросом к LLM
    logger.info("[intro] Saved to short-term memory, id=%s (will be sent as next request).", mem_id)
    return text


def reply_to_user(user_text: str, attachment_texts: Optional[List[str]] = None) -> str:
    """Ответ пользователю с учётом памяти и вложений. Сохраняем user + assistant в БД снаружи (api). Лимит токенов — из настроек (chat_max_tokens)."""
    messages = _build_messages_for_chat(user_text, attachment_texts)
    return call_llm(messages, max_tokens=_get_chat_max_tokens())
