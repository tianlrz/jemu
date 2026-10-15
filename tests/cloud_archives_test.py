"""Archive/adapter tests operate only on temporary local folders. No cloud upload."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from cloud_archives import FolderArchiveStore, create_archive, restore_archive, check_zip_limits


class CloudArchives(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jemu-cloud-test-')
        self.root = Path(self.temp.name)
        server.DATA = self.root / 'library'; server.DATA.mkdir()
        (self.root / 'sync').mkdir()
        self.store = FolderArchiveStore(self.root / 'sync')
        self.deck, meta = server.create_deck('复旦测试', 'index.html', [('index.html', b'<h1>original</h1>'), ('asset.bin', b'asset')])
        (self.deck / 'document/index.html').write_text('<h1>edited</h1>', encoding='utf8')
        self.meta = server.snapshot(self.deck, 'test edit', [{'type':'text','after':{'text':'edited'}}])
        self.archive_id = '20261015T120000Z-0123456789ab.jemu.zip'
        self.local = self.root / self.archive_id
        create_archive(self.deck, self.local, self.meta)
        self.stored = self.store.put(self.local, self.archive_id)

    def tearDown(self):
        self.temp.cleanup()

    def test_roundtrip_preserves_all_versions_and_changes(self):
        listing = self.store.list()
        self.assertEqual(listing['items'][0]['versionCount'], 2)
        self.assertFalse(listing['hasMore'])
        restored, meta = restore_archive(self.stored, server.DATA, server.safe_child, server.revision)
        self.assertNotEqual(restored, self.deck)
        self.assertEqual(meta['revision'], self.meta['revision'])
        self.assertEqual((restored / 'document/index.html').read_text(), '<h1>edited</h1>')
        for version in self.meta['versions']:
            for suffix in ('.zip', '.changes.json'):
                name = version['id'] + suffix
                self.assertEqual((restored/'versions'/name).read_bytes(), (self.deck/'versions'/name).read_bytes())
        self.assertEqual((self.deck/'meta.json').read_bytes(), json.dumps(self.meta,ensure_ascii=False,indent=2).encode('utf8'))

    def tampered(self, kind):
        target = self.root / (kind+'.zip')
        with zipfile.ZipFile(self.local) as src, zipfile.ZipFile(target,'w') as dest:
            manifest=json.loads(src.read('manifest.json'))
            for info in src.infolist():
                if info.filename=='manifest.json':continue
                content=src.read(info)
                if kind=='checksum' and info.filename=='document/index.html':content=b'corrupt'
                dest.writestr(info.filename,content)
            if kind=='traversal':
                dest.writestr('../outside.txt', b'outside')
                manifest['files'].append({'path':'../outside.txt','size':7,'sha256':hashlib.sha256(b'outside').hexdigest()})
            dest.writestr('manifest.json',json.dumps(manifest))
        return target

    def test_rejects_corruption_without_creating_partial_deck(self):
        before = set(server.DATA.glob('deck-*'))
        with self.assertRaises(ValueError):restore_archive(self.tampered('checksum'), server.DATA, server.safe_child, server.revision)
        self.assertEqual(before, set(server.DATA.glob('deck-*')))

    def test_rejects_archive_path_traversal(self):
        with self.assertRaises(ValueError):restore_archive(self.tampered('traversal'), server.DATA, server.safe_child, server.revision)
        self.assertFalse((server.DATA/'outside.txt').exists())

    def test_duplicate_backup_cannot_replace_existing_archive(self):
        with self.assertRaises(ValueError):self.store.put(self.local,self.archive_id)
        self.assertEqual(self.local.read_bytes(),self.stored.read_bytes())

    def test_listing_does_not_open_archive_or_hydrate_cloud_zip(self):
        with patch('cloud_archives.zipfile.ZipFile', side_effect=AssertionError('Listing opened a ZIP')):
            listing = self.store.list()
        self.assertEqual(listing['items'][0]['id'], self.archive_id)

    def test_invalid_cloud_path_and_identifier(self):
        with self.assertRaises(ValueError):FolderArchiveStore(self.root/'missing')
        with self.assertRaises(ValueError):self.store.path('../meta.json')

    def test_bounds_central_directory_before_loading_zip(self):
        path=self.root/'invalid.zip'
        import struct
        path.write_bytes(struct.pack('<4s4H2LH',b'PK\x05\x06',0,0,10002,10002,0,0,0))
        with self.assertRaises(ValueError):check_zip_limits(path)


if __name__ == '__main__':unittest.main()
