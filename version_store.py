"""Version snapshots: small files inside each ZIP, larger assets stored once by SHA-256.

Every save used to copy every image and video into a new ZIP. Unchanged assets now
live once in versions/blobs/<sha256>; a version ZIP lists them in BLOB_INDEX. ZIPs
written before this change have no index and are read exactly as before.
"""
import hashlib
import json
import os
import re
import secrets
import threading
import zipfile

BLOB_MIN = 64 * 1024
BLOB_INDEX = '.jemu-blobs.json'
CHUNK = 64 * 1024
_HASHES = {}
_HASH_LOCK = threading.Lock()


def file_hash(path):
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _HASH_LOCK:
        if key in _HASHES:
            return _HASHES[key]
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b''):
            digest.update(chunk)
    result = digest.hexdigest()
    with _HASH_LOCK:
        while len(_HASHES) >= 4096:
            del _HASHES[next(iter(_HASHES))]
        _HASHES[key] = result
    return result


def store_blob(path, blobs):
    digest = file_hash(path)
    target = blobs / digest
    if target.is_file() and target.stat().st_size == path.stat().st_size:
        return digest
    blobs.mkdir(parents=True, exist_ok=True)
    temporary = blobs / ('.tmp-' + secrets.token_hex(6))
    try:
        check = hashlib.sha256()
        with path.open('rb') as source, temporary.open('wb') as dest:
            for chunk in iter(lambda: source.read(CHUNK), b''):
                check.update(chunk); dest.write(chunk)
        if check.hexdigest() != digest:
            raise ValueError('素材在保存版本时被修改，请重试')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return digest


def write_snapshot(document, files, target, blobs):
    index = {}
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            name = path.relative_to(document).as_posix()
            size = path.stat().st_size
            # HTML stays inside the ZIP: page recovery and edit diffs read it directly.
            if name == BLOB_INDEX or (size >= BLOB_MIN and path.suffix.lower() not in ('.html', '.htm')):
                index[name] = {'sha256': store_blob(path, blobs), 'size': size}
            else:
                archive.write(path, name)
        if index:
            archive.writestr(BLOB_INDEX, json.dumps(index, ensure_ascii=False, separators=(',', ':')))


class Version:
    """Read one version ZIP, resolving assets kept in the shared blob folder."""

    def __init__(self, path, blobs):
        self.blobs = blobs
        self.archive = zipfile.ZipFile(path)
        self.index = {}
        try:
            members = {info.filename: info for info in self.archive.infolist() if not info.is_dir()}
            info = members.pop(BLOB_INDEX, None)
            if info is not None:
                if info.file_size > 16 * 1024 * 1024:
                    raise ValueError('版本素材索引过大')
                index = json.loads(self.archive.read(info))
                if not isinstance(index, dict) or not all(isinstance(item, dict) and isinstance(item.get('size'), int) and item['size'] >= 0
                                                          and isinstance(item.get('sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', item['sha256'])
                                                          for item in index.values()):
                    raise ValueError('版本素材索引无效')
                self.index = index
            self.members = members
        except BaseException:
            self.archive.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.archive.close()

    def sizes(self):
        return {**{name: info.file_size for name, info in self.members.items()}, **{name: item['size'] for name, item in self.index.items()}}

    def open(self, name):
        item = self.index.get(name)
        if item is None:
            return self.archive.open(self.members[name])
        path = self.blobs / item['sha256']
        if not path.is_file() or path.stat().st_size != item['size']:
            raise ValueError('版本素材缺失：' + name)
        return path.open('rb')

    def read(self, name):
        with self.open(name) as stream:
            return stream.read()
