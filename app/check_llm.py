# -*- coding: utf-8 -*-
"""
Проверка доступа к LM Studio. Запуск в контейнере:
  docker compose exec thinker python -m app.check_llm
Без Docker из корня проекта: python -m app.check_llm
"""
import os
import sys

# корень проекта (родитель app/) в path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)


def main():
    from app.config import Config
    base = Config.API_BASE_URL
    v1 = Config.API_BASE_URL_V1
    print("API_BASE_URL:", base)
    print("API_BASE_URL_V1:", v1)

    # 1) сырой HTTP GET /v1/models
    try:
        import urllib.request
        url = v1.rstrip("/") + "/models"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read().decode("utf-8", errors="replace")
            print("GET /v1/models: OK", r.status)
            if "data" in data:
                print("  (есть данные моделей)")
            else:
                print("  (ответ без data)", data[:200])
    except Exception as e:
        print("GET /v1/models: FAIL", e)
        return 1

    # 2) через клиент приложения
    from app.llm import list_models, call_llm
    ids = list_models()
    print("list_models():", ids if ids else "(пусто)")
    if not ids:
        print("  Проверьте: LM Studio запущен? В Docker: API_BASE_URL=http://host.docker.internal:1234")
        return 1

    # 3) короткий запрос к чату
    try:
        out = call_llm([{"role": "user", "content": "Ответь одним словом: ок"}], max_tokens=5)
        print("call_llm (тест):", repr(out))
    except Exception as e:
        print("call_llm: FAIL", e)
        return 1

    print("OK: LM Studio доступен, модели и чат работают.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
