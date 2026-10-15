"""Databasregler: Bambu-koppling vid omkoppling och att CLI-kommandon inte avbryter pågående körningar."""
import os
import shutil
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp()
os.environ['KATALOG_DATA_DIR'] = TMP
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from katalog import db  # noqa: E402


class Db(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)

    def test_init_keeps_running_run(self):
        rid = db.start_run('test')
        db.init()   # t.ex. "docker exec … katalog users" medan servern kör
        self.assertEqual(db.running_run()['id'], rid)
        db.recover_runs()   # serverns omstart
        self.assertIsNone(db.running_run())

    def test_bambu_relink(self):
        uid = db.create_user('bambu', 'x')
        db.save_bambu_token(uid, 'a@x', 'global', 'old', 0)
        db.save_bambu_sync(uid, 'ok', matched={'PLA Basic|10101': {}}, unmatched=[], token='old')
        db.save_bambu_token(uid, 'a@x', 'global', 'new', 0)
        db.save_bambu_sync(uid, 'expired', 'utgått', token='old')   # sen synk med gamla tokenet
        link = db.get_bambu(uid)
        self.assertEqual((link['token'], link['status']), ('new', 'ok'))
        self.assertIn('10101', link['matched'])   # samma konto: innehavet ligger kvar
        db.save_bambu_token(uid, 'b@x', 'global', 'other', 0)
        self.assertIsNone(db.get_bambu(uid)['matched'])   # annat konto: gamla innehavet försvinner


if __name__ == '__main__':
    unittest.main()
