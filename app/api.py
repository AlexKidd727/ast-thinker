# -*- coding: utf-8 -*-
"""Flask: страницы (чат, журналы, настройки) и API для сообщений и загрузки файлов."""
import json
import logging
import os
import threading
from pathlib import Path
from datetime import datetime as dt
from queue import Queue
from flask import Flask, request, jsonify, render_template, send_from_directory, Response, stream_with_context
from werkzeug.utils import secure_filename

from app.config import Config

logger = logging.getLogger(__name__)
from app import db as db_module
from app import llm as llm_module
from app.llm import reply_to_user, list_models, list_models_openrouter_with_key, summarize_chat_messages, extract_main_thought, summarize_journal_entries
from app.scheduler import run_introspection_guarded, start_scheduler

# Создаём папку для загрузок
Path(Config.UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {"txt", "md", "json", "csv", "log", "py", "html", "css", "js"}
MAX_CONTENT_LENGTH = 4 * 1024 * 1024  # 4 MB


def allowed_file(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ALLOWED_EXTENSIONS


def read_file_safe(path: str, max_chars: int = 50000) -> str:
    """Читаем текст из файла с лимитом символов для контекста LLM."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(max_chars)
    except Exception:
        return ""


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    app.config["UPLOAD_FOLDER"] = Config.UPLOAD_FOLDER

    # Таблицы БД (в т.ч. thought_archive) создаются при первом запуске; при gunicorn/flask run main.py не вызывается
    try:
        db_module.init_db()
    except Exception as e:
        logger.exception("[api] init_db failed (app may still run): %s", e)
    # Планировщик саморазмышлений: запускаем при создании приложения
    try:
        start_scheduler()
    except Exception as e:
        logger.exception("[api] start_scheduler failed: %s", e)

    # ---------- Страницы ----------
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/chat")
    def chat_page():
        return render_template("chat.html")

    @app.route("/thoughts")
    def thoughts_page():
        db_module.set_setting("idle_bypass", "1")
        return render_template("thoughts.html")

    @app.route("/memory")
    def memory_page():
        return render_template("memory.html")

    @app.route("/history")
    def history_page():
        return render_template("history.html")

    @app.route("/settings")
    def settings_page():
        return render_template("settings.html")

    # ---------- API: чат ----------
    @app.route("/api/chat/messages", methods=["GET"])
    def get_chat_messages():
        """Список сообщений с пагинацией (page, per_page), строка о суммаризации и метаданные."""
        total_count = db_module.get_chat_message_count()
        page = max(1, int(request.args.get("page", 1)))
        per_page = max(1, min(200, int(request.args.get("per_page", 50))))
        total_pages = max(1, (total_count + per_page - 1) // per_page) if total_count else 1
        offset = (page - 1) * per_page
        messages = db_module.get_chat_messages_paged(offset=offset, limit=per_page)
        raw = (db_module.get_setting("summarize_every_n", "20") or "").strip()
        n_val = int(raw) if raw.isdigit() else 20
        raw_counter = (db_module.get_setting("messages_since_summary", "0") or "").strip()
        counter = int(raw_counter) if raw_counter.isdigit() else 0
        if n_val <= 0:
            summary_info = "Сообщений: {}. Суммаризация выключена.".format(total_count)
        else:
            next_in = max(0, n_val - counter)
            summary_info = "Сообщений: {}. Суммаризация каждые {} (до следующей: {} сообщ.).".format(total_count, n_val, next_in)
        out = []
        for m in messages:
            atts = [{"filename": a.filename, "content_text": (a.content_text or "")[:500]} for a in m.attachments]
            meta = None
            if getattr(m, "meta_json", None) and (m.meta_json or "").strip():
                try:
                    meta = json.loads(m.meta_json)
                except (TypeError, ValueError):
                    pass
            if meta is None:
                meta = _build_meta()  # старые записи без meta — подставляем текущие
            out.append({
                "id": m.id,
                "role": m.role,
                "text": m.text,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "attachments": atts,
                "model": (getattr(m, "model_name", None) or "").strip() or meta.get("model") or "(авто)",
                "meta": meta,
            })
        return jsonify({
            "messages": out,
            "total": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "summary_info": summary_info,
            "messages_since_summary": counter,
            "summarize_every_n": n_val,
            "meta": _build_meta(),
        })

    @app.route("/api/chat/messages", methods=["DELETE"])
    def delete_chat_messages():
        """Очистка чата: удалить все сообщения, сбросить суммаризацию и счётчик сообщений после суммаризации."""
        count = db_module.clear_chat_messages()
        db_module.set_setting("chat_summary", "")
        db_module.set_setting("messages_since_summary", "0")
        return jsonify({"ok": True, "deleted": count})

    @app.route("/api/chat/send", methods=["POST"])
    def send_message():
        """Текст сообщения + опционально файлы. Сохраняем user message, вложения, вызываем LLM, сохраняем assistant."""
        text = (request.form.get("text") or "").strip()
        if not text and not request.files:
            return jsonify({"error": "Текст или файл обязателен"}), 400

        user_content = text or "Приложен файл(ы) для размышлений."
        db_module.set_setting("idle_bypass", "0")
        msg_id = db_module.add_chat_message("user", user_content)
        attachment_texts = []

        files_to_process = request.files.getlist("files") if "files" in request.files else []
        for f in files_to_process:
            if not f or not f.filename:
                continue
            if not allowed_file(f.filename):
                continue
            filename = secure_filename(f.filename)
            save_path = os.path.join(Config.UPLOAD_FOLDER, f"{msg_id}_{filename}")
            f.save(save_path)
            content_text = read_file_safe(save_path)
            db_module.add_attachment(msg_id, filename, save_path, content_text)
            attachment_texts.append(f"[{filename}]\n{content_text}")

        reply = reply_to_user(user_content, attachment_texts if attachment_texts else None)
        meta = _build_meta()
        model_name = (meta.get("model") or "(авто)").strip()
        db_module.add_chat_message(
            "assistant",
            reply or "Нет ответа.",
            model_name=model_name,
            meta_json=json.dumps(meta),
        )
        # Сообщение пользователя попадает в поток мыслей — следующий цикл размышлений будет от него
        seed = ("Пользователь написал: " + user_content.strip())[:600]
        db_module.add_short_term_memory(seed, tags="chat")

        # Счётчик сообщений после последней суммаризации: при старте 0, после каждой отправки +2; при достижении N — суммаризация и сброс в 0
        raw = (db_module.get_setting("summarize_every_n", "20") or "").strip()
        n_val = int(raw) if raw.isdigit() else 20
        raw_counter = (db_module.get_setting("messages_since_summary", "0") or "").strip()
        counter = int(raw_counter) if raw_counter.isdigit() else 0
        counter += 2  # только что добавили user + assistant
        db_module.set_setting("messages_since_summary", str(counter))
        next_at = n_val if n_val > 0 else 0
        logger.info(
            "[api] Chat message sent. messages_since_summary=%s, summarize_every_n=%s. "
            "Long-term memory from summarization runs only when counter >= n (next at %s messages).",
            counter, n_val, next_at
        )

        if n_val <= 0:
            logger.info("[api] Summarization disabled (summarize_every_n=%s).", raw or "0")
        elif counter >= n_val:
            logger.info("[api] Summarization TRIGGER: counter=%s >= n=%s, resetting counter, starting background thread.", counter, n_val)
            db_module.set_setting("messages_since_summary", "0")
            flask_app = request.application
            summary_limit = min(n_val, 100)

            def _run_summary():
                with flask_app.app_context():
                    logger.info("[summary] Step 1/6: thread started, limit=%s.", summary_limit)
                    try:
                        msgs = db_module.get_recent_chat_messages(limit=summary_limit)
                        logger.info("[summary] Step 2/6: get_recent_chat_messages returned %s messages.", len(msgs) if msgs else 0)
                        if not msgs:
                            logger.warning("[summary] Step 2 FAIL: no messages to summarize. Abort.")
                            return
                        summary = summarize_chat_messages(msgs)
                        logger.info("[summary] Step 3/6: summarize_chat_messages returned %s chars. Content: %s", len(summary) if summary else 0, (summary[:200] + "...") if summary and len(summary) > 200 else (summary or "(empty)"))
                        if not summary:
                            logger.warning("[summary] Step 3: LLM returned empty summary; saving fallback to long_term_memory.")
                            db_module.set_setting("chat_summary", "")
                            fallback_text = "Суммаризация: модель не вернула краткое содержание (проверьте LLM/API). Сообщений в диалоге: %s." % len(msgs)
                            db_module.add_long_term_memory(fallback_text, tags="chat_summary")
                            return
                        db_module.set_setting("chat_summary", summary)
                        main_thought = extract_main_thought(summary)
                        logger.info("[summary] Step 4/6: extract_main_thought returned %s chars. Content: %s", len(main_thought) if main_thought else 0, (main_thought[:150] + "...") if main_thought and len(main_thought) > 150 else (main_thought or "(empty)"))
                        if not main_thought:
                            # Сохраняем хотя бы начало суммаризации, чтобы в долговременной памяти что-то появилось
                            main_thought = (summary or "").strip()[:500] or "Суммаризация диалога (главная мысль не выделена)."
                            logger.warning("[summary] Step 4: main thought empty, saving summary excerpt to long-term memory.")
                        mem_id = db_module.add_long_term_memory(main_thought, tags="chat_summary")
                        logger.info("[summary] Step 5/6: add_long_term_memory OK, id=%s, tags=chat_summary.", mem_id)
                        journal_id = db_module.add_short_term_memory(
                            "Проведена суммаризация; главная мысль сохранена в долговременную память.",
                            tags="summarization",
                        )
                        logger.info("[summary] Step 6/6: add_short_term_memory (journal) OK, id=%s. Summarization complete.", journal_id)
                        # Сообщение в чат, чтобы пользователь видел факт суммаризации (в БД и в интерфейсе чата)
                        summary_chat_text = (
                            "Проведена суммаризация диалога. Краткое содержание сохранено в контекст; "
                            "главная мысль — в долговременную память (раздел «Память»)."
                        )
                        summary_meta = {
                            "model": (db_module.get_setting("model_name", "") or "").strip() or "(авто)",
                            "context_limit": int(db_module.get_setting("context_limit", "") or str(Config.CONTEXT_LIMIT)) if (db_module.get_setting("context_limit", "") or "").strip().isdigit() else Config.CONTEXT_LIMIT,
                            "chat_max_tokens": (db_module.get_setting("chat_max_tokens", str(Config.MAX_TOKENS)) or "").strip(),
                            "intro_max_tokens": (db_module.get_setting("intro_max_tokens", str(Config.INTRO_MAX_TOKENS)) or "").strip(),
                            "temperature": (db_module.get_setting("temperature", "0.7") or "").strip(),
                            "summarize_every_n": (db_module.get_setting("summarize_every_n", "20") or "").strip(),
                        }
                        db_module.add_chat_message(
                            "assistant",
                            summary_chat_text,
                            model_name="",
                            meta_json=json.dumps(summary_meta),
                        )
                        logger.info("[summary] Step 7/7: add_chat_message (summary notice) OK. Message visible in chat.")
                    except Exception as e:
                        logger.exception("[summary] Summarization FAIL with exception: %s", e)
            t = threading.Thread(target=_run_summary, daemon=True)
            t.start()
            logger.info("[api] Summarization background thread started.")
        else:
            logger.info("[api] Summarization skip: counter=%s < n=%s (need >= %s for next run).", counter, n_val, n_val)

        return jsonify({
            "user_message_id": msg_id,
            "reply": reply or "",
        })

    # ---------- API: журналы памяти ----------
    def _build_meta():
        """Метаданные из настроек (модель, контекст и т.п.) для чата и журнала мыслей."""
        ctx_limit = Config.CONTEXT_LIMIT
        ctx_setting = (db_module.get_setting("context_limit", "") or "").strip()
        if ctx_setting.isdigit():
            ctx_limit = int(ctx_setting)
        return {
            "model": (db_module.get_setting("model_name", "") or "").strip() or "(авто)",
            "context_limit": ctx_limit,
            "chat_max_tokens": (db_module.get_setting("chat_max_tokens", str(Config.MAX_TOKENS)) or "").strip(),
            "intro_max_tokens": (db_module.get_setting("intro_max_tokens", str(Config.INTRO_MAX_TOKENS)) or "").strip(),
            "temperature": (db_module.get_setting("temperature", "0.7") or "").strip(),
            "repetition_threshold": (db_module.get_setting("repetition_threshold", "0.7") or "").strip(),
            "summarize_every_n": (db_module.get_setting("summarize_every_n", "20") or "").strip(),
        }

    @app.route("/api/thoughts", methods=["GET"])
    def get_thoughts():
        """Журнал размышлений: записи в хронологическом порядке с пагинацией (page, per_page) и нумерацией."""
        db_module.set_setting("idle_bypass", "1")
        total_count = db_module.get_short_term_memory_count()
        page = max(1, int(request.args.get("page", 1)))
        per_page = max(1, min(200, int(request.args.get("per_page", 50))))
        total_pages = max(1, (total_count + per_page - 1) // per_page) if total_count else 1
        offset = (page - 1) * per_page
        items = db_module.get_short_term_memory_paged(offset=offset, limit=per_page)
        out = []
        for i, m in enumerate(items):
            meta = None
            if getattr(m, "meta_json", None) and (m.meta_json or "").strip():
                try:
                    meta = json.loads(m.meta_json)
                except (TypeError, ValueError):
                    pass
            if meta is None:
                meta = _build_meta()
            num = offset + i + 1
            out.append({
                "id": m.id,
                "num": num,
                "num_display": "№ {}".format(num),
                "timestamp": m.timestamp.isoformat() if m.timestamp else None,
                "text": m.text,
                "tags": m.tags or "",
                "model": (getattr(m, "model_name", None) or "").strip() or meta.get("model") or "(авто)",
                "meta": meta,
            })
        return jsonify({
            "items": out,
            "total": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "meta": _build_meta(),
        })

    @app.route("/api/thoughts", methods=["DELETE"])
    def delete_thoughts():
        """Очистка журнала мыслей: удалить все записи краткосрочной памяти и сбросить первоначальный вопрос цепочки."""
        count = db_module.clear_short_term_memory()
        db_module.set_setting("intro_anchor", "")
        return jsonify({"ok": True, "deleted": count})

    def _format_meta_line(meta):
        """Одна строка метаданных для экспорта."""
        if not meta or not isinstance(meta, dict):
            return ""
        parts = []
        if meta.get("model"):
            parts.append("модель: {}".format(meta["model"]))
        if meta.get("context_limit") is not None:
            parts.append("контекст: {} ток.".format(meta["context_limit"]))
        if meta.get("intro_max_tokens"):
            parts.append("макс. токенов саморазмышления: {}".format(meta["intro_max_tokens"]))
        if meta.get("temperature"):
            parts.append("temp: {}".format(meta["temperature"]))
        if meta.get("repetition_threshold"):
            parts.append("порог повторений: {}".format(meta["repetition_threshold"]))
        return " | ".join(parts)

    @app.route("/api/thoughts/export", methods=["GET"])
    def export_thoughts_md():
        """Экспорт журнала мыслей в Markdown (файл для скачивания), все записи без обрезки."""
        db_module.set_setting("idle_bypass", "1")
        items = db_module.get_all_short_term_memory()
        lines = ["# Журнал текущих мыслей", "", "Дата экспорта: {}".format(dt.now().strftime("%Y-%m-%d %H:%M")), ""]
        for i, m in enumerate(items):
            num = i + 1
            ts = m.timestamp.strftime("%Y-%m-%d %H:%M") if m.timestamp else ""
            tag_str = (m.tags or "").strip()
            model_name = (getattr(m, "model_name", None) or "").strip()
            meta = None
            if getattr(m, "meta_json", None) and (m.meta_json or "").strip():
                try:
                    meta = json.loads(m.meta_json)
                except (TypeError, ValueError):
                    pass
            lines.append("## № {}".format(num))
            if ts:
                lines.append("**Дата:** {}".format(ts))
            if tag_str:
                lines.append("**Теги:** {}".format(tag_str))
            meta_line = _format_meta_line(meta) if meta else ("модель: " + model_name if model_name else "")
            if meta_line or model_name:
                lines.append("**Модель и параметры:** {}".format(meta_line or ("модель: " + (model_name or "(авто)"))))
            lines.append("")
            lines.append((m.text or "").strip())
            lines.append("")
            lines.append("---")
            lines.append("")
        body = "\n".join(lines)
        filename = "thoughts_{}.md".format(dt.now().strftime("%Y-%m-%d_%H-%M"))
        return Response(
            body,
            mimetype="text/markdown; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=\"{}\"".format(filename)},
        )

    @app.route("/api/thoughts/summarize", methods=["POST"])
    def summarize_thoughts():
        """Обобщить весь журнал мыслей: чанками по N записей суммаризация, затем общий итог — сохранить в долговременную память как вывод по первому запросу пользователя."""
        db_module.set_setting("idle_bypass", "1")
        items = db_module.get_all_short_term_memory()
        if not items:
            return jsonify({"error": "Журнал пуст, нечего обобщать"}), 400
        data = request.get_json(silent=True) or {}
        chunk_size = max(3, min(50, int(data.get("chunk_size") or 10)))
        try:
            final_summary, first_user_text = summarize_journal_entries(items, chunk_size=chunk_size)
        except Exception as e:
            logger.exception("[api] summarize_thoughts failed: %s", e)
            return jsonify({"error": "Ошибка обобщения: {}".format(str(e))}), 500
        if not final_summary:
            return jsonify({"error": "Не удалось получить итог обобщения"}), 500
        # Сохраняем в долговременную память: по первоначальному запросу + общий вывод
        if first_user_text:
            text_to_save = "По запросу: «{}»\n\nИтог: {}".format(first_user_text.replace("«", '"').replace("»", '"')[:300], final_summary)
        else:
            text_to_save = "Итог обобщения журнала:\n\n" + final_summary
        mem_id = db_module.add_long_term_memory(text_to_save, tags="journal_summary")
        logger.info("[api] summarize_thoughts: saved to long_term_memory id=%s.", mem_id)
        return jsonify({"ok": True, "memory_id": mem_id, "summary_length": len(final_summary)})

    def _sse_message(event: str, data: dict) -> str:
        """Формирует одно SSE-сообщение: event + data (JSON)."""
        return "event: {}\ndata: {}\n\n".format(event, json.dumps(data, ensure_ascii=False))

    @app.route("/api/thoughts/summarize/stream", methods=["POST"])
    def summarize_thoughts_stream():
        """Обобщение журнала с потоковой отдачей прогресса (SSE). Для визуального прогресса на фронте."""
        db_module.set_setting("idle_bypass", "1")
        items = db_module.get_all_short_term_memory()
        if not items:
            return jsonify({"error": "Журнал пуст, нечего обобщать"}), 400
        data = request.get_json(silent=True) or {}
        chunk_size = max(3, min(50, int(data.get("chunk_size") or 10)))

        def progress_cb(phase, current, total):
            q.put({"type": "progress", "phase": phase, "current": current, "total": total})

        q = Queue()

        def run_summarize():
            try:
                final_summary, first_user_text = summarize_journal_entries(
                    items, chunk_size=chunk_size, progress_callback=progress_cb
                )
                if not final_summary:
                    q.put({"type": "error", "message": "Не удалось получить итог обобщения"})
                    return
                if first_user_text:
                    text_to_save = "По запросу: «{}»\n\nИтог: {}".format(
                        first_user_text.replace("«", '"').replace("»", '"')[:300], final_summary
                    )
                else:
                    text_to_save = "Итог обобщения журнала:\n\n" + final_summary
                mem_id = db_module.add_long_term_memory(text_to_save, tags="journal_summary")
                logger.info("[api] summarize_thoughts_stream: saved to long_term_memory id=%s.", mem_id)
                q.put({"type": "done", "memory_id": mem_id, "summary_length": len(final_summary)})
            except Exception as e:
                logger.exception("[api] summarize_thoughts_stream failed: %s", e)
                q.put({"type": "error", "message": str(e)})

        def generate():
            yield _sse_message("start", {"total_entries": len(items), "chunk_size": chunk_size})
            t = threading.Thread(target=run_summarize)
            t.start()
            while True:
                ev = q.get()
                t = ev.get("type", "message")
                yield _sse_message(t, ev)
                if t in ("done", "error"):
                    break

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.route("/api/thoughts/archive", methods=["POST"])
    def archive_thoughts():
        """Архивировать текущий журнал мыслей и очистить его. В архив попадают все записи без обрезки."""
        db_module.set_setting("idle_bypass", "1")
        items = db_module.get_all_short_term_memory()
        entries = [{
            "text": (m.text or ""),
            "tags": (m.tags or ""),
            "trigger_text": (m.trigger_text or ""),
            "model_name": (getattr(m, "model_name", None) or ""),
            "meta_json": (getattr(m, "meta_json", None) or ""),
        } for m in items]
        if not entries:
            return jsonify({"error": "Журнал пуст, нечего архивировать"}), 400
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip() or "Архив {}".format(dt.now().strftime("%Y-%m-%d %H:%M"))
        archive_id = db_module.add_thought_archive(name, entries)
        db_module.clear_short_term_memory()
        db_module.set_setting("intro_anchor", "")
        logger.info("[api] Thoughts archived: id=%s, name=%s, entries=%s.", archive_id, name, len(entries))
        return jsonify({"ok": True, "id": archive_id, "name": name, "entries_count": len(entries)})

    @app.route("/api/thoughts/archives", methods=["GET"])
    def list_thought_archives():
        """Список архивов веток размышлений (для раздела История)."""
        rows = db_module.get_thought_archives_list(limit=100)
        return jsonify([{
            "id": r["id"],
            "name": r["name"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "entries_count": r["entries_count"],
        } for r in rows])

    @app.route("/api/thoughts/archives/<int:archive_id>", methods=["GET"])
    def get_thought_archive(archive_id):
        """Один архив по id (для предпросмотра и восстановления)."""
        arch = db_module.get_thought_archive(archive_id)
        if not arch:
            return jsonify({"error": "Архив не найден"}), 404
        return jsonify({
            "id": arch["id"],
            "name": arch["name"],
            "created_at": arch["created_at"].isoformat() if arch["created_at"] else None,
            "entries": arch["entries"],
        })

    @app.route("/api/thoughts/archives/<int:archive_id>/restore", methods=["POST"])
    def restore_thought_archive(archive_id):
        """Восстановить журнал из архива: текущий журнал очищается и заменяется записями из архива."""
        arch = db_module.get_thought_archive(archive_id)
        if not arch:
            return jsonify({"error": "Архив не найден"}), 404
        db_module.clear_short_term_memory()
        db_module.set_setting("intro_anchor", "")
        for e in arch["entries"]:
            db_module.add_short_term_memory(
                e.get("text") or "",
                tags=e.get("tags") or "",
                trigger_text=e.get("trigger_text") or "",
                model_name=e.get("model_name") or "",
                meta_json=e.get("meta_json") or "",
            )
        logger.info("[api] Archive %s restored, %s entries.", archive_id, len(arch["entries"]))
        return jsonify({"ok": True, "entries_restored": len(arch["entries"])})

    @app.route("/api/thoughts/archives/<int:archive_id>", methods=["DELETE"])
    def delete_thought_archive(archive_id):
        """Удалить архив."""
        if not db_module.delete_thought_archive(archive_id):
            return jsonify({"error": "Архив не найден"}), 404
        return jsonify({"ok": True})

    @app.route("/api/thoughts/archives/<int:archive_id>/export", methods=["GET"])
    def export_thought_archive_md(archive_id):
        """Экспорт архива в Markdown (файл для скачивания)."""
        arch = db_module.get_thought_archive(archive_id)
        if not arch:
            return jsonify({"error": "Архив не найден"}), 404
        entries = arch["entries"]
        lines = ["# {}".format(arch["name"]), "", "Дата архива: {}".format(arch["created_at"].strftime("%Y-%m-%d %H:%M") if arch["created_at"] else ""), ""]
        for i, e in enumerate(entries):
            num = i + 1
            tag_str = (e.get("tags") or "").strip()
            model_name = (e.get("model_name") or "").strip()
            meta = None
            if (e.get("meta_json") or "").strip():
                try:
                    meta = json.loads(e.get("meta_json") or "{}")
                except (TypeError, ValueError):
                    pass
            if meta is not None and not meta.get("model") and model_name:
                meta = dict(meta)
                meta["model"] = model_name
            meta_line = _format_meta_line(meta) if meta else ("модель: " + model_name if model_name else "")
            lines.append("## № {}".format(num))
            if tag_str:
                lines.append("**Теги:** {}".format(tag_str))
            if meta_line or model_name:
                lines.append("**Модель и параметры:** {}".format(meta_line or ("модель: " + (model_name or "(авто)"))))
            lines.append("")
            lines.append((e.get("text") or "").strip())
            lines.append("")
            lines.append("---")
            lines.append("")
        body = "\n".join(lines)
        filename = "archive_{}_{}.md".format(archive_id, dt.now().strftime("%Y-%m-%d"))
        return Response(
            body,
            mimetype="text/markdown; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=\"{}\"".format(filename)},
        )

    @app.route("/api/memory", methods=["GET"])
    def get_memory():
        items = db_module.get_long_term_memory_list(limit=100)
        logger.info("[api] get_memory: returning %s long-term memory items.", len(items))
        return jsonify([{
            "id": m.id,
            "timestamp": m.timestamp.isoformat() if m.timestamp else None,
            "text": m.text,
            "tags": m.tags or "",
        } for m in items])

    @app.route("/api/memory", methods=["POST"])
    def add_memory():
        """Добавить запись в долгосрочную память (ручная пометка)."""
        data = request.get_json() or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Текст обязателен"}), 400
        tags = (data.get("tags") or "").strip()
        mid = db_module.add_long_term_memory(text, tags)
        return jsonify({"id": mid})

    # ---------- API: процесс мышления (запуск/остановка) ----------
    @app.route("/api/thinking/status", methods=["GET"])
    def thinking_status():
        v = db_module.get_setting("thinking_enabled", "1").strip().lower()
        return jsonify({"running": v in ("1", "true", "yes")})

    @app.route("/api/thinking/start", methods=["POST"])
    def thinking_start():
        """Включить режим размышлений и снять ожидание простоя: следующий цикл планировщика запустится сразу."""
        db_module.set_setting("thinking_enabled", "1")
        db_module.set_setting("idle_bypass", "1")
        return jsonify({"ok": True, "running": True})

    @app.route("/api/thinking/think_now", methods=["POST"])
    def thinking_think_now():
        """Включить режим размышлений, при необходимости добавить текст пользователя в поток мыслей и сразу запустить цикл.
        Текст пользователя задаёт первоначальный вопрос цепочки (intro_anchor) — все мысли будут привязаны к нему."""
        db_module.set_setting("thinking_enabled", "1")
        db_module.set_setting("idle_bypass", "1")
        data = request.get_json(silent=True) or {}
        user_text = (data.get("text") or "").strip()
        if user_text:
            db_module.add_short_term_memory(user_text[:600], tags="user_seed")
            db_module.set_setting("intro_anchor", user_text[:500])
        def run_once():
            run_introspection_guarded()
        t = threading.Thread(target=run_once, daemon=True)
        t.start()
        return jsonify({"ok": True, "running": True})

    @app.route("/api/thinking/stop", methods=["POST"])
    def thinking_stop():
        db_module.set_setting("thinking_enabled", "0")
        return jsonify({"ok": True, "running": False})

    # ---------- API: список моделей (LM Studio / OpenRouter) ----------
    @app.route("/api/models", methods=["GET"])
    def get_models():
        try:
            # Опционально: загрузка моделей OpenRouter по переданному ключу (до сохранения в настройках)
            provider_param = request.args.get("provider", "").strip().lower()
            key_param = (request.args.get("openrouter_api_key") or "").strip()
            if provider_param == "openrouter" and key_param:
                models = list_models_openrouter_with_key(key_param)
            else:
                models = list_models()
            out = {"models": models}
            if not models and getattr(llm_module, "_last_list_models_error", None):
                out["error"] = llm_module._last_list_models_error
            return jsonify(out)
        except Exception as e:
            logger.exception("[api] get_models failed: %s", e)
            return jsonify({"models": [], "error": str(e)})

    # ---------- API: настройки ----------
    @app.route("/api/settings", methods=["GET"])
    def get_settings():
        """Настройки: все значения и производные (idle_minutes) считаются на бэкенде. Фронт только подставляет в поля."""
        v = db_module.get_setting("thinking_enabled", "1").strip().lower()
        idle_sec = db_module.get_setting("idle_seconds", "300").strip()
        idle_seconds = int(idle_sec) if idle_sec.isdigit() else 300
        llm_provider = (db_module.get_setting("llm_provider", "lm_studio") or "lm_studio").strip()
        if llm_provider not in ("lm_studio", "openrouter"):
            llm_provider = "lm_studio"
        openrouter_key = (db_module.get_setting("openrouter_api_key", "") or "").strip()
        if not openrouter_key and getattr(Config, "OPENROUTER_API_KEY", None):
            openrouter_key = (Config.OPENROUTER_API_KEY or "").strip()
        return jsonify({
            "intro_interval": db_module.get_setting("intro_interval", str(Config.INTRO_INTERVAL)),
            "intro_max_tokens": db_module.get_setting("intro_max_tokens", str(Config.INTRO_MAX_TOKENS)),
            "chat_max_tokens": db_module.get_setting("chat_max_tokens", str(Config.MAX_TOKENS)),
            "intro_prompt": db_module.get_setting("intro_prompt", Config.DEFAULT_INTRO_PROMPT),
            "model_name": db_module.get_setting("model_name", ""),
            "llm_provider": llm_provider,
            "openrouter_api_key": openrouter_key,
            "thinking_enabled": v in ("1", "true", "yes"),
            "idle_seconds": str(idle_seconds),
            "idle_minutes": str(max(0, idle_seconds // 60)),
            "temperature": db_module.get_setting("temperature", "0.7"),
            "summarize_every_n": db_module.get_setting("summarize_every_n", "20"),
            "model_thinking_disabled": db_module.get_setting("model_thinking_disabled", "0").strip() in ("1", "true", "yes"),
            "model_thinking_max_tokens": db_module.get_setting("model_thinking_max_tokens", "0").strip(),
            "repetition_threshold": db_module.get_setting("repetition_threshold", "0.7").strip(),
        })

    @app.route("/api/settings", methods=["POST"])
    def save_settings():
        data = request.get_json() or {}
        if "intro_interval" in data:
            v = str(data["intro_interval"]).strip()
            if v.isdigit():
                db_module.set_setting("intro_interval", v)
        if "intro_max_tokens" in data:
            v = str(data["intro_max_tokens"]).strip()
            if v.isdigit():
                db_module.set_setting("intro_max_tokens", v)
        if "chat_max_tokens" in data:
            v = str(data["chat_max_tokens"]).strip()
            if v.isdigit() and int(v) >= 64:
                db_module.set_setting("chat_max_tokens", v)
        if "intro_prompt" in data:
            db_module.set_setting("intro_prompt", str(data["intro_prompt"]))
        if "model_name" in data:
            db_module.set_setting("model_name", str(data["model_name"]).strip())
        if "llm_provider" in data:
            p = str(data["llm_provider"]).strip().lower()
            if p in ("lm_studio", "openrouter"):
                db_module.set_setting("llm_provider", p)
        if "openrouter_api_key" in data:
            db_module.set_setting("openrouter_api_key", str(data["openrouter_api_key"]).strip())
        if "thinking_enabled" in data:
            db_module.set_setting("thinking_enabled", "1" if data["thinking_enabled"] else "0")
        if "idle_seconds" in data:
            v = str(data["idle_seconds"]).strip()
            if v.isdigit():
                db_module.set_setting("idle_seconds", v)
        if "idle_minutes" in data:
            try:
                m = int(data["idle_minutes"])
                db_module.set_setting("idle_seconds", str(max(0, m * 60)))
            except (TypeError, ValueError):
                pass
        if "temperature" in data:
            try:
                t = float(data["temperature"])
                t = max(0.0, min(2.0, t))
                db_module.set_setting("temperature", str(t))
            except (TypeError, ValueError):
                pass
        if "summarize_every_n" in data:
            v = str(data["summarize_every_n"]).strip()
            if v.isdigit() and int(v) >= 0:
                db_module.set_setting("summarize_every_n", v)
        if "model_thinking_disabled" in data:
            db_module.set_setting("model_thinking_disabled", "1" if data["model_thinking_disabled"] else "0")
        if "model_thinking_max_tokens" in data:
            v = str(data["model_thinking_max_tokens"]).strip()
            if v.isdigit() and int(v) >= 0:
                db_module.set_setting("model_thinking_max_tokens", v)
        if "repetition_threshold" in data:
            try:
                v = float(data["repetition_threshold"])
                v = max(0.0, min(1.0, v))
                db_module.set_setting("repetition_threshold", str(v))
            except (TypeError, ValueError):
                pass
        return jsonify({"ok": True})

    return app
