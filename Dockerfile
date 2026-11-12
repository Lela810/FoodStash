FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FOODSTASH_DB=/data/foodstash.db \
    PORT=8080
WORKDIR /app
RUN groupadd --gid 10001 foodstash \
    && useradd --uid 10001 --gid foodstash --no-create-home foodstash \
    && mkdir /data && chown foodstash:foodstash /data
COPY --chown=foodstash:foodstash foodstash.py VERSION ./
COPY --chown=foodstash:foodstash static ./static
USER foodstash
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3).read()"
CMD ["python", "foodstash.py"]
