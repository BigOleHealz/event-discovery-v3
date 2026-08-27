FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt .
RUN pip install --no-cache-dir --requirement requirements.txt

COPY --chmod=755 entrypoint.sh ./entrypoint.sh
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

USER app

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app"]
