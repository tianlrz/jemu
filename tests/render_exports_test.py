"""Actual independent print/PPT exports, without touching the real library."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen
import zipfile
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import render_exports
from jemu import Client,CLIError

ROOT=Path(__file__).resolve().parents[1]
SOURCE='''<!doctype html><meta charset="utf-8"><style>.slide{font:60px Arial;padding:40px}h1{margin:0}.red{background:#dd2244}.blue{background:#2266dd}</style><main data-jemu-deck data-width="960" data-height="540"><section class="slide red"><h1>First page</h1></section><section class="slide blue"><h1>Second page</h1></section></main>'''

class Export(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not render_exports.capabilities()['pptx']:raise unittest.SkipTest('System browser / optional PDF rasterizer unavailable')
        cls.temp=tempfile.TemporaryDirectory(prefix='jemu-render-test-');cls.root=Path(cls.temp.name)
        cls.process=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--no-seed','--data-dir',str(cls.root/'library')],stdout=subprocess.PIPE,text=True)
        cls.url=cls.process.stdout.readline().strip();cls.client=Client(cls.url)
        source=cls.root/'source.html';source.write_text(SOURCE,encoding='utf8')
        imported=subprocess.run([sys.executable,str(ROOT/'jemu.py'),'--url',cls.url,'--json','decks','import',str(source)],capture_output=True,text=True,encoding='utf8',check=True)
        cls.meta=json.loads(imported.stdout)['data'];cls.prefix='/api/decks/'+cls.meta['id']
    @classmethod
    def tearDownClass(cls):
        cls.client.json('POST','/api/shutdown',{});cls.process.wait(timeout=15);cls.process.stdout.close();cls.temp.cleanup()
    def wait(self,job):
        until=time.monotonic()+80
        while job['status'] in ('queued','rendering'):
            self.assertLess(time.monotonic(),until,'Export timed out');time.sleep(.2)
            job=self.client.json('GET',self.prefix+'/render-jobs/'+job['id'])
        self.assertEqual(job['status'],'completed',job);return job
    def test_pdf_is_all_pages_and_service_stays_responsive(self):
        before=self.client.meta(self.meta['id'])
        job=self.client.json('POST',self.prefix+'/render-start',{'kind':'pdf','baseRevision':before['revision']})
        with self.assertRaises(CLIError):self.client.json('POST',self.prefix+'/render-start',{'kind':'pdf','baseRevision':before['revision']})
        started=time.monotonic();self.assertTrue(self.client.json('GET','/api/health')['ok']);self.assertLess(time.monotonic()-started,3)
        job=self.wait(job);self.assertEqual(job['pages'],2)
        output=self.root/'result.pdf';self.client.request('GET',job['downloadUrl'],out=str(output))
        import pypdfium2 as pdfium
        pdf=pdfium.PdfDocument(output)
        try:
            self.assertEqual(len(pdf),2)
            for i,expected in enumerate(((221,34,68),(34,102,221))):
                # Chromium quantizes CSS page dimensions to PDF point boundaries.
                page=pdf[i];self.assertAlmostEqual(page.get_width()/page.get_height(),16/9,delta=.002)
                bitmap=page.render(scale=1);image=bitmap.to_pil()
                try:self.assertEqual(image.convert('RGB').getpixel((300,200)),expected)
                finally:image.close();bitmap.close();page.close()
        finally:pdf.close()
        after=self.client.meta(self.meta['id']);self.assertEqual(before['revision'],after['revision']);self.assertEqual(before['versions'],after['versions'])
    def test_pptx_via_cli_has_exact_images_and_valid_relationships(self):
        output=self.root/'result.pptx'
        run=subprocess.run([sys.executable,str(ROOT/'jemu.py'),'--url',self.url,'--json','export',self.meta['id'],'--kind','pptx','--out',str(output)],capture_output=True,text=True,encoding='utf8',timeout=90)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr);self.assertEqual(json.loads(run.stdout)['data']['pages'],2)
        with zipfile.ZipFile(output) as z:
            self.assertEqual(z.testzip(),None)
            slides=[n for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')]
            self.assertEqual(len(slides),2)
            self.assertEqual(len([n for n in z.namelist() if n.startswith('ppt/media/')]),2)
            for name in z.namelist():
                if name.endswith(('.xml','.rels')):ET.fromstring(z.read(name))
                if name.endswith('.rels'):
                    from posixpath import normpath,dirname,join
                    parent='' if name=='_rels/.rels' else dirname(dirname(name))
                    for rel in ET.fromstring(z.read(name)):
                        self.assertIn(normpath(join(parent,rel.attrib['Target'])),z.namelist())
            for name in slides:
                root=ET.fromstring(z.read(name));ns={'p':render_exports.P,'a':render_exports.A}
                self.assertEqual(len(root.findall('.//p:pic',ns)),1)
                self.assertEqual(root.findall('.//a:t',ns),[])
    def test_limits_scripts_stale_revisions_and_native_rejection(self):
        for data in ({'baseRevision':'stale'}, {'baseRevision':self.meta['revision'],'pages':201,'width':960,'height':540,'html':'<main>bad</main>'},{'baseRevision':self.meta['revision'],'pages':1,'width':960,'height':540,'html':'<section data-jemu-export-page><script>alert(1)</script></section>'}):
            with self.assertRaises(CLIError):self.client.json('POST',self.prefix+'/render-start',{'kind':'pdf',**data})
        self.assertEqual(render_exports.StaticDeck().pages,[])
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);(directory/'document').mkdir();(directory/'document/index.html').write_text('<section>ordinary</section>')
            with self.assertRaises(ValueError):render_exports.stored_layout(directory,{},'index.html')

if __name__=='__main__':unittest.main()
