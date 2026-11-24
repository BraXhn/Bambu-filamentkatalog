"""Bevakning av priskällor i containern: avisering när en källa misslyckats två gånger i rad, och när den fungerar igen."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from katalog import alerts, config, db, sourcewatch  # noqa: E402


class SourceWatch(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(config, k) for k in ('DATA_DIR', 'DB_PATH')}
        self.tmp = Path(tempfile.mkdtemp())
        config.DATA_DIR, config.DB_PATH = self.tmp, self.tmp / 'katalog.db'
        db._local.conn = None
        db.init()
        self.admin = db.create_user('admin', 'x', is_admin=True)
        self.user = db.create_user('vanlig', 'x')
        for uid in (self.admin, self.user):
            db.set_pref(uid, 'notify', f'https://ntfy.sh/t{uid}')
        self.sent, self.orig = [], alerts.send
        alerts.send = lambda target, title, msg, click=None: self.sent.append((target, title, msg))

    def tearDown(self):
        alerts.send = self.orig
        db.conn().close()
        db._local.conn = None
        for k, v in self.saved.items():
            setattr(config, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, tradera):
        rid = db.start_run('schedule')
        db.finish_run(rid, 'partial' if tradera == 'failed' else 'ok',
                      {'tradera': {'label': 'Tradera', 'status': tradera, 'offers': 236, 'error': 'Traderas sökning kunde inte hämtas'},
                       '3djake': {'label': '3DJake', 'status': 'ok', 'offers': 169}}, '')

    def test_alert_once_then_recovered(self):
        self.run_with('failed')
        self.assertEqual(sourcewatch.check(lambda m: None), ([], []))           # en gång räcker inte
        self.run_with('failed')
        self.assertEqual(sourcewatch.check(lambda m: None), (['Tradera'], []))
        self.assertEqual([t for t, _, _ in self.sent], [f'https://ntfy.sh/t{self.admin}'])   # bara administratörer
        self.assertIn('Traderas sökning kunde inte hämtas', self.sent[0][2])
        self.run_with('failed')
        self.assertEqual(sourcewatch.check(lambda m: None), ([], []))           # ingen upprepning
        self.run_with('ok')
        self.assertEqual(sourcewatch.check(lambda m: None), ([], ['Tradera']))
        self.assertEqual(self.sent[-1][1], 'Prishämtningen fungerar igen')
        self.assertEqual(json.loads(db.get_setting('source_alerts')), [])


if __name__ == '__main__':
    unittest.main()
