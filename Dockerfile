#
# Bambu filamentkatalog – inloggning, administration och nattlig prisuppdatering
#
#   docker compose up -d --build
#
# Bygg om data.json/offers.json från sources/ i stället för att använda dist/ som den är:
#   docker build --build-arg REBUILD_DATA=true -t bambu-filamentkatalog .

ARG PYTHON_IMAGE=python:3.13-alpine
ARG NODE_IMAGE=node:22-alpine
ARG RUNTIME_IMAGE=nginxinc/nginx-unprivileged:1.29-alpine

############################
# 1. Data (valfri ombyggnad)
############################
FROM ${PYTHON_IMAGE} AS data
ARG REBUILD_DATA=false
WORKDIR /src
COPY dist/ dist/
COPY build_catalog.py build_prices.py prices.py ./
# sources/ (17 MB) används bara här och hamnar aldrig i slutimagen.
COPY sources/ sources/
# Kompakt JSON (inga mellanslag efter , och :) – samma innehåll, mindre fil.
RUN if [ "$REBUILD_DATA" = "true" ]; then python build_catalog.py && python build_prices.py; fi \
 && python -c "import json,pathlib;[p.write_text(json.dumps(json.loads(p.read_text(encoding='utf8')),ensure_ascii=False,separators=(',',':')),encoding='utf8') for p in pathlib.Path('dist').glob('*.json')]"

############################
# 2. Minifiering + förkomprimering
############################
FROM ${NODE_IMAGE} AS assets
WORKDIR /site
RUN apk add --no-cache zopfli \
 && npm install -g --no-fund --no-audit esbuild@0.25.10
COPY --from=data /src/dist/ ./
# Minifiera JS och skapa .gz med zopfli (bästa möjliga gzip); nginx serverar .gz direkt.
RUN set -eu; \
    for f in *.js; do esbuild "$f" --minify --target=es2020 --allow-overwrite --outfile="$f" --log-level=warning; done; \
    for f in *.html *.js *.json; do \
        zopfli --i15 "$f"; \
        touch -r "$f" "$f.gz"; \
    done

############################
# 3. Runtime: nginx + Python (endast standardbiblioteket), icke-root
############################
FROM ${RUNTIME_IMAGE} AS runtime
LABEL org.opencontainers.image.title="Bambu filamentkatalog" \
      org.opencontainers.image.description="Färg- och priskatalog för Bambu Lab-filament med inloggning och nattlig prisuppdatering" \
      org.opencontainers.image.source="https://github.com/BraXhn/Bambu-filamentkatalog" \
      org.opencontainers.image.licenses="UNLICENSED"
USER root
RUN apk add --no-cache python3 tzdata tini \
 && rm -f /etc/nginx/conf.d/default.conf \
 && mkdir -p /data && chown 101:101 /data
WORKDIR /app
COPY --chown=root:root katalog/ /app/katalog/
COPY --chown=root:root assets/ /app/assets/
COPY --chown=root:root --from=assets /site/ /app/www/
RUN python3 -m compileall -q /app/katalog
ENV KATALOG_APP_DIR=/app \
    KATALOG_DATA_DIR=/data \
    TZ=Europe/Stockholm \
    PRICE_UPDATE_TIME=01:00 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 101:101
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["/sbin/tini", "--"]
CMD ["python3", "-m", "katalog", "serve"]
