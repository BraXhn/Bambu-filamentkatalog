# Bambu filamentkatalog

Färgkatalog och prisjämförelse för Bambu Lab-filament, med inloggning och användarkonton. Priserna från 3DJake, 3D Prima och Tradera uppdateras automatiskt varje natt. Allt körs i en enda Docker-container.

- **Katalog:** 229 färgvarianter inom 38 filamenttyper, med sökning, filter, kort- och tabellvy och CSV-export.
- **Prisjämförelse:** pris inklusive frakt och köparskydd, uppdelat på vikt och refill/spole. Auktioner visas separat.
- **Användare:** varje användare har sina egna ”Jag har detta filament”-markeringar, sparade på kontot och synliga på alla enheter.
- **Synk från Bambu Lab:** koppla ditt Bambu Lab-konto så markeras spolarna i ditt filamentbibliotek automatiskt som ”Jag har”, med gram kvar per färg.
- **Administration i webbläsaren:** lägg till och ta bort användare, återställ lösenord, lås upp konton, se prisuppdateringarnas status och starta en uppdatering direkt.

## Kom igång

```
docker compose up -d
```

1. Öppna `http://<serverns-adress>:8000`.
2. Första gången visas *Välkommen!*. Ange **installationskoden** från containerns logg (`docker logs katalog`, eller loggfönstret i Unraid eller Portainer) och skapa ditt administratörskonto.
3. Lägg till fler användare under **Administration**. Lämnar du lösenordet tomt slumpas ett, som visas en gång. Användaren byter det vid första inloggningen.

Imagen finns färdigbyggd för amd64 och arm64 som `ghcr.io/braxhn/bambu-filamentkatalog:latest`. Den byggs och testas automatiskt vid varje ändring i `main`. Vill du bygga själv kör du `docker compose up -d --build`.

> Är paketet privat på GitHub behöver servern `docker login ghcr.io`. Du kan också göra paketet publikt under *Packages → bambu-filamentkatalog → Package settings*.

### Unraid

Det finns en färdig mall i [`unraid/bambu-filamentkatalog.xml`](unraid/bambu-filamentkatalog.xml). Kör detta i Unraids terminal:

```
mkdir -p /mnt/user/appdata/bambu-filamentkatalog
chown -R 101:101 /mnt/user/appdata/bambu-filamentkatalog
wget -O /boot/config/plugins/dockerMan/templates-user/my-bambu-filamentkatalog.xml \
  https://raw.githubusercontent.com/BraXhn/Bambu-filamentkatalog/main/unraid/bambu-filamentkatalog.xml
```

Gå sedan till **Docker → Add Container** och välj mallen *bambu-filamentkatalog* under **Template**. Klicka **Apply**.

- **Delad IP** (nätverkstyp *Bridge*): katalogen nås på `http://<unraid-ip>:<porten i mallen>`.
- **Egen IP** (nätverkstyp *Custom: br0* eller liknande): katalogen nås alltid på `http://<containerns-ip>:8080`. Portfältet används då inte.

Containern kör som användare `101`, så datamappen måste ägas av `101:101`. Det gör `chown`-raden ovan. Containern säger till i loggen om den saknar skrivrätt.

## Exponera mot internet

Containern är byggd för att stå bakom en omvänd proxy som sköter HTTPS, till exempel Nginx Proxy Manager, SWAG, Traefik, Caddy eller Cloudflare Tunnel. Exponera inte port 8000 direkt utan TLS.

1. Peka proxyn mot `http://<container>:8080`. Proxyn ska skicka `Host`, `X-Forwarded-For` och `X-Forwarded-Proto`. Det gör alla de nämnda som standard.
2. Sätt **`TRUSTED_PROXIES`** till proxyns adress eller Docker-nät, till exempel `172.18.0.0/16`. Annars ser containern alla besökare som samma IP-adress. Det gör begränsningen av inloggningsförsök gemensam för alla, och https känns inte igen.
3. Sätt **`COOKIE_SECURE=true`** när sajten bara nås via https.

Skydd som är inbyggt:

- **Behörighet:** allt utom inloggningssidan kräver inloggning. Det gäller även `data.json`, `offers.json` och JavaScript-filerna. nginx kontrollerar sessionen inför varje fil.
- **Lösenord:** hashas med scrypt. Sessionstoken lagras bara som hash. Cookies är HttpOnly och SameSite=Lax, och Secure när sajten nås via https.
- **Formulär:** CSRF-token och kontroll av ursprung (Origin) på alla formulär och API-anrop.
- **Begränsning av inloggningsförsök:**
  - nginx tillåter högst 10 försök per minut och IP-adress.
  - Appen tillåter högst 20 misslyckade försök per kvart och IP-adress.
  - Ett konto spärras i 15 minuter efter 8 felaktiga försök.
  - Svaret är detsamma oavsett om kontot finns, så svaret avslöjar inga användarnamn.
- **Säkerhetsrubriker:** strikt Content-Security-Policy, HSTS bakom https, X-Frame-Options, `noindex`. nginx-versionen döljs.
- **Container:** kör som icke-root med skrivskyddat filsystem och utan Linux-capabilities. Den behöver bara `/data` och `/tmp`.
- **Prishämtaren:** hämtar bara via https från `www.3djake.se`, `www.3dprima.com` och `www.tradera.com`. Den publicerar bara länkar till dessa.
- **Bambu-synken:** pratar bara med Bambu Labs egna adresser och GitHub (färgkodstabellen). Högst 6 inloggnings- eller synkförsök per kvart och användare, så att ditt Bambu-konto inte spärras av misstag.

## Prisuppdatering

- Körs varje natt kl. 01.00 svensk tid (`PRICE_UPDATE_TIME`, `TZ`), med sommar- och vintertid. En administratör kan också starta den med **Uppdatera priser nu**.
- **3DJake:** produktsidorna läses för pris och lagerstatus per variant (Spole/Refill). Nya färger hittas automatiskt via länkar mellan produkterna.
- **3D Prima:** kända produktsidor läses för pris och lagerstatus.
- **Tradera:** sökningen *bambu lab filament* läses. Bara aktiva annonser från svenska säljare som matchar en färg i katalogen tas med. Annonssidan läses för köp nu-pris, frakt, obligatoriskt köparskydd, skick, vikt och refill/spole.
- Om en källa inte svarar, eller plötsligt ger noll träffar när den tidigare gav många, behålls dess tidigare priser. Statusen visas under Administration.
- En körning tar ungefär 10–15 minuter, eftersom anropen sprids ut för att inte belasta butikerna.
- Flödet *Kontrollera prishämtning* i GitHub Actions kör hämtningen varje måndag mot de riktiga sidorna. Det varnar om en butik har ändrat sitt sidformat.

Lägst hittat betyder lägst bland de kontrollerade erbjudandena, inte garanterat marknadens lägsta pris. Produktbilder och produktuppgifter kommer från respektive leverantör.

## Synk från ditt Bambu Lab-filamentbibliotek

Under **Mitt konto → Bambu Lab-konto** kan varje användare koppla sitt Bambu Lab-konto.

1. Ange e-post och lösenord hos Bambu Lab.
2. Ange koden från e-post, eller från autentiseringsappen om du har tvåstegsverifiering.

Därefter hämtas spolarna i filamentbiblioteket, alltså det du ser under *Filament* i Bambu Handy och Bambu Studio.

- **Bara läsning:** ingenting ändras någonsin i ditt Bambu-konto.
- **Matchning:** spolarna kopplas till katalogens färger via Bambu Studios publika färgkodstabell (`filamentId` + färg → färgnummer). Den hämtas och cachas i `/data/state`. Spolar som inte finns i katalogen, till exempel andra tillverkare, listas separat.
- **I katalogen:** färgerna visas som *Bambu-biblioteket · 640 g kvar* och räknas in i *Mina filament* och i CSV-exporten. Tomma och arkiverade spolar räknas inte. Dina egna markeringar finns kvar som vanligt.
- **Synkas:** var sjätte timme (`BAMBU_SYNC_HOURS`), när du klickar *Synka nu* och direkt när du kopplar kontot.
- **Lösenordet** skickas bara till Bambu Lab och sparas inte. Inloggningen (token) gäller cirka 90 dagar och kan inte förnyas automatiskt. När den gått ut visas det i katalogen och på Mitt konto. Du loggar då in igen. Under tiden visas senast synkade spolar.

> Bambu Lab har inget officiellt API för filamentbiblioteket. Synken använder samma anrop som Bambu Studio och Bambu Handy (dokumenterade av [OpenBambuAPI](https://github.com/Doridian/OpenBambuAPI)) och kan sluta fungera om Bambu ändrar något. Då visas ett tydligt fel och resten av katalogen påverkas inte.

## Inställningar (miljövariabler)

| Variabel | Standard | Beskrivning |
|---|---|---|
| `TZ` | `Europe/Stockholm` | Tidszon för schemat och visade tider |
| `PRICE_UPDATE_TIME` | `01:00` | När priserna uppdateras. Tom stänger av schemat. |
| `PRICE_UPDATE_ON_START` | `false` | Uppdatera direkt när containern startar |
| `PRICE_SOURCES` | `3djake,3dprima,tradera` | Vilka källor som används |
| `TRUSTED_PROXIES` | loopback | Omvända proxyer vars `X-Forwarded-*` litas på (kommaseparerade nät) |
| `COOKIE_SECURE` | `auto` | `true` = alltid Secure-cookie, `auto` = när proxyn anger https |
| `SESSION_IDLE_DAYS` / `SESSION_MAX_DAYS` | `14` / `60` | Utloggning efter inaktivitet eller maxtid |
| `PASSWORD_MIN_LENGTH` | `10` | Minsta lösenordslängd |
| `BAMBU_SYNC_HOURS` | `6` | Hur ofta kopplade Bambu Lab-konton synkas (0 = bara manuellt) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | – | Skapar första administratören automatiskt i stället för installationskod |

## Kommandon i containern

```
docker exec katalog python3 -m katalog users
docker exec -it katalog python3 -m katalog add-user lisa [--admin]
docker exec katalog python3 -m katalog reset-password lisa
docker exec katalog python3 -m katalog unlock lisa
docker exec katalog python3 -m katalog update
```

## Säkerhetskopiering

All data finns i `/data`:

| Sökväg | Innehåll |
|---|---|
| `katalog.db` | Användare, sessioner, markeringar, körhistorik och Bambu-inloggningar (filen är bara läsbar för containern) |
| `www/offers.json` | Senaste priserna |
| `state/` | Kända produktlänkar |

Kopiera mappen när containern är stoppad, eller använd `sqlite3 katalog.db ".backup kopia.db"`.

## Projektstruktur

| Sökväg | Innehåll |
|---|---|
| `dist/` | Webbappen (HTML, CSS, JS) och inbyggd data |
| `katalog/` | Server: inloggning (`web.py`, `security.py`, `db.py`), sidor (`pages.py`), Bambu Lab-synk (`bambu.py`), nginx-konfiguration (`nginx.py`), schemaläggning (`scheduler.py`) |
| `katalog/prices/` | Prishämtare per källa och sammanslagning (`update.py`) |
| `assets/` | Stil och ikon för inloggnings- och adminsidorna |
| `tests/` | Enhetstester och integrationstest mot containern |
| `build_catalog.py`, `build_prices.py`, `prices.py`, `sources/` | Ursprungliga byggskript och sparade underlag för färgkatalogen. Bygg om med `--build-arg REBUILD_DATA=true`. |
| `start.py`, `Starta-Windows.bat` | Enkel lokal visning utan inloggning och prisuppdatering |

Utveckling utan Docker: `KATALOG_DATA_DIR=./data KATALOG_SEED_DIR=./dist KATALOG_START_NGINX=false python3 -m katalog serve` startar appen på 127.0.0.1:9000. Testerna körs med `python3 -m unittest discover -s tests -p 'test_*.py'`.
