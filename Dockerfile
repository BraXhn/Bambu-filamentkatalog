#
# Bambu filamentkatalog – inloggning, administration och nattlig prisuppdatering
#
#   docker compose up -d --build
#
# Bygg om data.json/offers.json från sources/ i stället för att använda dist/ som den är:
#   docker build --build-arg REBUILD_DATA=true -t bambu-filamentkatalog .

ARG PYTHON_IMAGE=python:3.13-alpine
ARG NODE_IMAGE=node:22-alpine

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
# 3. Runtime: en Python-process (endast standardbiblioteket), icke-root
############################
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="Bambu filamentkatalog" \
      org.opencontainers.image.description="Färg- och priskatalog för Bambu Lab-filament med inloggning och nattlig prisuppdatering" \
      org.opencontainers.image.source="https://github.com/BraXhn/Bambu-filamentkatalog"
RUN apk add --no-cache tini tzdata su-exec \
 && mkdir -p /data && chown 99:100 /data
WORKDIR /app
COPY --chown=root:root katalog/ /app/katalog/
COPY --chown=root:root assets/ /app/assets/
COPY --chown=root:root --chmod=755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chown=root:root --from=assets /site/ /app/www/
RUN python -m compileall -q /app/katalog && chmod -R a+rX /app
ENV KATALOG_APP_DIR=/app \
    KATALOG_DATA_DIR=/data \
    TZ=Europe/Stockholm \
    PRICE_UPDATE_TIME=01:00 \
    PORT=8080 \
    HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PUID=99 \
    PGID=100
# Appen kör som PUID:PGID (99:100 = nobody:users, som Unraid använder för appdata).
# entrypoint.sh startar som root enbart för att rätta ägaren på /data och släpper sedan rättigheterna.
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -q -O /dev/null "http://127.0.0.1:${PORT}/healthz" || exit 1
ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "katalog", "serve"]
