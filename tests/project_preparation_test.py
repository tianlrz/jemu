"""Working copies retain source bytes, survive reloads and reject stale writes."""
import base64
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]

class Preparation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='jemu-preparation-')
        cls.library=Path(cls.temp.name)/'library'
        cls.process=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--no-seed','--data-dir',str(cls.library)],stdout=subprocess.PIPE,text=True)
        cls.base=cls.process.stdout.readline().strip()
        cls.token=re.search(r"DECKROOM_TOKEN='([^']+)'",urllib.request.urlopen(cls.base).read().decode()).group(1)
    @classmethod
    def post(cls,path,data):
        return json.load(urllib.request.urlopen(urllib.request.Request(cls.base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Deckroom-Token':cls.token})))
    @classmethod
    def tearDownClass(cls):
        cls.post('/api/shutdown',{});cls.process.wait(timeout=10);cls.process.stdout.close();cls.temp.cleanup()
    def setUp(self):
        self.source=b'<!doctype html><meta charset="gbk"><button>original interaction</button>\xd6\xd0\xce\xc4'
        self.meta=self.post('/api/import',{'name':'Preparation fixture','entry':'index.html','files':[{'path':'index.html','data':base64.b64encode(self.source).decode()},{'path':'logo.png','data':base64.b64encode(b'asset bytes').decode()}]})
        self.prefix='/api/decks/'+self.meta['id']
        self.directory=self.library/self.meta['id']
    def prepare(self,meta,**fields):
        return self.post(self.prefix+'/prepare',{'baseRevision':meta['revision'],**fields})
    def archive(self,version):
        with zipfile.ZipFile(self.directory/'versions'/(version+'.zip')) as archive:return archive.read('index.html'),archive.read('logo.png')
    def test_native_bytes_preserved_and_preparation_once(self):
        prepared=self.prepare(self.meta)
        self.assertEqual(len(prepared['versions']),2)
        self.assertEqual(prepared['jemuVersion']['kind'],'native')
        self.assertEqual((self.directory/'document/index.html').read_bytes(),self.source)
        self.assertEqual(self.archive(prepared['jemuVersion']['sourceVersion']),(self.source,b'asset bytes'))
        self.assertEqual(self.prepare(prepared)['versions'],prepared['versions'])
    def test_complete_html_persisted_and_source_recoverable(self):
        clean='<!doctype html><meta charset="utf-8"><main data-jemu-deck><section>One</section><section>Two</section></main>'
        prepared=self.prepare(self.meta,html=clean)
        self.assertEqual((self.directory/'document/index.html').read_text(encoding='utf8'),clean)
        self.assertEqual(self.archive(prepared['jemuVersion']['sourceVersion'])[0],self.source)
        self.assertEqual(self.archive(prepared['jemuVersion']['version'])[0],clean.encode())
        self.assertEqual(self.prepare(prepared,html=clean)['versions'],prepared['versions'])
        # Subsequent viewing must never overwrite an edit or append another backup.
        edited=self.post(self.prefix+'/save',{'html':clean+'<!-- later edit -->','baseRevision':prepared['revision']})
        self.assertEqual(self.prepare(edited)['versions'],edited['versions'])
        self.assertIn('later edit',(self.directory/'document/index.html').read_text())
    def test_native_then_dynamic_conversion_and_revision_conflict(self):
        native=self.prepare(self.meta)
        clean='<main data-jemu-deck><section>All pages</section></main>'
        converted=self.prepare(native,html=clean)
        self.assertEqual(converted['jemuVersion']['kind'],'normalized')
        self.assertEqual(len(converted['versions']),3)
        with self.assertRaises(urllib.error.HTTPError) as stale:self.prepare(native,html='stale overwrite')
        self.assertEqual(stale.exception.code,409)
        self.assertEqual((self.directory/'document/index.html').read_text(),clean)
    def test_external_source_backed_up_before_conversion(self):
        external=b'<h1>external source</h1>'
        (self.directory/'document/index.html').write_bytes(external)
        current=json.load(urllib.request.urlopen(self.base+self.prefix))
        prepared=self.prepare(current,html='<main data-jemu-deck>converted</main>')
        self.assertEqual(self.archive(prepared['jemuVersion']['sourceVersion'])[0],external)
        self.assertEqual(len(prepared['versions']),3)
    def test_invalid_target_does_not_create_version(self):
        for fields in ({'entry':'../outside.html'},{'entry':'logo.png'},{'html':42}):
            with self.assertRaises(urllib.error.HTTPError) as invalid:self.prepare(self.meta,**fields)
            self.assertEqual(invalid.exception.code,400)
        current=json.load(urllib.request.urlopen(self.base+self.prefix))
        self.assertEqual(current['versions'],self.meta['versions'])
        self.assertEqual((self.directory/'document/index.html').read_bytes(),self.source)

if __name__=='__main__':unittest.main()
