#!/bin/sh
# Startar som root bara för att ge datamappen rätt ägare, släpper sedan alla rättigheter
# och kör appen som PUID:PGID (standard 99:100 = Unraids nobody:users).
set -e
PUID="${PUID:-99}"
PGID="${PGID:-100}"
if [ "$(id -u)" = "0" ]; then
    mkdir -p "${KATALOG_DATA_DIR:-/data}"
    if [ "$(stat -c %u:%g "${KATALOG_DATA_DIR:-/data}")" != "$PUID:$PGID" ] || \
       [ -n "$(find "${KATALOG_DATA_DIR:-/data}" -mindepth 1 \( ! -user "$PUID" -o ! -group "$PGID" \) -print -quit 2>/dev/null)" ]; then
        echo "[start] ger ${KATALOG_DATA_DIR:-/data} ägaren $PUID:$PGID"
        chown -R "$PUID:$PGID" "${KATALOG_DATA_DIR:-/data}" || echo "[varning] kunde inte ändra ägare på datamappen"
    fi
    exec su-exec "$PUID:$PGID" "$@"
fi
exec "$@"
