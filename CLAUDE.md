# Bambu filamentkatalog

Färgkatalog, prisjämförelse och inköpslista för Bambu Lab-filament, med användarkonton och envägssynk
från användarens Bambu Lab-filamentbibliotek. Körs som en enda Docker-container (Unraid hos ägaren,
nås utifrån via Cloudflare Tunnel). Användaren skriver svenska; allt i gränssnittet, loggar,
commit-meddelanden och svar till användaren är på svenska.

## Arkitektur

- **Bara Python-stdlib** (inga pip-beroenden i runtime). En process: `ThreadingHTTPServer` med
  HTTP/1.1 keep-alive i `katalog/web.py`. Lägg inte till ramverk eller extra processer (nginx togs bort med flit).
- `katalog/web.py` – routing, förfrågningsvalidering (strikt Content-Length, inga Transfer-Encoding,
  16 KB rubriker, 10 s deadline per förfrågan via `_ConnReader`), anslutningsgränser per IP, CSRF, sessioner.
- `katalog/pages.py` – serverrenderade sidor (inloggning, konto, admin, inköpslista). Escapea allt med `e()`.
- `katalog/static.py` – webbappen i minnet (gzip, ETag). `index.html` skrivs om till `app.js?v=<hash>`
  och serveras `no-store`; versionerade filer är `immutable`. Ändrar du `dist/*.js` behövs inget mer.
- `dist/` – katalogdata (`data.json`), webbapp (`index.html`, `app.js`, `prices-ui.js`) och seed `offers.json`.
- `katalog/db.py` – SQLite (DELETE-journal, inte WAL: Unraids FUSE klarar inte WAL). Tabeller:
  users, sessions (bara token-hash), owned, cart, runs, settings, bambu_links, bambu_pending.
- `katalog/security.py` – scrypt, rate limiters. `katalog/config.py` – alla miljövariabler med säkra standardvärden.
- `katalog/bambu.py` – inofficiellt Bambu-moln-API (inloggning med e-postkod/TFA, filamentbibliotek), matchning
  mot katalogen. Testas mot `tests/fake_bambu.py` via `KATALOG_BAMBU_TEST_BASE`.
- `katalog/scheduler.py` – prisuppdatering 01:00, Bambu-synk var 6:e timme, rensning av sessioner.
- `katalog/cart.py` – inköpslistans planerare: väljer butik per enhet så att totalen inkl. frakt blir lägst
  (girig start + enkelflyttar + samlingsdrag). Fraktregler i `SHOPS`. Mängdrabatter: Bambu Lab EU
  (fasta €-nivåpriser per filamenttyp, `BAMBU_BULK_*`) och procentnivåer per produkt från erbjudandets
  `bulk`/`bulkGroup` (3DVerkstan). Rabatten ska alltid redovisas tydligt i inköpslistan.

## Prishämtning (`katalog/prices/`)

En modul per källa med `run(http, matcher, state, log, now) -> [offer]`, registrerad i `update.py:SOURCES`
och i `config.PRICE_SOURCES`. Alla anrop går via `common.Http` som bara tillåter värdar i
`common.ALLOWED_HOSTS` (lägg till nya butiker där) och följer bara omdirigeringar inom listan.

| Källa | Modul | Hur |
|---|---|---|
| 3DJake | `jake.py` | JSON-LD per variant (`sai`), hittar syskonfärger via länkar |
| 3D Prima | `prima.py` | kända produktsidor (seed_state), JSON-LD med listpris |
| Tradera | `tradera.py` | Next.js flight-data (`self.__next_f.push`), annonssidor |
| Bambu Lab EU | `bambustore.py` | produktsidornas JSON-LD ProductGroup; EUR→SEK via ECB. `/products.json` är avstängt hos Bambu |
| 3DVerkstan | `verkstan.py` | WooCommerce Store API + `data-product_variations`; priser **exkl. moms** (×1,25); färg via Bambus artikelnummer (`bambu_skus.json`) |
| 3DExperten | `experten.py` | Shopify `products.json` (samlingar med "bambu"); svenska färgnamn översätts; hela färgnamnet måste stämma |

Erbjudandeformat: `key` (`"<typ>|<färgkod>"`), `shop`, `title`, `url`, `price` (kr inkl. moms), `listPrice`,
`shipping`, `fee`, `weight` (g), `pack` (`Spole`/`Refill`), `kind` (`fixed`), `condition`, `endAt`,
`shippingNote`, `sourceNote`, `checkedAt`, valfritt `fx`, `bulk`, `bulkGroup`.
Hellre hoppa över en vara än matcha fel färg. En källa som fallerar eller plötsligt ger 0 behåller sina
tidigare priser. `apply_history()` räknar ordinarie pris (butikens listpris, annars 30-dagarsmedian) och rabatt.

## Kommandon

```bash
python3 -m unittest discover -s tests -p 'test_*.py'      # enhetstester (snabba, kör alltid)
KATALOG_DATA_DIR=/tmp/kd KATALOG_SEED_DIR=dist python3 -m katalog serve   # lokal server
python3 tests/integration.py http://127.0.0.1:8080         # integrationstest mot körande server
python3 -m katalog update --sources 3dverkstan,3dexperten  # prishämtning (kräver nätåtkomst till butikerna)
```

Döda bara egna serverprocesser med `pkill -f '^python3 -m katalog'` (inte `pkill -f katalog`).

## CI, publicering och drift

- `.github/workflows/ci.yml`: enhetstester, integrationstest, soak-test, Unraid-simulering (macvlan),
  publicerar `ghcr.io/braxhn/bambu-filamentkatalog:latest` (amd64+arm64) från `main`.
  Användaren uppdaterar med **Force update** i Unraid; påminn om det efter ändringar.
- `.github/workflows/prices.yml`: hämtning mot riktiga butiker varje måndag (kan köras manuellt med
  `gh api -X POST repos/BraXhn/Bambu-filamentkatalog/actions/workflows/prices.yml/dispatches -f ref=main`).
  Bambu Lab EU svarar inte GitHubs amerikanska servrar, så den källan är inte med där.
- Container: `python:3.13-alpine` + tini + su-exec, startar som root bara för att chowna `/data` till
  PUID:PGID (99:100), kör sedan som den användaren. Read-only rotfilsystem, `--cap-drop ALL` +
  CHOWN/SETUID/SETGID/DAC_OVERRIDE. Unraid-mall i `unraid/`.
- Om sandlådan inte når en butik: grenen `probe` har ett arbetsflöde som hämtar sidor och pushar
  resultatet till grenen `probe-results` (klona den för att läsa). Annoteringar läses med
  `gh api repos/.../check-runs/<job-id>/annotations` eftersom loggar/artefakter kan vara blockerade.

## Säkerhet (behåll)

Appen exponeras mot internet. Behåll: scrypt, sessionstoken bara som hash, CSRF (token + Origin),
inloggningsgränser per IP och konto+IP (fördröjning, inget globalt lås), identiska svar vid fel
användarnamn/lösenord, `TRUSTED_PROXIES` (standard loopback), `ALLOWED_HOSTS`, host-allowlist för
skrapare, escaping i både server- och klientkod (`esc`, `safeUrl`, `hexOk` i JS).
`/healthz` och loopback undantas från `ALLOWED_HOSTS` (healthcheck).

## Konventioner

- Uppdatera README.md (svenska) när beteende eller miljövariabler ändras.
- Lägg till/uppdatera tester i `tests/` för ny logik; syntetiska fixtures, inga nätanrop i enhetstester.
- Sidfoten ska innehålla "Created by Helicon & BraXhn".
