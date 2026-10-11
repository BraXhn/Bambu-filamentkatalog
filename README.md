# Bambu filamentkatalog

Färgkatalog och prisjämförelse för Bambu Lab-filament, med inloggning och användarkonton. Priserna från 3DJake, 3D Prima och Tradera uppdateras automatiskt varje natt. Allt körs i en enda Docker-container.

- **Katalog:** 229 färgvarianter inom 38 filamenttyper, med sökning, filter, kort- och tabellvy och CSV-export.
- **Prisjämförelse:** pris inklusive frakt och köparskydd, uppdelat på vikt och refill/spole. Auktioner visas separat.
- **Inköpslista:** lägg filament i en lista. Sidan *Inköpslista* visar var varje filament bör köpas, med direktlänk, och en uppskattad totalsumma inklusive frakt. Köpen fördelas så att totalen blir så låg som möjligt, med hänsyn till fri frakt och samfrakt.
- **Billigast och erbjudanden:** sortera på lägsta aktuella pris eller största rabatt, och filtrera fram aktuella erbjudanden med rabatt i procent mot ordinarie pris.
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

Det finns en färdig mall i [`unraid/bambu-filamentkatalog.xml`](unraid/bambu-filamentkatalog.xml). Hämta den med en rad i Unraids terminal:

```
wget -O /boot/config/plugins/dockerMan/templates-user/my-bambu-filamentkatalog.xml \
  https://raw.githubusercontent.com/BraXhn/Bambu-filamentkatalog/main/unraid/bambu-filamentkatalog.xml
```

Gå sedan till **Docker → Add Container** och välj mallen *bambu-filamentkatalog* under **Template**. Klicka **Apply**.

- **Rekommenderat: nätverkstyp *Bridge*.** Katalogen nås på `http://<unraid-ip>:<porten i mallen>` (standard 8765).
- **Egen IP** (*Custom: br0* eller liknande): katalogen nås på `http://<containerns-ip>:8080`. Välj en adress utanför routerns DHCP-intervall, annars kan den krocka med en annan enhet. Använd hellre *ipvlan* än *macvlan* (*Settings → Docker*).
- **Datamappen** (`/mnt/user/appdata/bambu-filamentkatalog`) skapas och får rätt ägare automatiskt. Containern startar som root bara för att byta ägare på `/data`. Sedan släpper den alla rättigheter och kör som 99:100 (`PUID`/`PGID`, Unraids *nobody:users*).
- **Loggen** visar en startruta med adressen att öppna, och en rad för varje sidvisning. Syns ingen rad när du försöker öppna sidan, når förfrågan aldrig containern. Felet ligger då i nätverket.

## Exponera mot internet

Låt en omvänd proxy sköta HTTPS, till exempel Nginx Proxy Manager, SWAG, Traefik, Caddy eller Cloudflare Tunnel. Exponera inte porten direkt utan TLS.

1. Peka proxyn mot `http://<container>:8080`. Proxyn ska skicka `Host`, `X-Forwarded-For` och `X-Forwarded-Proto`. Det gör alla de nämnda som standard.
2. Sätt **`TRUSTED_PROXIES`** till **proxyns egen adress**, till exempel `172.18.0.5/32`. Ange inte ett helt nät: alla enheter i ett betrott nät kan låtsas ha en annan IP-adress. Saknas inställningen varnar loggen och föreslår rätt värde.
3. Sätt **`COOKIE_SECURE=true`**. Sätt gärna också **`ALLOWED_HOSTS`** till ditt domännamn, så att andra värdnamn nekas.

### Cloudflare Tunnel

1. Lägg till ett **Public Hostname** i Cloudflare Zero Trust (*Networks → Tunnels → din tunnel*), till exempel `katalog.dindomän.se`. Ange tjänsten `http://<unraid-ip>:8765`.
2. Öppna katalogen via `https://katalog.dindomän.se` och titta i containerns logg. Där står en varning som slutar med `Ange då TRUSTED_PROXIES=…/32`. Det är cloudflared-containerns adress.
3. Lägg till följande i containerns inställningar och klicka **Apply**:

| Variabel | Värde |
|---|---|
| `TRUSTED_PROXIES` | adressen från loggen, t.ex. `172.17.0.5/32` |
| `COOKIE_SECURE` | `true` |
| `ALLOWED_HOSTS` | `katalog.dindomän.se,<unraid-ip>` (ta med Unraid-IP:n så att den fortsätter fungera hemma) |

Har cloudflared-containern ingen fast adress kan den ändras vid omstart. Ge den då en fast IP i Unraid. Ett alternativ är att lägga båda containrarna i ett eget Docker-nät och ange `http://bambu-filamentkatalog:8080` som tjänst.

Vill du ha ett extra lager, kan du lägga **Cloudflare Access** framför: *Access → Applications → Self-hosted*, med e-postinloggning för dig själv. Katalogens egen inloggning gäller ändå.

Skydd som är inbyggt:

- **Behörighet:** allt utom inloggningssidan kräver inloggning. Det gäller även `data.json`, `offers.json` och JavaScript-filerna.
- **Lösenord och sessioner:** lösenord hashas med scrypt. Sessionstoken lagras bara som hash. Cookies är HttpOnly och SameSite=Lax, och Secure när sajten nås via https.
- **Formulär:** CSRF-token och kontroll av ursprung (Origin) på alla formulär och API-anrop.
- **Begränsning av inloggningsförsök:**
  - Högst 10 försök per minut och IP-adress.
  - Högst 20 misslyckade försök per kvart och IP-adress.
  - 8 felaktiga försök per kvart spärrar kontot **från den adressen**. Ägaren kan fortfarande logga in från sin egen.
  - Vid storskalig gissning (50 fel i rad) spärras kontot helt i 15 minuter.
  - Svaret är detsamma oavsett om kontot finns.
- **Skydd mot överbelastning:**
  - Strikt kontroll av `Content-Length`. Chunkade förfrågningar nekas.
  - Rubriker får vara högst 16 KB och måste skickas inom 10 sekunder.
  - Högst 16 samtidiga anslutningar per IP-adress och 128 totalt.
  - Vilande anslutningar stängs efter 10 sekunder.
- **Säkerhetsrubriker:** strikt Content-Security-Policy, HSTS bakom https, X-Frame-Options, `noindex`.
- **Container:**
  - Skrivskyddat filsystem.
  - Appen kör som 99:100 utan Linux-capabilities.
  - Bara de fyra som behövs för ägarbytet vid start är tillåtna, och de släpps innan appen startar.
  - Ägarbytet följer inga länkar.
- **Prishämtaren:** hämtar bara via https från `www.3djake.se`, `www.3dprima.com` och `www.tradera.com`. Den publicerar bara länkar till dessa.
- **Bambu-synken:** pratar bara med Bambu Labs egna adresser och GitHub (färgkodstabellen). Högst 6 inloggnings- eller synkförsök per kvart och användare.

**Prestanda:** katalogen ligger i minnet, förkomprimerad, med ETag och keep-alive. Belastningstestet i CI ger cirka 1 500 förfrågningar per sekund med medianen 6 ms. Processen använder cirka 30 MB minne.

## Prisuppdatering

- Körs varje natt kl. 01.00 svensk tid (`PRICE_UPDATE_TIME`, `TZ`), med sommar- och vintertid. En administratör kan också starta den med **Uppdatera priser nu**.
- **3DJake:** produktsidorna läses för pris och lagerstatus per variant (Spole/Refill). Nya färger hittas automatiskt via länkar mellan produkterna.
- **3D Prima:** kända produktsidor läses för pris och lagerstatus.
- **Bambu Lab EU** (`eu.store.bambulab.com`):
  - Butikens produkt-API läses, med pris, ordinarie pris och lagerstatus per färg, spole/refill och storlek.
  - Priserna är i euro och räknas om till kronor med ECB:s dagskurs.
  - Butiken svarar bara europeiska besökare. Det fungerar från en server i Sverige, men inte från GitHub Actions.
  - Frakten visas först i deras kassa. Inköpslistan räknar med 99 kr; justera med `BAMBU_SHIPPING_SEK` och `BAMBU_FREE_FROM_SEK`.
- **Tradera:** sökningen *bambu lab filament* läses. Bara aktiva annonser från svenska säljare som matchar en färg i katalogen tas med. Annonssidan läses för köp nu-pris, frakt, obligatoriskt köparskydd, skick, vikt och refill/spole.
- Om en källa inte svarar, eller plötsligt ger noll träffar när den tidigare gav många, behålls dess tidigare priser. Statusen visas under Administration.
- En körning tar ungefär 10–15 minuter, eftersom anropen sprids ut för att inte belasta butikerna.
- Flödet *Kontrollera prishämtning* i GitHub Actions kör hämtningen varje måndag mot de riktiga sidorna. Det varnar om en butik har ändrat sitt sidformat.

**Ordinarie pris och rabatt:**
- I första hand används butikens egen uppgift: Traderas pris före rabatt, 3D Primas listpris, Bambu Labs jämförpris (`compare_at_price`), eller ett överstruket pris hos 3DJake.
- Annars används det typiska priset (medianen) för samma erbjudande de senaste 30 dagarna. Det kräver minst 7 dagars historik, så sådana rabatter syns först efter ungefär en vecka.
- Bara rabatter på minst 5 % visas.
- Prishistoriken sparas i `/data/state/price_history.json`.

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
| `PRICE_SOURCES` | `3djake,3dprima,tradera,bambulab` | Vilka källor som används |
| `BAMBU_SHIPPING_SEK` / `BAMBU_FREE_FROM_SEK` | `99` / – | Bambu Lab EU:s frakt och gräns för fri frakt i inköpslistan |
| `TRUSTED_PROXIES` | loopback | Omvända proxyer vars `X-Forwarded-*` litas på. Ange proxyns adress som `/32`. |
| `COOKIE_SECURE` | `auto` | `true` = alltid Secure-cookie, `auto` = när webbläsaren använder https |
| `ALLOWED_HOSTS` | – | Tillåtna värdnamn, kommaseparerade (t.ex. `katalog.example.se`). Tomt = alla. |
| `PUID` / `PGID` | `99` / `100` | Användare och grupp som appen kör som |
| `SESSION_IDLE_DAYS` / `SESSION_MAX_DAYS` | `14` / `60` | Utloggning efter inaktivitet eller maxtid |
| `PASSWORD_MIN_LENGTH` | `10` | Minsta lösenordslängd |
| `BAMBU_SYNC_HOURS` | `6` | Hur ofta kopplade Bambu Lab-konton synkas (0 = bara manuellt) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | – | Skapar första administratören automatiskt i stället för installationskod |

## Kommandon i containern

```
docker exec bambu-filamentkatalog python -m katalog users
docker exec -it bambu-filamentkatalog python -m katalog add-user lisa [--admin]
docker exec bambu-filamentkatalog python -m katalog reset-password lisa
docker exec bambu-filamentkatalog python -m katalog unlock lisa
docker exec bambu-filamentkatalog python -m katalog update
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
| `katalog/` | Server (`web.py`, statiska filer i `static.py`), inloggning (`security.py`, `db.py`), sidor (`pages.py`), Bambu Lab-synk (`bambu.py`), schemaläggning (`scheduler.py`) |
| `docker/entrypoint.sh` | Ger `/data` rätt ägare och startar appen som PUID:PGID |
| `katalog/prices/` | Prishämtare per källa och sammanslagning (`update.py`) |
| `assets/` | Stil och ikon för inloggnings- och adminsidorna |
| `tests/` | Enhetstester och integrationstest mot containern |
| `build_catalog.py`, `build_prices.py`, `prices.py`, `sources/` | Ursprungliga byggskript och sparade underlag för färgkatalogen. Bygg om med `--build-arg REBUILD_DATA=true`. |
| `start.py`, `Starta-Windows.bat` | Enkel lokal visning utan inloggning och prisuppdatering |

Utveckling utan Docker: `KATALOG_DATA_DIR=./data KATALOG_SEED_DIR=./dist PORT=8080 python3 -m katalog serve`. Testerna körs med `python3 -m unittest discover -s tests -p 'test_*.py'`.
