"""Nya färger: första körningen sparar utan att markera något, sedan upptäcks nyheter i katalogen och hos Bambu Lab."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from katalog import alerts, config, db, newcolors  # noqa: E402

CAT = [{'type': 'PLA Basic', 'code': '10101', 'name': 'Black'}, {'type': 'PLA Basic', 'code': '10100', 'name': 'Jade White'}]


def entry(code, typ='PLA Basic', sv='', en='Färg'):
    return {'fila_color_code': code, 'fila_id': 'GFA00', 'fila_type': typ, 'fila_color': ['#112233FF'],
            'fila_color_name': {'sv': sv, 'en': en}}


class NewColors(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(config, k) for k in ('DATA_DIR', 'DB_PATH', 'STATE_DIR')}
        self.tmp = Path(tempfile.mkdtemp())
        config.DATA_DIR, config.DB_PATH, config.STATE_DIR = self.tmp, self.tmp / 'katalog.db', self.tmp / 'state'
        db._local.conn = None
        db.init()

    def tearDown(self):
        db.conn().close()
        db._local.conn = None
        for k, v in self.saved.items():
            setattr(config, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_seed_then_detect(self):
        colors = [entry('10101'), entry('10100'), entry('18101', 'PLA Palette', en='Key')]
        self.assertEqual(newcolors.check(colors, CAT, now=1000), [])                 # första gången: inget är nytt
        sm = newcolors.summary(now=2000, catalog=CAT)
        self.assertEqual(sm['recent'], {})
        self.assertEqual(sm['upcoming'], [])
        self.assertEqual([m['code'] for m in sm['missing']], ['18101'])                # saknas i katalogen, men inte nytt
        cat2 = CAT + [{'type': 'PLA Basic', 'code': '10700', 'name': 'Lilac'}]
        colors2 = colors + [entry('10700', sv='Lila'), entry('19999', 'PLA Galaxy', sv='Stjärnhimmel')]
        fresh = newcolors.check(colors2, cat2, now=5000)
        self.assertEqual(sorted((n['kind'], n.get('code')) for n in fresh), [('bambu', '19999'), ('katalog', '10700')])
        sm = newcolors.summary(now=6000, catalog=cat2)
        self.assertEqual(list(sm['recent']), ['PLA Basic|10700'])
        self.assertEqual([(u['code'], u['name']) for u in sm['upcoming']], [('19999', 'Stjärnhimmel')])   # svenskt namn först
        self.assertEqual(newcolors.check(colors2, cat2, now=7000), [])                # inget nytt andra gången
        self.assertEqual(newcolors.summary(now=5000 + 61 * 86400, catalog=cat2)['recent'], {})       # nytt i 60 dagar

    def test_notify_only_opted_in(self):
        a, b = db.create_user('a', 'x'), db.create_user('b', 'x')
        for uid in (a, b):
            db.set_pref(uid, 'notify', f'https://ntfy.sh/t{uid}')
        db.set_pref(a, 'notify_new', '1')
        sent, orig = [], alerts.send
        alerts.send = lambda target, title, msg, click=None: sent.append((target, title, msg))
        try:
            newcolors.notify([{'kind': 'bambu', 'type': 'PLA Galaxy', 'name': 'Stjärnhimmel', 'code': '19999'}], log=lambda m: None)
        finally:
            alerts.send = orig
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], f'https://ntfy.sh/t{a}')
        self.assertIn('1 ny färg hos Bambu Lab', sent[0][1])
        self.assertIn('PLA Galaxy Stjärnhimmel (19999)', sent[0][2])


if __name__ == '__main__':
    unittest.main()
