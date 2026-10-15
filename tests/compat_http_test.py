"""Exercise native assets/media, legacy encoding, page identity and view preference."""
import base64
import http.client
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


class CompatibilityHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='jemu-compat-')
        cls.library=Path(cls.temp.name)/'library'
        cls.process=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--no-seed','--data-dir',str(cls.library)],stdout=subprocess.PIPE,text=True)
        cls.base=cls.process.stdout.readline().strip()
        cls.token=re.search(r"DECKROOM_TOKEN='([^']+)'",urllib.request.urlopen(cls.base).read().decode()).group(1)
        def file(name,data):return {'path':name,'data':base64.b64encode(data).decode()}
        cls.meta=cls.post('/api/import',{'name':'Compatibility HTTP','entry':'报告#1.html','files':[
            file('报告#1.html','<!doctype html><meta charset="gbk"><h1>中文旧编码</h1>'.encode('gbk')),
            file('detail.html',b'<!doctype html><h1>child</h1>'),
            file('assets/app.mjs',b'export const ready=true;'),
            file('media/sample.mp4',bytes(range(256))*1024),
            file('nested/index.html',b'<h1>folder</h1>'),
        ]})
        cls.prefix='/documents/'+cls.meta['id']+'/'

    @classmethod
    def post(cls,path,data):
        req=urllib.request.Request(cls.base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Deckroom-Token':cls.token})
        return json.load(urllib.request.urlopen(req))

    @classmethod
    def tearDownClass(cls):
        cls.post('/api/shutdown',{});cls.process.wait(timeout=10);cls.process.stdout.close();cls.temp.cleanup()

    def test_encoded_entry_and_legacy_charset(self):
        self.assertIn('%23',self.meta['url'])
        with urllib.request.urlopen(self.base+self.meta['url']) as response:
            self.assertNotIn('charset=utf-8',response.headers['Content-Type'])
            self.assertIn('中文旧编码',response.read().decode('gbk'))

    def test_mjs_mime_and_directory_relative_base(self):
        with urllib.request.urlopen(self.base+self.prefix+'assets/app.mjs') as response:self.assertIn('javascript',response.headers['Content-Type'])
        with urllib.request.urlopen(self.base+self.prefix+'nested') as response:
            self.assertTrue(response.url.endswith('nested/'))
            self.assertIn(b'folder',response.read())

    def test_media_range_and_head(self):
        path=self.base+self.prefix+'media/sample.mp4'
        for value,start,length in [('bytes=100-199',100,100),('bytes=-16',262128,16),('bytes=262140-',262140,4)]:
            with urllib.request.urlopen(urllib.request.Request(path,headers={'Range':value})) as response:
                self.assertEqual(response.status,206)
                self.assertEqual(response.read(),(bytes(range(256))*1024)[start:start+length])
                self.assertEqual(int(response.headers['Content-Length']),length)
        with urllib.request.urlopen(urllib.request.Request(path,method='HEAD')) as response:
            self.assertEqual(int(response.headers['Content-Length']),262144);self.assertEqual(response.read(),b'')
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(urllib.request.Request(path,headers={'Range':'bytes=999999-'}))
        self.assertEqual(error.exception.code,416)

    def test_subpage_save_and_mode_setting(self):
        deck='/api/decks/'+self.meta['id']
        meta=json.load(urllib.request.urlopen(self.base+deck))
        original=(self.library/meta['id']/'document'/'报告#1.html').read_bytes()
        meta=self.post(deck+'/save',{'entry':'detail.html','html':'<!doctype html><meta charset="utf-8"><h1>edited child</h1>','baseRevision':meta['revision'],'changes':[]})
        self.assertEqual((self.library/meta['id']/'document'/'报告#1.html').read_bytes(),original)
        self.assertIn('edited child',(self.library/meta['id']/'document/detail.html').read_text())
        meta=self.post(deck+'/view-mode',{'mode':'native'})
        self.assertEqual(meta['viewMode'],'native')
        self.assertEqual(meta['entry'],'报告#1.html')


if __name__=='__main__':unittest.main()
