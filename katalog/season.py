"""Bästa tid att köpa: säsongsmönster i det dagliga lägsta priset (sparas i ~2 år) och kända reaperioder."""
import datetime
import statistics

from . import db

MONTHS = ['januari', 'februari', 'mars', 'april', 'maj', 'juni', 'juli', 'augusti', 'september', 'oktober', 'november', 'december']


def black_friday(year):
    nov1 = datetime.date(year, 11, 1)
    thursday = nov1 + datetime.timedelta(days=(3 - nov1.weekday()) % 7 + 21)
    return thursday + datetime.timedelta(days=1)


def calendar_hint(today):
    """Kända reaperioder inom en månad framåt (gäller alla färger)."""
    bf = black_friday(today.year)
    days = (bf - today).days
    if 0 < days <= 30:
        return f'Black Friday är {bf.day} {MONTHS[bf.month - 1]} (om {days} dagar) – många butiker har rea då.'
    if 0 <= (today - bf).days <= 3:
        return 'Black Friday-veckan pågår – många butiker har rea just nu.'
    if today.month == 12 and today.day < 26:
        return f'Mellandagsrean börjar 26 december (om {26 - today.day} dagar).'
    return None


def advice(key, today=None):
    """Råd för en färg utifrån historiken, eller None om det inte finns tillräckligt med data."""
    today = today or datetime.date.today()
    rows = [(datetime.date.fromisoformat(r['day']), r['best']) for r in db.daily_prices(key)]
    if len(rows) < 30:
        return None
    span = (rows[-1][0] - rows[0][0]).days
    current = rows[-1][1]
    last_year = [p for d, p in rows if (today - d).days <= 365]
    if len(last_year) >= 30:
        low = sorted(last_year)[max(0, len(last_year) // 10 - 1)]
        if current <= low:
            return f'Priset är bland de lägsta det senaste året ({current} kr).'
    if span < 300:
        return None
    by_month = {}
    for d, p in rows:
        by_month.setdefault(d.month, []).append(p)
    if today.month not in by_month:
        return None
    now_avg = statistics.mean(by_month[today.month])
    best = None
    for ahead in (1, 2):
        m = (today.month - 1 + ahead) % 12 + 1
        if m in by_month and len(by_month[m]) >= 5:
            avg = statistics.mean(by_month[m])
            if avg <= now_avg * 0.92 and (best is None or avg < best[1]):
                best = (m, avg)
    if best:
        pct = round(100 * (1 - best[1] / now_avg))
        return f'Brukar bli billigare i {MONTHS[best[0] - 1]} (ca −{pct} % mot {MONTHS[today.month - 1]}).'
    return None
