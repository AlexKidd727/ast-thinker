# -*- coding: utf-8 -*-
"""Периодический запуск саморазмышления. Один запрос за раз; интервал — пауза между циклами.
В режиме размышлений ответы LLM сохраняются и каждый следующий цикл отправляет предыдущий ответ как новый запрос к LLM."""
import logging
import threading
import time
from datetime import datetime

from app import db
from app.llm import run_introspection, summarize_journal_entries

logger = logging.getLogger(__name__)

# Общий вывод в журнал при обнаружении зацикливания (повтор двух последних мыслей)
LOOP_DETECTED_MESSAGE = (
    "Обнаружено зацикливание (повтор последних мыслей). Обдумывание завершено."
)

# Один рабочий поток (_worker): только он вызывает run_introspection. Один запрос к LLM за раз.
_introspection_lock = threading.Lock()
_scheduler_thread = None  # threading.Thread, единственный поток планировщика
# Время последнего запуска саморазмышления (для сна до следующего цикла с учётом смены интервала в настройках)
_last_introspection_run_time: float = 0.0


def run_introspection_guarded():
    """Один цикл саморазмышления под lock (поток + БД). Возвращает (True, result) если цикл выполнен, (False, None) если уже идёт другой (в т.ч. в другом процессе)."""
    if not _introspection_lock.acquire(blocking=False):
        return False, None
    if not db.try_acquire_introspection_lock():
        db.release_introspection_lock_if_stale(max_age_seconds=300)
        if not db.try_acquire_introspection_lock():
            _introspection_lock.release()
            logger.debug("[scheduler] Introspection lock held by another process, skip.")
            return False, None
    try:
        result = run_introspection()
        return True, result
    finally:
        db.release_introspection_lock()
        _introspection_lock.release()


def _get_interval_seconds() -> int:
    v = db.get_setting("intro_interval", "300")
    try:
        return max(10, int(v))
    except ValueError:
        return 300


def _sleep_chunked(seconds: int, chunk: int = 5) -> None:
    """Сон заданное число секунд по кускам chunk сек, чтобы при сохранении настроек новый интервал подхватывался в следующей итерации."""
    elapsed = 0
    while elapsed < seconds:
        time.sleep(min(chunk, seconds - elapsed))
        elapsed += chunk


def _sleep_until_next_run() -> None:
    """Спать до момента (последний запуск + текущий интервал из БД). Каждые 5 сек перечитываем интервал — смена в настройках применяется без перезапуска."""
    global _last_introspection_run_time
    chunk = 5
    while True:
        interval = _get_interval_seconds()
        deadline = _last_introspection_run_time + interval
        now = time.time()
        if now >= deadline:
            break
        time.sleep(min(chunk, deadline - now))


def _is_thinking_enabled() -> bool:
    """Процесс мышления включён (настройка thinking_enabled в БД)."""
    return db.get_setting("thinking_enabled", "1").strip() in ("1", "true", "yes")


def _get_idle_seconds() -> int:
    """Секунд без сообщений от пользователя, после которых разрешено размышление (idle_seconds в БД)."""
    v = db.get_setting("idle_seconds", "300")
    try:
        return max(0, int(v))
    except ValueError:
        return 300


def _text_similarity(text1: str, text2: str) -> float:
    """Доля совпадающих слов (по множествам) от меньшего текста. 0.0–1.0. Для детекции повторений."""
    import re
    def words(s):
        return set(re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", (s or "").lower()))
    w1, w2 = words(text1), words(text2)
    if not w1 or not w2:
        return 0.0
    common = len(w1 & w2)
    return common / min(len(w1), len(w2))


def _get_repetition_threshold() -> float:
    """Порог совпадения двух последних мыслей (0.0–1.0): при превышении — остановка размышлений."""
    v = db.get_setting("repetition_threshold", "0.7").strip()
    try:
        return max(0.0, min(1.0, float(v)))
    except ValueError:
        return 0.7


def _should_stop_due_to_repetition() -> bool:
    """Проверить два последних intro: если сходство >= порога — вернуть True (нужна остановка)."""
    last_two = db.get_last_intro_thoughts(limit=2)
    if len(last_two) < 2:
        return False
    t1, t2 = (last_two[1].text or "").strip(), (last_two[0].text or "").strip()
    if not t1 or not t2:
        return False
    sim = _text_similarity(t1, t2)
    th = _get_repetition_threshold()
    if sim >= th:
        logger.info("[scheduler] Repetition detected (similarity=%.2f >= %.2f), stopping thinking.", sim, th)
        return True
    return False


def _is_user_idle_long_enough() -> bool:
    """Пользователь не писал в чат дольше idle_seconds — можно переходить в режим размышлений. Если смотрит журнал — не ждём."""
    if db.get_setting("idle_bypass", "0").strip() in ("1", "true", "yes"):
        return True
    last_ts = db.get_last_user_message_time()
    if last_ts is None:
        return True
    idle = _get_idle_seconds()
    if idle <= 0:
        return True
    elapsed = (datetime.utcnow() - last_ts).total_seconds()
    return elapsed >= idle


def _worker():
    """Единственный рабочий поток: пока thinking_enabled — постоянно через установленные интервалы запускает цикл саморазмышления."""
    global _last_introspection_run_time
    logger.info("[scheduler] Worker thread started (single worker), entering main loop.")
    while True:
        try:
            if not _is_thinking_enabled():
                logger.debug("[scheduler] Thinking disabled, sleeping 5 s.")
                time.sleep(5)
                continue
            interval = _get_interval_seconds()
            if not _is_user_idle_long_enough():
                last_ts = db.get_last_user_message_time()
                idle_sec = _get_idle_seconds()
                elapsed = (datetime.utcnow() - last_ts).total_seconds() if last_ts else 0
                logger.info(
                    "[scheduler] Skip: user active (last message %.0f s ago, need %s s idle; idle_bypass=0).",
                    elapsed, idle_sec
                )
                _sleep_chunked(interval)
                continue
            logger.info("[scheduler] User idle or idle_bypass=1, starting introspection.")
            ran, result = run_introspection_guarded()
            if not ran:
                logger.info("[scheduler] Introspection already in progress, skip this tick (interval = delay, not forced call).")
                _sleep_chunked(interval)
                continue
            if ran:
                _last_introspection_run_time = time.time()
            if result:
                preview = (result[:120] + "...") if len(result) > 120 else result
                logger.info("[scheduler] Introspection saved, length=%s. Preview: %s", len(result), preview)
                if _should_stop_due_to_repetition():
                    # Запись в журнал: общий вывод о зацикливании
                    try:
                        db.add_short_term_memory(
                            LOOP_DETECTED_MESSAGE,
                            tags="intro",
                            trigger_text="",
                            model_name="",
                            meta_json="",
                        )
                    except Exception as e:
                        logger.warning("[scheduler] Failed to add loop-detected message: %s", e)
                    # Автообобщение журнала и сохранение итога в долговременную память
                    try:
                        items = db.get_all_short_term_memory()
                        if items:
                            final_summary, first_user_text = summarize_journal_entries(items, chunk_size=10)
                            if final_summary:
                                if first_user_text:
                                    text_to_save = "По запросу: «{}»\n\nИтог: {}".format(
                                        first_user_text.replace("«", '"').replace("»", '"')[:300], final_summary
                                    )
                                else:
                                    text_to_save = "Итог обобщения журнала:\n\n" + final_summary
                                db.add_long_term_memory(text_to_save, tags="journal_summary")
                                logger.info("[scheduler] Auto-summary on repetition: saved to long_term_memory.")
                            else:
                                logger.warning("[scheduler] Auto-summary on repetition: LLM returned empty.")
                        else:
                            logger.debug("[scheduler] Auto-summary skipped: journal empty.")
                    except Exception as e:
                        logger.exception("[scheduler] Auto-summary on repetition failed: %s", e)
                    db.set_setting("thinking_enabled", "0")
                    logger.info("[scheduler] Thinking disabled due to repetition.")
            else:
                logger.warning("[scheduler] Introspection returned empty or failed.")
            interval = _get_interval_seconds()
            logger.info("[scheduler] Next check in %s s (интервал перечитывается при сохранении настроек).", interval)
            _sleep_until_next_run()
        except Exception as e:
            logger.exception("[scheduler] Introspection worker error: %s", e)
            interval = _get_interval_seconds()
            _sleep_chunked(min(interval, 60))


def start_scheduler():
    """Запуск единственного фонового потока для саморазмышлений. Пока включён — циклы идут через intro_interval сек."""
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        logger.warning("[scheduler] Worker already running, not starting second thread.")
        return
    _scheduler_thread = threading.Thread(target=_worker, daemon=True)
    _scheduler_thread.start()
    logger.info("[scheduler] Single introspection worker started (one thread only).")
