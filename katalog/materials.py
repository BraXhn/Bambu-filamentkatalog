"""Materialguide: riktvärden per filamenttyp (temperaturer, munstycke, AMS, kammare, torkning och stödmaterial).

Värdena är allmänna riktvärden för Bambu Labs filament; Bambu Studios filamentprofil för din skrivare gäller i första hand.
`open_days` är hur länge en öppnad spole klarar sig i vanlig inomhusluft innan torkning rekommenderas.
"""

# Grundmaterial (matchas mot början av katalogens typnamn, längsta träff vinner).
BASE = {
    'PLA':  dict(nozzle=(190, 230), bed=(35, 65), hardened=False, ams='Ja', enclosure='Nej (lucka/lock av ger bäst resultat)',
                 dry=(55, 8), open_days=90, support=['Support for PLA', 'PETG som gränsskikt'], note='Lättast att skriva ut.'),
    'PETG': dict(nozzle=(230, 270), bed=(65, 80), hardened=False, ams='Ja', enclosure='Nej',
                 dry=(65, 8), open_days=30, support=['Support for PLA/PETG', 'PLA som gränsskikt'],
                 note='Tar upp fukt; trådar och bubblor tyder på att spolen bör torkas. Lim/textur-platta rekommenderas.'),
    'PET':  dict(nozzle=(260, 290), bed=(70, 100), hardened=True, ams='Ja', enclosure='Rekommenderas',
                 dry=(80, 8), open_days=14, support=['Support for PA/PET'], note=''),
    'ABS':  dict(nozzle=(240, 280), bed=(80, 100), hardened=False, ams='Ja', enclosure='Krävs (sluten skrivare)',
                 dry=(80, 8), open_days=60, support=['Support for ABS'], note='Ventilera rummet; avger ångor.'),
    'ASA':  dict(nozzle=(240, 280), bed=(80, 100), hardened=False, ams='Ja', enclosure='Krävs (sluten skrivare)',
                 dry=(80, 8), open_days=60, support=['Support for ABS'], note='UV-tålig, bra utomhus. Ventilera rummet.'),
    'PC':   dict(nozzle=(260, 290), bed=(100, 110), hardened=False, ams='Ja', enclosure='Krävs (sluten skrivare)',
                 dry=(80, 8), open_days=14, support=['Support for ABS'], note='Mycket fuktkänslig.'),
    'PA':   dict(nozzle=(260, 300), bed=(80, 100), hardened=True, ams='Ja, men torka först', enclosure='Krävs (sluten skrivare)',
                 dry=(80, 12), open_days=3, support=['Support for PA/PET'], note='Extremt fuktkänslig – förvara torrt och torka före varje utskrift.'),
    'PAHT': dict(nozzle=(260, 290), bed=(80, 100), hardened=True, ams='Ja, men torka först', enclosure='Krävs (sluten skrivare)',
                 dry=(80, 12), open_days=3, support=['Support for PA/PET'], note='Extremt fuktkänslig.'),
    'PPA':  dict(nozzle=(290, 320), bed=(100, 120), hardened=True, ams='Nej (extern spole)', enclosure='Krävs (uppvärmd kammare)',
                 dry=(100, 8), open_days=3, support=['Support for PA/PET'], note='Kräver H2D/X1E-klass. Extremt fuktkänslig.'),
    'PPS':  dict(nozzle=(310, 340), bed=(100, 120), hardened=True, ams='Nej (extern spole)', enclosure='Krävs (uppvärmd kammare)',
                 dry=(120, 8), open_days=7, support=[], note='Kräver H2D/X1E-klass.'),
    'TPU':  dict(nozzle=(220, 240), bed=(30, 35), hardened=False, ams='Nej (extern spole)', enclosure='Nej',
                 dry=(70, 8), open_days=14, support=[], note='Mjukt; skriv långsamt.'),
    'PVA':  dict(nozzle=(210, 250), bed=(35, 60), hardened=False, ams='Ja, men torka först', enclosure='Nej',
                 dry=(55, 8), open_days=2, support=[], note='Vattenlösligt stöd. Extremt fuktkänsligt – förvara i lufttät låda.'),
    'Support for PLA': dict(nozzle=(190, 220), bed=(35, 65), hardened=False, ams='Ja', enclosure='Nej', dry=(55, 8), open_days=60,
                            support=[], note='Stöd/gränsskikt för PLA och PETG; lossnar lätt.'),
    'Support for ABS': dict(nozzle=(240, 270), bed=(80, 100), hardened=False, ams='Ja', enclosure='Krävs', dry=(80, 8), open_days=60,
                            support=[], note='Stöd för ABS, ASA och PC.'),
    'Support for PA': dict(nozzle=(260, 290), bed=(80, 100), hardened=True, ams='Ja, men torka först', enclosure='Krävs', dry=(80, 12),
                           open_days=3, support=[], note='Stöd för PA och PET.'),
}
# Typer där tillsatsen ändrar kraven.
ADDITIVES = {
    '-CF': dict(hardened=True, note_extra='Kolfiberfylld: kräver härdat munstycke (stål eller bättre).'),
    '-GF': dict(hardened=True, note_extra='Glasfiberfylld: kräver härdat munstycke.'),
    'Glow': dict(hardened=True, note_extra='Självlysande pigment är slipande: använd härdat munstycke.'),
    'Galaxy': dict(note_extra=''),
    'Marble': dict(note_extra=''),
    'Wood': dict(note_extra='Träfiber: 0,6 mm munstycke minskar risken för igensättning.'),
    'Sparkle': dict(note_extra='Glitter: 0,6 mm munstycke minskar risken för igensättning.'),
    'Aero': dict(note_extra='Skummande material: kräver särskild profil (lägre flöde).'),
    'TPU for AMS': dict(ams='Ja (gjort för AMS)'),
    'HF': dict(note_extra='HF = höghastighet: tål högre flöde.'),
}


def for_type(typ):
    """Riktvärden för en katalogtyp, t.ex. 'PETG-CF' eller 'Support for PA/PET'. None om typen är okänd."""
    t = str(typ or '')
    base = None
    for name in sorted(BASE, key=len, reverse=True):
        if t.startswith(name) or t.replace('-', ' ').startswith(name):
            base = name
            break
    if not base:
        return None
    info = dict(BASE[base], base=base, type=t)
    notes = [info['note']] if info['note'] else []
    for marker, extra in ADDITIVES.items():
        if marker in t:
            info.update({k: v for k, v in extra.items() if k != 'note_extra'})
            if extra.get('note_extra'):
                notes.append(extra['note_extra'])
    info['note'] = ' '.join(notes)
    return info


def all_types(types):
    return {t: for_type(t) for t in sorted(types) if for_type(t)}
