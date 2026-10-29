"""Unchanged large assets are stored once across versions; old and new versions restore exactly."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloud_archives import create_archive, restore_archive
import server

class VersionStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='jemu-versions-')
        cls.library=Path(cls.temp.name)/'library'
        cls.process=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--no-seed','--data-dir',str(cls.library)],stdout=subprocess.PIPE,text=True)
        cls.base=cls.process.stdout.readline().strip()
        cls.token=re.search(r"DECKROOM_TOKEN='([^']+)'",urllib.request.urlopen(cls.base).read().decode()).group(1)
    @classmethod
    def tearDownClass(cls):
        cls.process.terminate();cls.process.wait(timeout=10);cls.temp.cleanup()
    def post(self,path,body):
        request=urllib.request.Request(self.base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Deckroom-Token':self.token},method='POST')
        with urllib.request.urlopen(request) as response:return json.load(response)
    def get(self,path):
        with urllib.request.urlopen(self.base+path) as response:return response.read()
    def test_assets_deduplicated_and_restored(self):
        video=os.urandom(1024*1024);small=b'small asset'
        files={'index.html':b'<!doctype html><p>v1</p>','media/clip.mp4':video,'small.txt':small}
        meta=self.post('/api/import',{'name':'Dedup','entry':'index.html','files':[{'path':k,'data':base64.b64encode(v).decode()} for k,v in files.items()]})
        directory=self.library/meta['id'];first=meta['versions'][0]['id']
        for i in range(2,6):meta=self.post('/api/decks/'+meta['id']+'/save',{'html':f'<!doctype html><p>v{i}</p>','baseRevision':meta['revision']})
        self.assertEqual(len(meta['versions']),5)
        blobs=list((directory/'versions'/'blobs').iterdir())
        self.assertEqual([p.name for p in blobs],[hashlib.sha256(video).hexdigest()])
        zips=sum(p.stat().st_size for p in (directory/'versions').glob('*.zip'))
        self.assertLess(zips,200*1024,'each version ZIP must not repeat the 1 MiB asset')
        with zipfile.ZipFile(directory/'versions'/(first+'.zip')) as archive:
            self.assertNotIn('media/clip.mp4',archive.namelist());self.assertIn('small.txt',archive.namelist())
        # Change the asset, then restore the first version: both html and asset come back exactly.
        (directory/'document'/'media'/'clip.mp4').write_bytes(b'replaced')
        meta=json.loads(self.get('/api/decks/'+meta['id']))
        meta=self.post('/api/decks/'+meta['id']+'/restore',{'version':first,'baseRevision':meta['revision']})
        self.assertEqual((directory/'document'/'media'/'clip.mp4').read_bytes(),video)
        self.assertEqual((directory/'document'/'index.html').read_bytes(),files['index.html'])
        self.assertEqual(list(directory.glob('replaced-*')),[],'redundant pre-restore copy must be removed after the restore is recorded')
        self.assertEqual(list(directory.glob('restore-*')),[])
        # Edit export compares against the first version, including blob-stored assets.
        self.post('/api/decks/'+meta['id']+'/export-edit',{'mode':'trace','prepare':True,'baseRevision':meta['revision']})
        changes=json.loads((directory/'exports'/'changes.json').read_text(encoding='utf8'))
        status={item['path']:item['status'] for item in changes['files']}
        self.assertEqual(status['media/clip.mp4'],'unchanged')
        # A cloud archive carries the blob folder and restores a complete deck.
        work=Path(self.temp.name)/'archive';work.mkdir()
        server.DATA=self.library
        create_archive(directory,work/'a.jemu.zip',server.read_meta(directory))
        restored,restored_meta=restore_archive(work/'a.jemu.zip',work,server.safe_child,server.revision)
        self.assertTrue((restored/'versions'/'blobs'/hashlib.sha256(video).hexdigest()).is_file())
    def test_old_version_zip_still_restores(self):
        meta=self.post('/api/import',{'name':'Legacy','entry':'index.html','files':[{'path':'index.html','data':base64.b64encode(b'<p>old</p>').decode()},{'path':'big.bin','data':base64.b64encode(b'x'*200000).decode()}]})
        directory=self.library/meta['id'];first=meta['versions'][0]['id']
        # Rewrite the first version in the pre-blob format: every file inside the ZIP.
        with zipfile.ZipFile(directory/'versions'/(first+'.zip'),'w') as archive:
            archive.writestr('index.html',b'<p>old</p>');archive.writestr('big.bin',b'y'*200000)
        meta=self.post('/api/decks/'+meta['id']+'/save',{'html':'<p>new</p>','baseRevision':meta['revision']})
        meta=self.post('/api/decks/'+meta['id']+'/restore',{'version':first,'baseRevision':meta['revision']})
        self.assertEqual((directory/'document'/'big.bin').read_bytes(),b'y'*200000)
        self.assertEqual((directory/'document'/'index.html').read_bytes(),b'<p>old</p>')

if __name__=='__main__':unittest.main()
