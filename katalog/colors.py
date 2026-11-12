"""Färgavstånd (CIE Lab, ΔE76) för projektpaletter och "närmaste färg"."""


def _lin(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lab(hexstr):
    h = str(hexstr or '').lstrip('#')[:6]
    if len(h) != 6:
        return None
    try:
        r, g, b = (_lin(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    except ValueError:
        return None
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116  # noqa: E731
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def distance(a, b):
    la, lb = lab(a), lab(b)
    if not la or not lb:
        return float('inf')
    return sum((p - q) ** 2 for p, q in zip(la, lb)) ** 0.5


def nearest(hexstr, catalog, n=5, among=None, exclude=()):
    """[(avstånd, katalogpost)] närmast hexstr, bland among (nycklar) om angivet."""
    out = []
    for d in catalog:
        k = d['type'] + '|' + (d['code'] or d['name'])
        if k in exclude or (among is not None and k not in among) or not d.get('hex'):
            continue
        out.append((distance(hexstr, d['hex'][0]), d))
    out.sort(key=lambda x: x[0])
    return out[:n]
