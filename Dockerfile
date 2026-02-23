# AST-Thinker: Flask + планировщик саморазмышлений. Python 3.12 — готовые колёса для aiohttp/pydantic.
FROM python:3.12-slim

WORKDIR /app

# Зависимости приложения
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения
COPY app ./app
COPY .env.example .env.example

# Каталоги для БД и загрузок (при монтировании volume перезаписываются)
ENV DB_PATH=/app/data/db.sqlite
ENV UPLOAD_FOLDER=/app/uploads
RUN mkdir -p /app/data /app/uploads

EXPOSE 7111
ENV PORT=7111

CMD ["python", "-m", "app.main"]
