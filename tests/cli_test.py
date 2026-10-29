"""CLI/API contracts using isolated libraries and child processes. No cloud account."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.request import Request,urlopen
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import automation


class CLITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='jemu-cli-test-');cls.root=Path(cls.temp.name)
        cls.process=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--no-seed','--data-dir',str(cls.root/'library')],stdout=subprocess.PIPE,text=True,encoding='utf8')
        cls.url=cls.process.stdout.readline().strip()
    @classmethod
    def tearDownClass(cls):
        try:cls.cli('stop','--force')
        finally:
            cls.process.wait(timeout=10);cls.process.stdout.close();cls.temp.cleanup()
    @classmethod
    def cli(cls,*args,expected=0,stdin=None):
        done=subprocess.run([sys.executable,str(ROOT/'jemu.py'),'--url',cls.url,'--json',*args],input=stdin,capture_output=True,text=True,encoding='utf8',timeout=25)
        value=json.loads(done.stdout)
        if done.returncode!=expected:raise AssertionError((done.returncode,value,done.stderr))
        return value['data'] if value['ok'] else value['error']
    def make_deck(self):
        source=self.root/('source-'+self._testMethodName);source.mkdir()
        (source/'index.html').write_text('<!doctype html><h1 id="title">original</h1>',encoding='utf8')
        (source/'asset.bin').write_bytes(bytes(range(256))*8192)
        (source/'.ignored').write_bytes(b'not imported')
        return self.cli('decks','import',str(source),'--name','CLI test')
    def test_source_edit_conflicts_snapshots_and_delete(self):
        deck=self.make_deck();key=deck['id'];base=deck['revision']
        listing=self.cli('files','list',key)
        self.assertEqual(len(listing['files']),2)
        changed=self.root/'changed.html';changed.write_text('<!doctype html><h1 id="title">changed</h1>',encoding='utf8')
        edited=self.cli('files','write',key,'index.html','--from',str(changed),'--base-revision',base)
        self.assertEqual(len(edited['versions']),2)
        error=self.cli('files','write',key,'index.html','--from',str(changed),'--base-revision',base,expected=4)
        self.assertEqual(error['code'],'conflict')
        self.assertIn('changed',self.cli('files','read',key,'index.html')['text'])
        self.cli('files','delete',key,'index.html/','--base-revision',edited['revision'],expected=5)
        deleted=self.cli('files','delete',key,'asset.bin','--base-revision',edited['revision'])
        self.assertEqual(len(self.cli('files','list',key)['files']),1)
        restored=self.cli('versions','restore',key,deck['versions'][0]['id'],'--base-revision',deleted['revision'])
        self.assertEqual(len(self.cli('files','list',key)['files']),2)
        self.assertGreater(len(restored['versions']),len(deleted['versions']))
        self.cli('files','read',key,'../meta.json',expected=5)
    def test_exports_and_no_clobber(self):
        deck=self.make_deck();key=deck['id']
        for kind in ('source','full','trace'):
            target=self.root/('export-'+kind+('.md' if kind=='trace' else '.zip'))
            result=self.cli('export',key,'--kind',kind,'--out',str(target),'--intent','align boxes')
            self.assertEqual(result['bytes'],target.stat().st_size)
            before=target.read_bytes()
            self.cli('export',key,'--kind',kind,'--out',str(target),expected=2)
            self.assertEqual(before,target.read_bytes())
            if kind!='trace':
                with zipfile.ZipFile(target) as archive:self.assertTrue(any(p.endswith('index.html') for p in archive.namelist()))
        output=self.root/'saved-asset.bin'
        self.cli('files','read',key,'asset.bin','--out',str(output))
        self.assertEqual(output.read_bytes(),bytes(range(256))*8192)
    def test_working_version_preserves_original(self):
        deck=self.make_deck();key=deck['id']
        prepared=self.cli('decks','prepare',key,'--base-revision',deck['revision'])
        self.assertEqual(len(prepared['versions']),2)
        self.assertEqual(self.cli('decks','prepare',key,'--base-revision',prepared['revision'])['versions'],prepared['versions'])
        clean=self.root/'prepared.html';clean.write_text('<main data-jemu-deck><section>complete</section></main>',encoding='utf8')
        converted=self.cli('decks','prepare',key,'--base-revision',prepared['revision'],'--source',str(clean))
        self.assertEqual(converted['jemuVersion']['kind'],'normalized')
        self.assertEqual(self.cli('decks','prepare',key,'--base-revision',prepared['revision'],expected=4)['code'],'conflict')
        self.assertIn('complete',self.cli('files','read',key,'index.html')['text'])
    def test_settings_backup_restore_same_api(self):
        from jemu import Client,CLIError
        deck=self.make_deck();folder=self.root/'backup';folder.mkdir()
        old=self.cli('settings','get')['backup']['settingsRevision']
        config=self.cli('settings','backup','set','--provider','quark','--folder',str(folder))
        self.assertEqual(config['provider'],'quark')
        with self.assertRaises(CLIError) as error:Client(self.url).json('POST','/api/cloud/disconnect',{'expectedSettingsRevision':old})
        self.assertEqual(error.exception.code,'conflict')
        self.assertEqual(self.cli('settings','get')['backup']['folder'],str(folder.resolve()))
        self.assertTrue(self.cli('settings','backup','test')['ok'])
        archive=self.cli('backup','create',deck['id'])
        self.assertEqual(archive['remoteStatus'],'unknown')
        items=self.cli('backup','list')['items'];self.assertEqual(items[0]['id'],archive['id'])
        restored=self.cli('backup','restore',archive['id']);self.assertNotEqual(restored['id'],deck['id'])
        self.assertEqual(restored['revision'],deck['revision'])
        self.cli('settings','backup','disconnect')
        self.assertFalse(self.cli('settings','get')['backup']['configured'])
    def test_discovery_errors_and_ui_mailbox(self):
        from jemu import Client
        client=Client(self.url)
        self.assertIn('files',self.cli('capabilities')['operations'])
        error=self.cli('files','write','deck-x','index.html','--from','missing',expected=2)
        self.assertEqual(error['code'],'usage')
        client.json('POST','/api/ui/sync',{'client':'test-client','state':{'dirty':True,'deck':None}})
        self.assertEqual(self.cli('ui','status')['clients'][0]['client'],'test-client')
        self.assertEqual(self.cli('stop',expected=4)['code'],'unsaved_edits')
        queued=self.cli('ui','next','--no-wait')
        commands=client.json('POST','/api/ui/sync',{'client':'test-client','state':{'dirty':False}})['commands']
        self.assertEqual(commands[0]['id'],queued['id'])
        client.json('POST','/api/ui/sync',{'client':'test-client','state':{},'results':[{'id':queued['id'],'ok':True}]})
        self.assertEqual(self.cli('ui','result',queued['id'])['status'],'completed')
        self.cli('request','POST','/api/ui/sync','--body','-',stdin=json.dumps({'client':'test-client','state':{}}))
    def test_lifecycle_in_separate_application_copy(self):
        # Never point start/stop at the real application's runtime or library.
        app=self.root/'isolated-app';app.mkdir();(app/'web').mkdir()
        for name in ('jemu.py','server.py','automation.py','native_window.py','rendered_pages.py','render_exports.py','edit_exports.py','cloud_archives.py','webdav_store.py','credentials.py','version_store.py'):shutil.copy2(ROOT/name,app/name)
        shutil.copy2(ROOT/'web/index.html',app/'web/index.html')
        def run(*args):
            result=subprocess.run([sys.executable,str(app/'jemu.py'),'--json',*args],capture_output=True,text=True,encoding='utf8',timeout=15)
            self.assertEqual(result.returncode,0,result.stdout);return json.loads(result.stdout)['data']
        started=run('start','--port','0')
        try:
            self.assertTrue(started['running']);self.assertFalse(started['reused'])
            self.assertTrue(run('start')['reused']);self.assertEqual(run('status')['url'],started['url'])
        finally:run('stop','--force')


class MailboxBounds(unittest.TestCase):
    def test_queue_and_result_budget(self):
        automation.CLIENTS.clear();automation.COMMANDS.clear()
        automation.sync({'client':'one','state':{}})
        for _ in range(16):automation.enqueue({'action':'next'})
        with self.assertRaises(ValueError):automation.enqueue({'action':'next'})
        commands=automation.sync({'client':'one','state':{}})['commands']
        automation.sync({'client':'one','state':{},'results':[{'id':c['id'],'ok':True} for c in commands]})
        for _ in range(1000):
            item=automation.enqueue({'action':'next'})
            automation.sync({'client':'one','state':{}})
            automation.sync({'client':'one','state':{},'results':[{'id':item['id'],'ok':True}]})
        self.assertLessEqual(len(automation.COMMANDS),64)
        for i in range(12):automation.sync({'client':str(i),'state':{}})
        self.assertLessEqual(len(automation.CLIENTS),4)
        automation.CLIENTS.clear();automation.COMMANDS.clear()


if __name__=='__main__':unittest.main()
