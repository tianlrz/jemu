"""Portable archives and a sync-folder storage adapter. No background sync or SDK."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import heapq
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import tempfile
import zipfile

CHUNK = 64 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBERS = 10000
MAX_MANIFEST = 8 * 1024 * 1024


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class FolderArchiveStore:
    """Writes only Jemu's archive subtree; a separate cloud client handles upload."""
    provider = 'sync-folder'

    def __init__(self, folder):
        self.folder = Path(folder).expanduser()
        if not self.folder.is_absolute() or not self.folder.is_dir():
            raise ValueError('请选择已经存在、可访问的云盘同步文件夹')
        self.folder = self.folder.resolve()

    @property
    def root(self):
        return self.folder / 'Jemu Archives'

    def path(self, archive_id):
        if not isinstance(archive_id, str) or not re.fullmatch(r'\d{8}T\d{6}Z-[a-f0-9]{12}\.jemu\.zip', archive_id):
            raise ValueError('存档标识无效')
        target = self.root / archive_id
        if target.resolve().parent != self.root.resolve() or target.is_symlink():
            raise ValueError('存档路径无效')
        return target

    def put(self, source, archive_id, manifest=None):
        self.root.mkdir(exist_ok=True)
        if self.root.is_symlink():
            raise ValueError('存档目录不能是链接')
        target = self.path(archive_id)
        if target.exists():
            raise ValueError('此存档已经存在，请重试')
        temporary = target.with_suffix('.uploading')
        try:
            with source.open('rb') as src, temporary.open('xb') as dest:
                shutil.copyfileobj(src, dest, CHUNK)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        if manifest is None:
            with zipfile.ZipFile(source) as archive:
                manifest = read_manifest(archive)
        summary = {'id': archive_id, 'name': manifest['deck']['name'], 'created': manifest['created'], 'versionCount': manifest['versionCount'], 'bytes': target.stat().st_size}
        index = target.with_suffix('.json')
        pending = index.with_suffix('.pending')
        try:
            pending.write_text(json.dumps(summary, ensure_ascii=False, separators=(',', ':')), encoding='utf8')
            os.replace(pending, index)
        finally:
            pending.unlink(missing_ok=True)
        return target

    def list(self, limit=50, offset=0):
        # Metadata is bounded and read only on explicit refresh.
        if not self.root.exists():
            return {'items': [], 'hasMore': False}
        paths = heapq.nlargest(offset + limit + 1, (p for p in self.root.glob('*.jemu.json') if p.is_file() and not p.is_symlink()), key=lambda p: p.name)
        items = []
        for path in paths[offset:offset + limit]:
            try:
                if path.stat().st_size > 16 * 1024:
                    raise ValueError('存档索引过大')
                summary = json.loads(path.read_text(encoding='utf8'))
                archive_id = path.with_suffix('.zip').name
                if not isinstance(summary, dict) or summary.get('id') != archive_id or not all(isinstance(summary.get(key), str) for key in ('name', 'created')) or not all(isinstance(summary.get(key), int) for key in ('versionCount', 'bytes')):
                    raise ValueError('存档索引格式无效')
                self.path(archive_id)
                items.append({key: summary[key] for key in ('id', 'name', 'created', 'versionCount', 'bytes')})
            except (ValueError, KeyError, OSError, zipfile.BadZipFile):
                items.append({'id': path.with_suffix('.zip').name, 'name': '无法读取的存档', 'invalid': True, 'bytes': path.stat().st_size})
        return {'items': items, 'hasMore': len(paths) > offset + limit}


def read_manifest(archive):
    info = archive.getinfo('manifest.json')
    if info.file_size > MAX_MANIFEST:
        raise ValueError('存档清单过大')
    manifest = json.loads(archive.read(info))
    if not isinstance(manifest, dict) or manifest.get('format') != 'jemu.archive' or manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('files'), list):
        raise ValueError('不支持的简幕存档格式')
    if not isinstance(manifest.get('deck'), dict) or not all(isinstance(manifest['deck'].get(key), str) for key in ('id', 'name', 'entry', 'revision')) or not isinstance(manifest.get('created'), str) or not isinstance(manifest.get('versionCount'), int):
        raise ValueError('存档信息不完整')
    return manifest


def check_zip_limits(path):
    # Check central-directory size BEFORE ZipFile loads it into memory.
    with path.open('rb') as stream:
        stream.seek(0, 2)
        length = stream.tell()
        if length > MAX_ARCHIVE_BYTES + MAX_MANIFEST:
            raise ValueError('存档超过 2 GiB')
        stream.seek(max(0, length - 65557))
        tail = stream.read(65557)
    index = tail.rfind(b'PK\x05\x06')
    if index < 0 or index + 22 > len(tail):
        raise ValueError('存档不完整')
    _, disk, central_disk, _, count, central_size, _, comment_size = struct.unpack_from('<4s4H2LH', tail, index)
    if disk or central_disk or count > MAX_MEMBERS + 1 or central_size > 16 * 1024 * 1024 or index + 22 + comment_size != len(tail):
        raise ValueError('存档目录过大或格式不支持')


def create_archive(directory, target, meta):
    paths = [directory / 'meta.json']
    paths += sorted(p for part in ('document', 'versions') for p in (directory / part).rglob('*') if p.is_file())
    if (directory / 'export-context.json').is_file():
        paths.append(directory / 'export-context.json')
    if len(paths) > MAX_MEMBERS or sum(p.stat().st_size for p in paths) > MAX_ARCHIVE_BYTES:
        raise ValueError('历史过多：单次存档限 2 GiB、10,000 个文件，请先分开整理历史')
    manifest = {'format': 'jemu.archive', 'schemaVersion': 1, 'created': timestamp(), 'deck': {'id': meta['id'], 'name': meta['name'], 'entry': meta['entry'], 'revision': meta['revision']}, 'versionCount': len(meta['versions']), 'files': []}
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError('存档不支持指向稿件目录外的文件')
            name = path.relative_to(directory).as_posix()
            # Old versions are already compressed; do not spend CPU recompressing ZIPs.
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_STORED if path.suffix == '.zip' else zipfile.ZIP_DEFLATED
            info.file_size = path.stat().st_size
            digest = hashlib.sha256()
            with path.open('rb') as src, archive.open(info, 'w') as dest:
                for chunk in iter(lambda: src.read(CHUNK), b''):
                    digest.update(chunk); dest.write(chunk)
            manifest['files'].append({'path': name, 'size': info.file_size, 'sha256': digest.hexdigest()})
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, separators=(',', ':')))
    return manifest


def restore_archive(source, library, safe_child, revision):
    """Validate in staging and restore as a NEW deck; never overwrite local work."""
    check_zip_limits(source)
    with zipfile.ZipFile(source) as archive, tempfile.TemporaryDirectory(prefix='.archive-restore-', dir=library) as temp:
        staged = Path(temp) / 'deck'
        staged.mkdir()
        manifest = read_manifest(archive)
        members = archive.infolist()
        if len(members) > MAX_MEMBERS + 1 or len(manifest['files']) > MAX_MEMBERS:
            raise ValueError('存档文件过多')
        expected = {}
        folded = set()
        total = 0
        for item in manifest['files']:
            if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not isinstance(item.get('size'), int) or item['size'] < 0 or not isinstance(item.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
                raise ValueError('存档文件清单无效')
            name = item['path']
            if name in expected or name.casefold() in folded or name == 'manifest.json':
                raise ValueError('存档存在重复文件')
            if name != 'meta.json' and name != 'export-context.json' and not name.startswith(('document/', 'versions/')):
                raise ValueError('存档包含不支持的文件')
            safe_child(staged, name)
            expected[name] = item
            folded.add(name.casefold())
        if {item.filename for item in members} != {*expected, 'manifest.json'} or len(members) != len(expected) + 1:
            raise ValueError('存档文件清单不一致')
        for info in members:
            if info.filename == 'manifest.json':
                continue
            item = expected[info.filename]
            total += info.file_size
            if info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000 or info.file_size != item['size'] or total > MAX_ARCHIVE_BYTES:
                raise ValueError('存档文件大小或类型无效')
            target = safe_child(staged, info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with archive.open(info) as src, target.open('wb') as dest:
                for chunk in iter(lambda: src.read(CHUNK), b''):
                    digest.update(chunk); dest.write(chunk)
            if digest.hexdigest() != item['sha256']:
                raise ValueError('存档校验失败，请等待云盘下载完整或重新获取存档')
        meta_file = staged / 'meta.json'
        if meta_file.stat().st_size > MAX_MANIFEST:
            raise ValueError('存档版本信息过大')
        meta = json.loads(meta_file.read_text(encoding='utf8'))
        if not isinstance(meta, dict) or not isinstance(meta.get('entry'), str) or not isinstance(meta.get('name'), str) or not isinstance(meta.get('versions'), list) or not meta['versions'] or len(meta['versions']) > MAX_MEMBERS:
            raise ValueError('存档版本信息无效')
        entry = meta['entry']
        if not entry.lower().endswith(('.html', '.htm')) or not safe_child(staged / 'document', entry).is_file():
            raise ValueError('存档缺少 HTML 主文件')
        for version in meta['versions']:
            version_id = version.get('id') if isinstance(version, dict) else None
            if not isinstance(version_id, str) or not re.fullmatch(r'[0-9a-f-]+', version_id) or not (staged / 'versions' / (version_id + '.zip')).is_file():
                raise ValueError('存档缺少历史版本')
        if len(meta['versions']) != manifest['versionCount'] or revision(staged) != manifest['deck']['revision'] or meta['revision'] != manifest['deck']['revision']:
            raise ValueError('存档内容与修订信息不一致')
        meta['id'] = 'deck-' + secrets.token_hex(5)
        meta['name'] = str(meta['name'])[:110] + ' · 恢复副本'
        meta['restoredAt'] = timestamp()
        meta['updated'] = meta['restoredAt']
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf8')
        destination = library / meta['id']
        staged.rename(destination)
        return destination, meta
