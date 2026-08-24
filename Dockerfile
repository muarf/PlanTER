# PlanTER — API + site web dans une seule image.
# Le graphe de routage (data/graph.bin, ~120 Mo) n'est PAS embarqué :
# il vit dans un volume et se construit au premier démarrage
# (voir docker/entrypoint.sh et REFRESH_ON_START).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY web/ web/
COPY scripts/ scripts/

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /app/data /app/reports

VOLUME ["/app/data", "/app/reports"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('PORT','8000')+'/v1/health', timeout=4)" || exit 1

ENTRYPOINT ["entrypoint.sh"]
