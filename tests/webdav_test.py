"""Real HTTP contracts against a local fake DAV account; never contact Nutstore."""
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote, unquote, urlparse
from xml.sax.saxutils import escape

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
import webdav_store as dav
from credentials import save_secret, load_secret
from cloud_archives import create_archive, restore_archive


class FakeDAV(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,status,body=b'',headers=None):
        self.send_response(status)
        self.send_header('Content-Length',str(len(body)))
        for key,value in (headers or {}).items():self.send_header(key,value)
        self.end_headers();self.wfile.write(body)
    def dispatch(self):
        host=self.server
        length=int(self.headers.get('Content-Length','0'))
        body=self.rfile.read(length)
        path=unquote(urlparse(self.path).path).rstrip('/')
        host.calls.append((self.command,path))
        if self.headers.get('Authorization')!=host.auth:return self.send(401)
        if host.failure:return self.send(host.failure,b'',{'Location':'http://127.0.0.1:1/never'})
        if self.command=='PROPFIND':
            if path not in host.dirs:return self.send(404)
            if host.xml is not None:return self.send(207,host.xml)
            names=[path]
            if self.headers.get('Depth')!='0':names+=sorted(p for p in host.dirs|set(host.files) if p.rsplit('/',1)[0]==path)
            responses=[]
            for name in names:
                kind='<d:collection/>' if name in host.dirs else ''
                responses.append('<d:response><d:href>'+escape(quote(name,safe='/'))+'</d:href><d:propstat><d:prop><d:resourcetype>'+kind+'</d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>')
            return self.send(207,('<d:multistatus xmlns:d="DAV:">'+''.join(responses)+'</d:multistatus>').encode())
        if self.command=='MKCOL':
            if path in host.dirs:return self.send(405)
            if path.rsplit('/',1)[0] not in host.dirs:return self.send(409)
            host.dirs.add(path);return self.send(201)
        if self.command=='PUT':
            if path in host.files and self.headers.get('If-None-Match')=='*':return self.send(412)
            if host.fail_index and path.endswith('.json'):return self.send(507)
            host.files[path]=body;return self.send(201)
        if self.command=='GET':
            return self.send(200,host.files[path]) if path in host.files else self.send(404)
        self.send(405)
    do_PROPFIND=do_MKCOL=do_PUT=do_GET=dispatch


@contextmanager
def fake_dav():
    host=ThreadingHTTPServer(('127.0.0.1',0),FakeDAV)
    host.auth='Basic '+base64.b64encode(b'test@example.invalid:local-test-secret').decode()
    host.calls=[];host.dirs={'/dav'};host.files={};host.failure=0;host.xml=None;host.fail_index=False
    thread=threading.Thread(target=host.serve_forever,daemon=True);thread.start()
    try:yield host,'http://127.0.0.1:'+str(host.server_port)+'/dav/'
    finally:host.shutdown();host.server_close();thread.join()


class WebDAVTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='jemu-dav-test-');self.root=Path(self.temp.name)
        server.DATA=self.root/'library';server.DATA.mkdir();server.REVISION_CACHE.clear()
        self.deck,self.meta=server.create_deck('测试作品','index.html',[('index.html',b'<h1>original</h1>'),('image.bin',b'asset')])
        (self.deck/'document/index.html').write_text('<h1>edited</h1>')
        self.meta=server.snapshot(self.deck,'edited',[{'type':'text'}])
        self.id='20261015T120000Z-0123456789ab.jemu.zip';self.archive=self.root/self.id
        self.manifest=create_archive(self.deck,self.archive,self.meta)
        self.mock=fake_dav();self.host,self.url=self.mock.__enter__()
        self.store=dav.NutstoreArchiveStore('test@example.invalid','local-test-secret',_test_url=self.url)
        dav._INDEX_CACHE.clear()
    def tearDown(self):self.mock.__exit__(None,None,None);self.temp.cleanup()
    def upload(self):return self.store.put(self.archive,self.id,self.manifest)
    def test_roundtrip_streaming_archive_and_full_history(self):
        self.assertTrue(self.store.test()['ok']);self.assertEqual(self.store.list(month='2026-10')['items'],[])
        self.assertEqual(self.upload()['remoteStatus'],'server-accepted')
        before=len(self.host.calls);listing=self.store.list(month='2026-10')
        self.assertEqual(listing['items'][0]['versionCount'],2)
        self.assertFalse(any(method=='GET' and path.endswith('.zip') for method,path in self.host.calls[before:]))
        with self.store.materialize(self.id) as source:
            restored,meta=restore_archive(source,server.DATA,server.safe_child,server.revision)
        self.assertFalse(source.exists());self.assertNotEqual(restored,self.deck)
        self.assertEqual(meta['revision'],self.meta['revision'])
        self.assertEqual((restored/'document/index.html').read_text(),'<h1>edited</h1>')
        self.assertEqual(len(meta['versions']),2)
        for version in self.meta['versions']:
            name=version['id']+'.changes.json'
            self.assertEqual((restored/'versions'/name).read_bytes(),(self.deck/'versions'/name).read_bytes())
    def test_immutable_no_remote_overwrite_and_index_published_last(self):
        self.upload();old=dict(self.host.files)
        with self.assertRaisesRegex(ValueError,'已存在'):self.upload()
        self.assertEqual(old,self.host.files)
        self.assertTrue([p for m,p in self.host.calls if m=='PUT'][1].endswith('.json'))
    def test_failed_index_hides_partial_backup_and_retry_preserves_zip(self):
        self.host.fail_index=True
        with self.assertRaisesRegex(ValueError,'空间或流量'):self.upload()
        self.assertEqual(self.store.list(month='2026-10')['items'],[])
        self.assertEqual(len(self.host.files),1)
    def test_http_failures_do_not_leak_password_or_retry_or_follow_redirects(self):
        for status in (401,429,302,500):
            self.host.failure=status;before=len(self.host.calls)
            with self.assertRaises(ValueError) as caught:self.store.test()
            self.assertNotIn('local-test-secret',str(caught.exception));self.assertEqual(len(self.host.calls),before+1)
        self.host.failure=0;self.upload();self.store.list(month='2026-10')
        self.host.failure=401
        with self.assertRaises(ValueError):self.store.list(month='2026-10')
    def test_xml_and_path_validation_and_upload_size_bound(self):
        for raw in (b'not xml',b'<!DOCTYPE x [<!ENTITY y "z">]><x/>',b'<d:multistatus xmlns:d="DAV:"/>'):
            self.host.xml=raw
            with self.assertRaises(ValueError):self.store.test()
        self.host.xml=None
        for bad in ('../escape','2026-10/evil'):
            with self.assertRaises(ValueError):self.store.list(month=bad)
            with self.assertRaises(ValueError):self.store.remote_path(bad)
        with self.archive.open('r+b') as stream:stream.truncate(dav.UPLOAD_LIMIT+1)
        before=len(self.host.calls)
        with self.assertRaisesRegex(ValueError,'450 MiB'):self.upload()
        self.assertEqual(len(self.host.calls),before)
    def test_month_pagination_cache_and_truncated_directory_guard(self):
        self.upload();path='/dav/'+self.store.remote_path(self.id)[:-4]+'.json'
        for index in range(54):
            aid='20261015T120100Z-'+format(index,'012x')+'.jemu.zip'
            metadata={**json.loads(self.host.files[path]),'id':aid}
            self.host.files['/dav/'+self.store.remote_path(aid)[:-4]+'.json']=json.dumps(metadata).encode()
        first=self.store.list(month='2026-10');last=self.store.list(month='2026-10',offset=50)
        self.assertEqual(len(first['items']),50);self.assertTrue(first['hasMore']);self.assertEqual(len(last['items']),5);self.assertFalse(last['hasMore'])
        before=len(self.host.calls);self.store.list(month='2026-10')
        self.assertFalse(any(m=='GET' for m,p in self.host.calls[before:]));self.assertLessEqual(len(dav._INDEX_CACHE),100)
        self.host.xml=b'<d:multistatus xmlns:d="DAV:">'+b'<d:response/>'*750+b'</d:multistatus>'
        with self.assertRaisesRegex(ValueError,'目录上限'):self.store.propfind('')
    @unittest.skipUnless(sys.platform=='win32','Windows DPAPI only')
    def test_local_credentials_encrypted_bound_to_username_and_disconnect(self):
        secret=server.DATA/'.settings/webdav.secret'
        save_secret(secret,'test@example.invalid','local-test-secret',server.atomic_write)
        self.assertNotIn(b'local-test-secret',secret.read_bytes())
        self.assertEqual(load_secret(secret,'test@example.invalid'),'local-test-secret')
        with self.assertRaises(ValueError):load_secret(secret,'other@example.invalid')
        server.atomic_json(server.DATA/'.settings/cloud.json',{'provider':'nutstore','username':'test@example.invalid','folder':''})
        public=server.public_cloud_config();self.assertTrue(public['passwordStored']);self.assertNotIn('local-test-secret',json.dumps(public))
        create_archive(self.deck,self.archive,self.meta)
        import zipfile
        with zipfile.ZipFile(self.archive) as zipped:self.assertFalse(any('webdav.secret' in name or 'cloud.json' in name for name in zipped.namelist()))
    @unittest.skipUnless(sys.platform=='win32','Windows DPAPI only')
    def test_local_api_roundtrip_config_backup_restore_disconnect(self):
        actual=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        server.SERVER=actual
        thread=threading.Thread(target=actual.serve_forever,daemon=True);thread.start()
        from urllib.request import Request,urlopen
        def call(path,data=None):
            raw=None if data is None else json.dumps(data).encode()
            request=Request('http://127.0.0.1:'+str(actual.server_port)+path,data=raw,headers={'Content-Type':'application/json','X-Deckroom-Token':server.TOKEN})
            with urlopen(request) as response:return json.load(response)
        factory=lambda username,password:dav.NutstoreArchiveStore(username,password,_test_url=self.url)
        try:
            with patch('server.NutstoreArchiveStore',side_effect=factory):
                config=call('/api/cloud/config',{'provider':'nutstore','username':'test@example.invalid','password':'local-test-secret'})
                self.assertTrue(config['configured']);self.assertEqual(self.host.calls,[])
                self.assertNotIn('password',config);self.assertTrue(call('/api/cloud/test',{})['ok'])
                saved=call('/api/decks/'+self.meta['id']+'/cloud-backup',{'baseRevision':self.meta['revision']})
                self.assertEqual(saved['status'],'uploaded')
                entries=call('/api/cloud/archives?month='+saved['month'])
                restored=call('/api/cloud/restore',{'archive':entries['items'][0]['id']})
                self.assertNotEqual(restored['id'],self.meta['id']);self.assertEqual(len(restored['versions']),2)
                self.assertFalse(call('/api/cloud/disconnect',{})['configured'])
                self.assertFalse((server.DATA/'.settings/webdav.secret').exists());self.assertEqual(len(self.host.files),2)
                # Quark uses the existing local folder adapter, not the DAV account.
                folder=self.root/'quark-backup';folder.mkdir()
                calls_before=len(self.host.calls)
                config=call('/api/cloud/config',{'provider':'quark','folder':str(folder)})
                self.assertEqual(config['provider'],'quark');self.assertEqual(config['mode'],'sync-folder')
                saved=call('/api/decks/'+self.meta['id']+'/cloud-backup',{'baseRevision':self.meta['revision']})
                self.assertEqual(saved['status'],'written-to-sync-folder');self.assertEqual(saved['remoteStatus'],'unknown')
                entries=call('/api/cloud/archives')
                restored=call('/api/cloud/restore',{'archive':entries['items'][0]['id']})
                self.assertNotEqual(restored['id'],self.meta['id']);self.assertEqual(len(restored['versions']),2)
                self.assertEqual(len(self.host.calls),calls_before)
        finally:actual.shutdown();actual.server_close();thread.join()


if __name__=='__main__':unittest.main()
