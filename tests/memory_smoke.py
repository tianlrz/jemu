"""Windows service memory regression using a real 48 MiB streamed deck. Standard library only."""
import ctypes
from ctypes import wintypes
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class Memory(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                *[(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]]


def run():
    with tempfile.TemporaryDirectory(prefix='jemu-memory-') as temp:
        work = Path(temp)
        process = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--data-dir', str(work / 'library'), '--no-seed'], stdout=subprocess.PIPE, text=True)
        handle = None
        try:
            base = process.stdout.readline().strip()
            port = int(base.rsplit(':', 1)[1])
            token = re.search(r"DECKROOM_TOKEN='([^']+)'", urllib.request.urlopen(base).read().decode()).group(1)
            def post(path, data):
                req = urllib.request.Request(base + path, data=json.dumps(data).encode(), headers={'Content-Type':'application/json','X-Deckroom-Token':token})
                return json.load(urllib.request.urlopen(req))
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            psapi = ctypes.WinDLL('psapi', use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
            handle = kernel.OpenProcess(0x410, False, process.pid)
            def usage():
                stats = Memory(); stats.cb = ctypes.sizeof(stats)
                if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(stats), stats.cb):
                    raise ctypes.WinError(ctypes.get_last_error())
                return stats.WorkingSetSize
            baseline = usage()
            samples = [baseline]
            stopped = threading.Event()
            def monitor():
                while not stopped.wait(.02):
                    samples.append(usage())
            watcher = threading.Thread(target=monitor, daemon=True); watcher.start()
            html = (ROOT / 'starter/reference.html').read_bytes()
            paths = {'index.html':work / 'index.html', 'large.bin':work / 'large.bin'}
            paths['index.html'].write_bytes(html)
            chunk = os.urandom(64 * 1024)
            expected = hashlib.sha256()
            with paths['large.bin'].open('wb') as stream:
                for _ in range(48 * 16):
                    stream.write(chunk); expected.update(chunk)
            upload = post('/api/import-start', {'name':'Memory smoke', 'entry':'index.html'})['upload']
            for name, path in paths.items():
                connection = http.client.HTTPConnection('127.0.0.1', port)
                connection.putrequest('POST', '/api/import-file?upload='+upload+'&path='+name)
                connection.putheader('X-Deckroom-Token', token)
                connection.putheader('Content-Length', str(path.stat().st_size));connection.endheaders()
                with path.open('rb') as stream:
                    for block in iter(lambda:stream.read(64*1024),b''):connection.send(block)
                response = connection.getresponse(); assert response.status == 200, response.read(); response.read();connection.close()
            meta = post('/api/import-finish', {'upload':upload})
            deck = '/api/decks/'+meta['id']
            original_revision = meta['revision']
            changed = html.decode().replace('汇报目录','汇报目录（修改）')
            meta = post(deck+'/save', {'html':changed, 'baseRevision':meta['revision'],'changes':[{'type':'text','after':{'text':'test'}}]})
            assert all('changes' not in version for version in meta['versions'])
            assert json.load(urllib.request.urlopen(base+deck+'/status'))['revision'] == meta['revision']
            for _ in range(3):
                result = post(deck+'/export-edit', {'mode':'full','prepare':True,'baseRevision':meta['revision'],'scope':'current'})
                with urllib.request.urlopen(base+result['downloadUrl']) as source, (work/'full.zip').open('wb') as dest:
                    for block in iter(lambda:source.read(64*1024),b''):dest.write(block)
                with zipfile.ZipFile(work/'full.zip') as archive:
                    actual=hashlib.sha256()
                    with archive.open('document/large.bin') as source:
                        for block in iter(lambda:source.read(64*1024),b''):actual.update(block)
                    assert actual.hexdigest()==expected.hexdigest()
                    artifact=json.loads(archive.read('changes.json'))
                    assert artifact['editEvents'][-1]['after']['text']=='test'
            first=meta['versions'][0]['id']
            meta=post(deck+'/restore', {'version':first,'baseRevision':meta['revision']})
            assert meta['revision']==original_revision
            assert len(meta['versions'])==3
            # Cloud adapter uses a local mock folder: no third-party upload.
            sync = work / 'mock-sync'; sync.mkdir()
            post('/api/cloud/config', {'provider':'google-drive','folder':str(sync)})
            backup=post(deck+'/cloud-backup', {'baseRevision':meta['revision']})
            assert backup['status']=='written-to-sync-folder' and backup['remoteStatus']=='unknown'
            listing=json.load(urllib.request.urlopen(base+'/api/cloud/archives'))
            assert listing['items'][0]['id']==backup['id']
            restored=post('/api/cloud/restore', {'archive':backup['id']})
            assert restored['id'] != meta['id'] and len(restored['versions'])==3
            assert restored['revision']==meta['revision']
            stopped.set();watcher.join()
            samples.append(usage())
            peak=max(samples);delta=peak-baseline
            assert delta < 32*1024*1024, (baseline,peak,delta)
            print(json.dumps({'assetMiB':48,'exportCycles':3,'baselineWorkingSetMiB':round(baseline/1048576,1),'peakWorkingSetMiB':round(peak/1048576,1),'growthMiB':round(delta/1048576,1),'saveRestoreAndArtifactIntegrity':'passed','mockCloudArchiveRoundtrip':'passed'},ensure_ascii=False))
            post('/api/shutdown',{});process.wait(timeout=10)
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=10)
            if handle:kernel.CloseHandle(handle)
            process.stdout.close()


if __name__=='__main__':run()
