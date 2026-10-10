#
# Bambu filamentkatalog – optimerad container
#
#   docker build -t bambu-filamentkatalog .
#   docker run -d -p 8000:8080 --read-only --tmpfs /tmp bambu-filamentkatalog
#
# Bygg om data.json/offers.json från sources/ i stället för att använda dist/ som den är:
#   docker build --build-arg REBUILD_DATA=true -t bambu-filamentkatalog .

ARG PYTHON_IMAGE=python:3.13-alpine
ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.29-alpine-slim

############################
# 1. Data (valfri ombyggnad)
############################
FROM ${PYTHON_IMAGE} AS data
ARG REBUILD_DATA=false
WORKDIR /app
COPY dist/ dist/
COPY build_catalog.py build_prices.py prices.py ./
# sources/ (17 MB) finns bara i detta steg och hamnar aldrig i slutimagen.
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
COPY --from=data /app/dist/ ./
# Minifiera JS och skapa .gz med zopfli (bästa möjliga gzip); nginx serverar .gz direkt.
RUN set -eu; \
    for f in *.js; do esbuild "$f" --minify --target=es2020 --allow-overwrite --outfile="$f" --log-level=warning; done; \
    for f in *.html *.js *.json; do \
        zopfli --i15 "$f"; \
        touch -r "$f" "$f.gz"; \
    done; \
    ls -la

############################
# 3. Runtime: nginx, icke-root, ~12 MB
############################
FROM ${NGINX_IMAGE} AS runtime
LABEL org.opencontainers.image.title="Bambu filamentkatalog" \
      org.opencontainers.image.description="Statisk färg- och priskatalog för Bambu Lab-filament"
COPY --chown=root:root nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=root:root --from=assets /site/ /usr/share/nginx/html/
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
