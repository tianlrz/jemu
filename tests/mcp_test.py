"""MCP stdio protocol and shared API integration; temporary library, no accounts."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import jemu_mcp


class MCPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='jemu-mcp-test-');cls.root=Path(cls.temp.name)
        cls.service=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--no-seed','--data-dir',str(cls.root/'library')],stdout=subprocess.PIPE,text=True,encoding='utf8')
        cls.url=cls.service.stdout.readline().strip()
    @classmethod
    def tearDownClass(cls):
        from jemu import Client
        Client(cls.url).json('POST','/api/shutdown',{});cls.service.wait(timeout=10);cls.service.stdout.close();cls.temp.cleanup()
    def setUp(self):
        self.process=subprocess.Popen([sys.executable,str(ROOT/'jemu_mcp.py'),'--url',self.url],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf8')
        self.counter=0
    def tearDown(self):
        self.process.stdin.close();self.process.wait(timeout=10)
        self.assertEqual(self.process.returncode,0)
        self.assertEqual(self.process.stderr.read(),'');self.process.stderr.close();self.process.stdout.close()
    def request(self,method,params=None):
        self.counter+=1
        self.process.stdin.write(json.dumps({'jsonrpc':'2.0','id':self.counter,'method':method,'params':params or {}})+'\n');self.process.stdin.flush()
        result=json.loads(self.process.stdout.readline());self.assertEqual(result['id'],self.counter);return result
    def initialize(self,version='2025-11-25'):
        result=self.request('initialize',{'protocolVersion':version,'capabilities':{},'clientInfo':{'name':'test-client','version':'1'}})
        self.process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n');self.process.stdin.flush();return result['result']
    def call(self,name,arguments=None):return self.request('tools/call',{'name':name,'arguments':arguments or {}})
    def data(self,name,arguments=None):
        result=self.call(name,arguments)['result'];self.assertFalse(result['isError'],result)
        value=json.loads(result['content'][0]['text']);self.assertTrue(value['ok']);return value['data']
    def test_lifecycle_discovery_and_protocol_errors(self):
        self.assertEqual(self.request('tools/list')['error']['code'],-32002)
        self.assertEqual(self.initialize()['protocolVersion'],'2025-11-25')
        tools=self.request('tools/list')['result']['tools'];self.assertEqual(len(tools),11)
        self.assertTrue(all(tool['inputSchema']['additionalProperties'] is False for tool in tools))
        self.assertEqual(self.request('ping')['result'],{})
        self.assertEqual(self.request('unknown')['error']['code'],-32601)
        self.assertEqual(self.call('not-a-tool')['error']['code'],-32602)
        self.assertEqual(self.call('jemu_status',{'password':'never-accepted'})['error']['code'],-32602)
        self.assertEqual(self.call('jemu_files',{'operation':'read'})['error']['code'],-32602)
        self.assertNotIn('password',jemu_mcp.BY_NAME['jemu_backup_settings']['inputSchema']['properties'])
    def test_file_versions_export_and_backup_roundtrip(self):
        self.initialize();source=self.root/'source.html';source.write_text('<!doctype html><h1>original</h1>',encoding='utf8')
        deck=self.data('jemu_decks',{'operation':'import','source':str(source),'name':'MCP test'})
        source.write_text('<!doctype html><h1>changed</h1>',encoding='utf8')
        updated=self.data('jemu_files',{'operation':'write','deck':deck['id'],'path':'source.html','source':str(source),'base_revision':deck['revision']})
        self.assertEqual(len(updated['versions']),2)
        conflict=self.call('jemu_files',{'operation':'write','deck':deck['id'],'path':'source.html','source':str(source),'base_revision':deck['revision']})['result']
        self.assertTrue(conflict['isError']);self.assertEqual(conflict['structuredContent']['error']['code'],'conflict')
        self.assertIn('changed',self.data('jemu_files',{'operation':'read','deck':deck['id'],'path':'source.html'})['text'])
        self.assertEqual(len(self.data('jemu_versions',{'operation':'list','deck':deck['id']})['versions']),2)
        output=self.root/'export.zip';self.data('jemu_export',{'deck':deck['id'],'output':str(output),'kind':'full'});self.assertTrue(output.is_file())
        folder=self.root/'backups';folder.mkdir()
        self.data('jemu_backup_settings',{'operation':'set','provider':'quark','folder':str(folder)})
        self.assertEqual(self.data('jemu_settings_get')['backup']['provider'],'quark')
        archived=self.data('jemu_backup',{'operation':'create','deck':deck['id']})
        restored=self.data('jemu_backup',{'operation':'restore','archive':archived['id']});self.assertNotEqual(restored['id'],deck['id'])
        self.assertEqual(restored['revision'],updated['revision'])
    def test_old_client_and_no_stdout_noise(self):
        self.assertEqual(self.initialize('2025-03-26')['protocolVersion'],'2025-03-26')
        result=self.call('jemu_status')['result'];self.assertNotIn('structuredContent',result)
        self.assertTrue(json.loads(result['content'][0]['text'])['ok'])
        self.process.stdin.write('not json\n');self.process.stdin.flush()
        self.assertEqual(json.loads(self.process.stdout.readline())['error']['code'],-32700)
        self.assertEqual(self.request('ping')['result'],{})
    def test_prepared_working_version_from_ai_file(self):
        self.initialize();source=self.root/'prepare-source.html';source.write_text('<h1>source</h1>',encoding='utf8')
        deck=self.data('jemu_decks',{'operation':'import','source':str(source)})
        source.write_text('<main data-jemu-deck><section>complete working copy</section></main>',encoding='utf8')
        missing=self.call('jemu_decks',{'operation':'prepare','deck':deck['id']})
        self.assertEqual(missing['error']['code'],-32602)
        prepared=self.data('jemu_decks',{'operation':'prepare','deck':deck['id'],'base_revision':deck['revision'],'source':str(source)})
        self.assertEqual(prepared['jemuVersion']['kind'],'normalized')
        self.assertEqual(prepared['jemuVersion']['sourceVersion'],deck['versions'][0]['id'])
        self.assertIn('complete working copy',self.data('jemu_files',{'operation':'read','deck':deck['id'],'path':deck['entry']})['text'])
    def test_window_command_requires_no_browser_automation(self):
        from jemu import Client
        self.initialize();client=Client(self.url)
        client.json('POST','/api/ui/sync',{'client':'mcp-test-window','state':{'deck':'deck-test','dirty':False}})
        result=self.data('jemu_window',{'operation':'select','arguments':{'selector':'h1'},'client':'mcp-test-window','wait':False})
        commands=client.json('POST','/api/ui/sync',{'client':'mcp-test-window','state':{}})['commands']
        self.assertEqual(commands[0]['id'],result['id'])
        client.json('POST','/api/ui/sync',{'client':'mcp-test-window','state':{},'results':[{'id':result['id'],'ok':True}]})
        self.assertEqual(self.data('jemu_window',{'operation':'result','id':result['id']})['status'],'completed')
    def test_independent_settings_document(self):
        from jemu import Client
        client=Client(self.url)
        document=client.request('GET','/settings',json_response=False).decode('utf8')
        main=client.request('GET','/',json_response=False).decode('utf8')
        self.assertIn('<title>设置</title>',document)
        self.assertNotIn('<iframe',document);self.assertNotIn('/app.js',document)
        self.assertNotIn('CLI',document);self.assertNotIn('jemu.cmd',document)
        self.assertNotIn('id="cloudPanel"',main);self.assertNotIn('/cloud.js',main)
        self.assertIn('/settings.js',document)


if __name__=='__main__':unittest.main()
