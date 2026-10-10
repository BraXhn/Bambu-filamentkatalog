# Bambu filamentkatalog – projektkod

Export av den publicerade versionen med färgkatalog, egna filamentmarkeringar och prisjämförelse, 10 oktober 2026.

## Starta i Windows

1. Packa upp hela ZIP-filen.
2. Ha Python 3.10 eller senare installerat. Inga extra Python-paket behövs.
3. Dubbelklicka på `Starta-Windows.bat`.
4. Katalogen öppnas på http://127.0.0.1:8000. Låt terminalfönstret vara öppet medan du använder den. Avsluta med Ctrl+C.

Alternativt: öppna en terminal i projektmappen och kör `python start.py` (eller `python3 start.py` på macOS/Linux).

Öppna inte index.html direkt genom dubbelklickning: webbläsaren behöver en lokal webbserver för att läsa JSON-filerna.

## Köra med Docker

```
docker compose up -d --build
```

Katalogen finns sedan på http://localhost:8000. Utan Compose:

```
docker build -t bambu-filamentkatalog .
docker run -d --name katalog -p 8000:8080 --read-only --tmpfs /tmp --cap-drop ALL bambu-filamentkatalog
```

Bygg om `data.json`/`offers.json` från `sources/` under bygget: `docker build --build-arg REBUILD_DATA=true -t bambu-filamentkatalog .`

Efter ändringar i `dist/` bygger du om imagen (`docker compose up -d --build`).

Optimeringar i imagen:

- Multi-stage-bygge: Python, Node och `sources/` (17 MB) finns bara i byggstegen. Slutimagen är nginx (alpine-slim) plus ca 300 KB data.
- JSON kompakteras och JS minifieras med esbuild. Alla filer förkomprimeras med zopfli och nginx skickar `.gz` direkt (`gzip_static`), så ingen komprimering sker per anrop. Överföringen minskar från ca 272 KB till ca 30 KB.
- Körs som icke-root på port 8080 och fungerar med skrivskyddat filsystem. Healthcheck finns på `/healthz`.
- Säkerhetsheaders med strikt Content-Security-Policy. Den inbäddade `onerror`-hanteraren för produktbilder i `app.js` är därför ersatt med en vanlig händelselyssnare.
- `Cache-Control: no-cache` med ETag: webbläsaren får `304` när inget ändrats, och ny data syns direkt efter omstart med ny image.

## Innehåll

- `dist/index.html`: gränssnitt och CSS.
- `dist/app.js`: sökning, materialfilter, kort/tabell, CSV-export och ägandemarkeringar.
- `dist/prices-ui.js`: prisdialog, jämförelsegrupper, frakt och annonsers sluttider.
- `dist/data.json`: 229 färgvarianter inom 38 filamenttyper.
- `dist/offers.json`: daterade prisuppgifter och källänkar.
- `start.py`, `Starta-Windows.bat`: lokal start.
- `build_catalog.py`: skapar katalogdata från sparade produktuppgifter.
- `prices.py`: matchar erbjudanden mot katalogens material och färger.
- `build_prices.py`: skapar prisdata från sparade underlag.
- `sources/`: underlagen som dessa skript använder.

Webbappen är vanlig HTML, CSS och JavaScript. Den behöver ingen installation av npm-paket och inget byggsteg. För egen webbhosting laddar du upp innehållet i `dist/` till ett webbhotell för statiska filer.

## Data och begränsningar

Priserna är en ögonblicksbild från 10 oktober 2026, inte liveuppdaterade. Att starta programmet eller köra byggskripten söker inte efter nya priser. Exportens byggskript behåller ursprungligt kontrolldatum när det använder sparade prisunderlag.

Prisjämförelsen omfattar verifierade erbjudanden från svenska butikssidor och Tradera. Bambu Labs EU-pris kunde inte verifieras; en direktlänk visas. Lägst hittat betyder lägst bland de jämförbara erbjudanden som finns i datan, inte garanterat marknadens lägsta pris. Vikt, refill/spole, skick och kända fraktkostnader påverkar jämförelsen. Förfallna Tradera-annonser döljs utifrån sluttiden, men tidigare försäljning eller prisändringar kan inte upptäckas offline.

Produktbilder hämtas från externa bildadresser och kräver internet. Bilder och produktuppgifter kommer från respektive leverantör; denna export innebär inte att tredjepartsbilder eller varumärken får återanvändas utan begränsning.

Markeringarna ”Jag har detta filament” sparas i webbläsarens localStorage, separat för varje webbplatsadress. Markeringar från den publicerade katalogen följer därför inte automatiskt med till den lokala kopian. De försvinner om webbplatsdata rensas. CSV-exporten innehåller kolumnen ”Jag har”.

## Ändra data

Redigera `dist/data.json` och `dist/offers.json` direkt för enkla ändringar. Priser matchas med nyckeln `filamenttyp|färgnummer`, alternativt färgnamn när nummer saknas. Exempel: `PLA Basic|10101`.

För att bygga om från de medföljande underlagen:

```
python build_catalog.py
python build_prices.py
```

Gör en kopia av egna ändringar först: skripten skriver över JSON-filerna i dist. För ny prissökning krävs nya verifierade underlag och ett verkligt nytt kontrolldatum.

Den publicerade tjänstens Git-historik, inloggningsuppgifter och hostingkopplingar ingår inte. Den här koden kan köras självständigt.

## Daglig uppdatering kl. 01.00

Den publicerade webbplatsen har en aktiverad molnuppgift som startar prissökning dagligen kl. 01.00 i tidszonen Europe/Stockholm. Första schemalagda körningen är den 11 oktober 2026. Schemat följer svensk sommar- och vintertid. Sidan behöver inte vara öppen.

Inställningarna finns i `schema-prisuppdatering.json`. Molnuppgiften söker nya uppgifter och uppdaterar den publicerade webbplatsen. JSON-filen är en export av inställningarna, inte ett körbart lokalt uppdateringsprogram. Den lokala ZIP-kopian uppdateras inte av molnuppgiften. Den innehåller en ögonblicksbild av katalog och priser vid exporten.

Detta paket innehåller alltså webbappens källkod och data, men inte ChatGPTs molnkörmiljö eller en fristående söktjänst. Om projektet flyttas till en annan server måste en separat prissökning och schemaläggare implementeras där.
