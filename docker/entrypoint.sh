#!/bin/sh
# Startar som root bara för att ge datamappen rätt ägare, släpper sedan alla rättigheter
# och kör appen som PUID:PGID (standard 99:100 = Unraids nobody:users).
set -e
PUID="${PUID:-99}"
PGID="${PGID:-100}"
DATA="${KATALOG_DATA_DIR:-/data}"
case "$PUID$PGID" in *[!0-9]*|'') echo "[FEL] PUID/PGID måste vara siffror"; exit 1 ;; esac
if [ "$PUID" = "0" ] || [ "$PGID" = "0" ]; then
    echo "[FEL] PUID/PGID 0 (root) tillåts inte. Använd t.ex. 99 och 100."; exit 1
fi
if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA"
    # Byt bara ägare på kataloger och vanliga filer med en enda länk i samma filsystem:
    # följer aldrig symboliska länkar och rör inga hårda länkar till filer utanför /data.
    if [ -n "$(find "$DATA" -xdev \( -type d -o \( -type f -links 1 \) \) \( ! -user "$PUID" -o ! -group "$PGID" \) -print -quit 2>/dev/null)" ]; then
        echo "[start] ger $DATA ägaren $PUID:$PGID"
        find "$DATA" -xdev \( -type d -o \( -type f -links 1 \) \) \( ! -user "$PUID" -o ! -group "$PGID" \) \
            -exec chown -h "$PUID:$PGID" {} + || echo "[varning] kunde inte ändra ägare på allt i $DATA"
    fi
    if ! su-exec "$PUID:$PGID" true 2>/dev/null; then
        echo "=================================================================="
        echo "[FEL] Containern saknar rättigheten att byta användare (SETUID/SETGID)."
        echo "      I Unraid: Edit -> Advanced View -> Extra Parameters, ersätt med:"
        echo "      --read-only --tmpfs /tmp --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID --cap-add DAC_OVERRIDE --security-opt no-new-privileges"
        echo "      och klicka Apply."
        echo "=================================================================="
        exit 1
    fi
    exec su-exec "$PUID:$PGID" "$@"
fi
exec "$@"
