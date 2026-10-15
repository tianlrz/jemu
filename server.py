"""Jemu (简幕): local HTML presentation workspace. Python standard library only."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
from pathlib import Path, PurePosixPath
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse, urlencode
import webbrowser
import zipfile
import automation
import render_exports
import native_window
from rendered_pages import recover as recover_rendered_pages
from edit_exports import build_artifacts, full_bundle
from cloud_archives import FolderArchiveStore, create_archive, restore_archive
from credentials import load_secret, save_secret
from webdav_store import NutstoreArchiveStore, NUTSTORE_URL

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT/'VERSION').read_text(encoding='utf8').strip() if (ROOT/'VERSION').is_file() else 'dev'
MAX_BYTES = 100 * 1024 * 1024
LOCK = threading.RLock()
TOKEN = secrets.token_urlsafe(32)
DATA: Path
SERVER: ThreadingHTTPServer
REVISION_CACHE = {}


def cloud_config():
    path = DATA / '.settings' / 'cloud.json'
    return json.loads(path.read_text(encoding='utf8')) if path.is_file() else {'provider': 'nutstore', 'folder': '', 'username': ''}


def public_cloud_config():
    config=cloud_config()
    if config.get('provider')=='nutstore':
        try:load_secret(DATA/'.settings'/'webdav.secret',config.get('username',''));stored=True
        except ValueError:stored=False
        return {'provider':'nutstore','folder':'','username':config.get('username',''),'endpoint':NUTSTORE_URL,'passwordStored':stored,'mode':'webdav','configured':stored,'remoteStatus':'unknown','settingsRevision':cloud_config_revision()}
    return {**config,'mode':'sync-folder','configured':bool(config.get('folder')),'remoteStatus':'unknown','settingsRevision':cloud_config_revision()}


def cloud_config_revision():
    digest=hashlib.sha256(json.dumps(cloud_config(),sort_keys=True).encode())
    secret=DATA/'.settings/webdav.secret'
    if secret.is_file():digest.update(str(secret.stat().st_mtime_ns).encode())
    return digest.hexdigest()[:20]


def cloud_store():
    config = cloud_config()
    if config.get('provider')=='nutstore':
        username=config.get('username','')
        return NutstoreArchiveStore(username,load_secret(DATA/'.settings'/'webdav.secret',username))
    if not config.get('folder'):
        raise ValueError('请先设置云盘同步文件夹')
    store = FolderArchiveStore(config['folder'])
    if store.folder.is_relative_to(DATA.resolve()):
        raise ValueError('存档位置应位于本地稿件库之外')
    return store


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_mime(path):
    # Windows registry associations can incorrectly label .mjs/.js as text/plain.
    # nosniff and ES modules require a JavaScript MIME type on every machine.
    if path.suffix.lower() in ('.js','.mjs'):return 'text/javascript'
    return mimetypes.guess_type(path.name)[0] or 'application/octet-stream'


def atomic_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + secrets.token_hex(4))
    try:
        temp.write_bytes(value)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def safe_child(root, name):
    name = str(name)
    if "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("文件路径不合法")
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or any(p in ("..", ".") or p.endswith((" ", ".")) for p in parts):
        raise ValueError("文件路径不合法")
    for part in parts:
        stem = part.split(".")[0].upper()
        if stem in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
            raise ValueError("文件名不合法")
    result = (root / name).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("文件路径超出稿件目录")
    return result


def deck_dir(deck_id):
    if not deck_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in deck_id):
        raise ValueError("稿件标识不合法")
    directory = DATA / deck_id
    if not (directory / "meta.json").is_file():
        raise FileNotFoundError("未找到稿件")
    return directory


def read_meta(directory):
    return json.loads((directory / "meta.json").read_text(encoding="utf-8"))


def tree_files(document):
    files = sorted(p for p in document.rglob("*") if p.is_file())
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(document.resolve()):
            raise ValueError("稿件不支持指向目录外的链接")
    return files


def revision(directory):
    digest = hashlib.sha256()
    size = 0
    document = directory / "document"
    files = tree_files(document)
    fingerprint = []
    for path in files:
        stat = path.stat()
        size += stat.st_size
        if size > MAX_BYTES:
            raise ValueError("稿件超过 100 MB，请先移走不需要的文件")
        fingerprint.append((path.relative_to(document).as_posix(), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    cached = REVISION_CACHE.get(str(document))
    if cached and cached[0] == fingerprint:
        return cached[1]
    for path in files:
        digest.update(path.relative_to(document).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b''):
                digest.update(chunk)
        digest.update(b"\x00")
    result = digest.hexdigest()
    if len(fingerprint) <= 10000:
        REVISION_CACHE.pop(str(document), None)
        while len(REVISION_CACHE) >= 8:
            del REVISION_CACHE[next(iter(REVISION_CACHE))]
        REVISION_CACHE[str(document)] = (fingerprint, result)
    return result


def archive_document(directory, target):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in tree_files(directory / "document"):
            archive.write(path, path.relative_to(directory / "document").as_posix())


def snapshot(directory, label, changes=None, *, force=False):
    meta = read_meta(directory)
    rev = revision(directory)
    if meta.get("revision") == rev and not force:
        return meta
    version_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    target = directory / "versions" / (version_id + ".zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.zip.tmp')
    try:
        archive_document(directory, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    changes = changes if isinstance(changes, list) else []
    atomic_json(target.with_suffix('.changes.json'), changes)
    item = {"id": version_id, "created": now(), "label": str(label)[:120], "revision": rev, "changeCount": len(changes)}
    meta.setdefault("versions", []).append(item)
    meta.update(revision=rev, updated=item["created"])
    atomic_json(directory / "meta.json", meta)
    return meta


def current_meta(directory):
    meta = read_meta(directory)
    if revision(directory) != meta.get("revision"):
        meta = snapshot(directory, "外部文件修改")
    return meta


def public_meta(directory, meta):
    context_file = directory / "export-context.json"
    context = json.loads(context_file.read_text(encoding="utf-8")) if context_file.is_file() else {}
    versions = [{k: v for k, v in item.items() if k != 'changes'} for item in meta['versions']]
    return {**meta, "versions": versions, "path": str(directory / "document"), "entryPath": str(directory / "document" / meta["entry"]),
            "requestPath": str(directory / "ai-request.md"), "exportContext": context, "url": f"/documents/{meta['id']}/{quote(meta['entry'], safe='/')}"}


def create_deck(name, entry, files):
    if not entry.lower().endswith((".html", ".htm")):
        raise ValueError("请选择 HTML 主文件")
    deck_id = "deck-" + secrets.token_hex(5)
    directory = DATA / deck_id
    document = directory / "document"
    validated, size = [], 0
    for name_, content in files:
        target = safe_child(document, name_)
        size += len(content)
        if size > MAX_BYTES:
            raise ValueError("稿件不能超过 100 MB")
        validated.append((target, content))
    entry_path = safe_child(document, entry)
    if entry_path not in [p for p, _ in validated]:
        raise ValueError("未找到选择的 HTML 主文件")
    document.mkdir(parents=True)
    for target, content in validated:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    atomic_json(directory / "meta.json", {"id": deck_id, "name": str(name).strip()[:120] or "未命名稿件", "entry": entry, "created": now(), "versions": []})
    return directory, snapshot(directory, "导入稿件")


def seed_template():
    if any(DATA.glob("*/meta.json")):
        return
    source = ROOT / "starter"
    if not source.is_dir():
        source = ROOT.parent / "fudan-slides" / "assets"
    if (source / "reference.html").is_file():
        create_deck("复旦 · 版式参考", "reference.html", [(name, (source / name).read_bytes()) for name in ("reference.html", "logo-blue.png", "logo-white.png")])


class Handler(BaseHTTPRequestHandler):
    server_version = "Jemu/0.1"

    def log_message(self, *_):
        pass

    def send(self, value, status=200, content_type="application/json; charset=utf-8", disposition=None):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(raw)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            host = self.headers.get("Host", "")
            if host not in {f"127.0.0.1:{SERVER.server_port}", f"localhost:{SERVER.server_port}"}:
                return self.send({"error": "只允许本机访问"}, 403)
            path = unquote(urlparse(self.path).path)
            parts = path.strip("/").split("/")
            with LOCK:
                if path == "/api/health":
                    return self.send({"ok": True, "app": "deckroom", "name": "Jemu", "displayName": "简幕", "version":VERSION,"library": str(DATA)})
                if path == '/api/capabilities':return self.send(automation.capabilities())
                if path == '/api/render-capabilities':return self.send(render_exports.capabilities())
                if path == '/api/window/status':return self.send(native_window.status(parse_qs(urlparse(self.path).query).get('window',[''])[0]))
                if path == '/api/settings':return self.send({'backup':public_cloud_config()})
                if path == '/api/settings/revision':return self.send({'revision':cloud_config_revision()})
                if path == '/api/ui/state':return self.send({'clients':automation.clients()})
                if path.startswith('/api/ui/commands/'):
                    return self.send(automation.result(parts[-1]))
                if path == "/api/decks":
                    items = [public_meta(p.parent, current_meta(p.parent)) for p in DATA.glob("*/meta.json")]
                    return self.send(sorted(items, key=lambda m: m.get("updated", ""), reverse=True))
                if path in ('/api/cloud/config','/api/settings/backup'):
                    return self.send(public_cloud_config())
                if path == '/api/cloud/archives':
                    query = parse_qs(urlparse(self.path).query)
                    offset = max(0, min(10000, int(query.get('offset', ['0'])[0])))
                    store=cloud_store()
                    if store.provider=='nutstore':
                        return self.send(store.list(offset=offset,month=query.get('month',[None])[0]))
                    return self.send(store.list(offset=offset))
                if len(parts) >= 3 and parts[:2] == ["api", "decks"]:
                    directory = deck_dir(parts[2])
                    if len(parts) in (5,6) and parts[3]=='render-jobs':
                        if len(parts)==5:return self.send(render_exports.status(parts[2],parts[4]))
                        if parts[5]=='download':
                            target,kind=render_exports.download(parts[2],parts[4])
                            mime='application/pdf' if kind=='pdf' else 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
                            return self.send_file(target,mime,'attachment; filename="jemu-deck.'+kind+'"')
                    if len(parts)==4 and parts[3]=='files':
                        return self.send({'revision':revision(directory),'files':[{'path':p.relative_to(directory/'document').as_posix(),'bytes':p.stat().st_size} for p in tree_files(directory/'document')]})
                    if len(parts)==4 and parts[3]=='rendered-pages':
                        meta=current_meta(directory);entry=parse_qs(urlparse(self.path).query).get('entry',[meta['entry']])[0]
                        target=safe_child(directory/'document',entry)
                        if target.suffix.lower() not in ('.html','.htm'):raise ValueError('只能恢复 HTML 页面')
                        return self.send(recover_rendered_pages(directory,meta,target.relative_to(directory/'document').as_posix()))
                    if len(parts)==4 and parts[3]=='file':
                        name=parse_qs(urlparse(self.path).query).get('path',[''])[0]
                        return self.send_file(safe_child(directory/'document',name),'application/octet-stream')
                    if len(parts) == 4 and parts[3] in ('download-full', 'download-trace'):
                        full = parts[3] == 'download-full'
                        name = 'jemu-full.zip' if full else 'jemu-edits.md'
                        return self.send_file(directory / 'exports' / name, 'application/zip' if full else 'text/markdown; charset=utf-8', 'attachment; filename="' + name + '"')
                    if len(parts) == 4 and parts[3] == 'status':
                        return self.send({'id': parts[2], 'revision': revision(directory)})
                    meta = current_meta(directory)
                    if len(parts) == 4 and parts[3] == "export":
                        with tempfile.TemporaryDirectory(prefix='jemu-export-') as temp:
                            target = Path(temp) / 'jemu-deck.zip'
                            archive_document(directory, target)
                            return self.send_file(target, 'application/zip', 'attachment; filename="jemu-deck.zip"')
                    if len(parts)==3:return self.send(public_meta(directory, meta))
                    return self.send({'error':'未找到接口'},404)
                if len(parts) >= 3 and parts[0] == "documents":
                    directory = deck_dir(parts[1])
                    target = safe_child(directory / "document", "/".join(parts[2:]))
                    if target.is_dir():
                        target = next((target / name for name in ('index.html', 'index.htm') if (target / name).is_file()), target / 'index.html')
                        if not path.endswith('/'):
                            self.send_response(301);self.send_header('Location', quote(path, safe='/') + '/');self.send_header('Content-Length','0');self.end_headers();return
                    mime = content_mime(target)
                    # Preserve BOM/meta/@charset detection for imported legacy documents.
                    return self.send_file(target, mime)
                if path in ("/", "/index.html", "/settings", "/settings.html"):
                    page='settings.html' if path in ('/settings','/settings.html') else 'index.html'
                    raw = (ROOT / "web" / page).read_text(encoding="utf-8").replace("__SESSION_TOKEN__", TOKEN)
                    window_key=parse_qs(urlparse(self.path).query).get('window',[''])[0]
                    if page=='index.html' and window_key in native_window.WINDOWS:raw=raw.replace('<title>Jemu · 简幕</title>','<title>'+native_window.title(window_key)+'</title>')
                    return self.send(raw.encode("utf-8"), content_type="text/html; charset=utf-8")
                if path in ("/app.js", "/app.css", "/resources.js", "/compat.js", "/dynamic-deck.js", "/export-layout.js", "/fullscreen.js", "/cloud.js", "/automation.js", "/settings.js", "/settings.css", "/presenting.js", "/presenter.html", "/presenter.js", "/presenter.css"):
                    target = ROOT / "web" / path.lstrip("/")
                    return self.send(target.read_bytes(), content_type=content_mime(target) + "; charset=utf-8")
                self.send({"error": "未找到文件"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except FileNotFoundError:
            self.send({"error": "未找到文件"}, 404)
        except (ValueError, OSError) as error:
            self.send({"error": str(error)}, 400)

    def do_POST(self):
        # Serialize body decoding too: concurrent large requests must not multiply RAM use.
        with LOCK:
            self.post_locked()

    def send_file(self, path, content_type, disposition=None):
        with path.open('rb') as stream:
            size = path.stat().st_size
            start, end, partial = 0, size - 1, False
            requested = self.headers.get('Range') if not self.headers.get('If-Range') else None
            if requested:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
                if match and (match[1] or match[2]):
                    start = int(match[1]) if match[1] else max(0, size - int(match[2]))
                    end = min(size - 1, int(match[2])) if match[1] and match[2] else size - 1
                else:
                    start = size
                if start >= size or start > end:
                    self.send_response(416);self.send_header('Content-Range', f'bytes */{size}');self.send_header('Content-Length', '0');self.end_headers();return
                partial = True
            length = max(0, end - start + 1)
            self.send_response(206 if partial else 200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(length))
            self.send_header('Accept-Ranges', 'bytes')
            if partial:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            if disposition:
                self.send_header('Content-Disposition', disposition)
            self.end_headers()
            if self.command != 'HEAD':
                stream.seek(start)
                while length:
                    chunk = stream.read(min(length, 64 * 1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk);length -= len(chunk)

    def post_locked(self):
        try:
            if not secrets.compare_digest(self.headers.get("X-Deckroom-Token", ""), TOKEN):
                return self.send({"error": "会话验证失败，请重新打开应用"}, 403)
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BYTES * 1.4 or length < 0:
                return self.send({"error": "文件过大"}, 413)
            parts = urlparse(self.path).path.strip("/").split("/")
            if len(parts)==4 and parts[:2]==['api','decks'] and parts[3]=='file':
                return self.write_document_file(parts[2],length)
            if parts == ['api', 'import-file']:
                return self.import_file(length)
            if length > 16 * 1024 * 1024:
                return self.send({'error': '请求超过 16 MB。大稿件请使用新版界面逐文件导入。'}, 413)
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data,dict):raise ValueError('请求内容必须是 JSON 对象')
            with LOCK:
                if parts==['api','window','attach']:return self.send(native_window.attach(data.get('window')))
                if parts==['api','window','fullscreen']:return self.send(native_window.fullscreen(data.get('window'),data.get('enabled')))
                if parts==['api','open-desktop']:
                    deck=data.get('deck');suffix=''
                    if deck:deck_dir(deck);suffix='?'+urlencode({'deck':deck})
                    open_window(f'http://127.0.0.1:{SERVER.server_port}/'+suffix)
                    return self.send({'opened':True})
                if parts==['api','ui','sync']:return self.send({**automation.sync(data),'settingsRevision':cloud_config_revision()})
                if parts==['api','ui','commands']:return self.send(automation.enqueue(data),202)
                if data.get('expectedSettingsRevision') and data['expectedSettingsRevision']!=cloud_config_revision():
                    return self.send({'error':'设置已在 CLI 或其他窗口中改变，请重新打开设置再操作'},409)
                if parts in (['api', 'cloud', 'config'],['api','settings','backup']):
                    provider = data.get('provider', 'google-drive')
                    if provider=='nutstore':
                        username=str(data.get('username','')).strip()
                        password=data.get('password','')
                        if not password:password=load_secret(DATA/'.settings'/'webdav.secret',username)
                        # Validate locally, without sending credentials before Test/Backup is clicked.
                        NutstoreArchiveStore(username,password)
                        save_secret(DATA/'.settings'/'webdav.secret',username,password,atomic_write)
                        atomic_json(DATA/'.settings'/'cloud.json',{'provider':'nutstore','username':username,'folder':''})
                        return self.send(public_cloud_config())
                    if provider not in ('google-drive', 'onedrive', 'quark', 'dropbox', 'folder'):
                        raise ValueError('请选择有效的云盘类型')
                    folder = str(data.get('folder', '')).strip()
                    if folder:
                        store = FolderArchiveStore(folder)
                        if store.folder.is_relative_to(DATA.resolve()):
                            raise ValueError('存档位置应位于本地稿件库之外')
                        folder = str(store.folder)
                    config = {'provider': provider, 'folder': folder}
                    atomic_json(DATA / '.settings' / 'cloud.json', config)
                    return self.send(public_cloud_config())
                if parts == ['api','cloud','disconnect']:
                    (DATA/'.settings'/'webdav.secret').unlink(missing_ok=True)
                    atomic_json(DATA/'.settings'/'cloud.json',{'provider':'nutstore','folder':'','username':''})
                    return self.send(public_cloud_config())
                if parts == ['api','cloud','test']:
                    store=cloud_store()
                    return self.send(store.test() if store.provider=='nutstore' else {'ok':True,'message':'同步目录可以访问。云端上传由云盘客户端负责。'})
                if parts == ['api', 'cloud', 'restore']:
                    store = cloud_store()
                    if store.provider=='nutstore':
                        with store.materialize(data.get('archive')) as source:directory,meta=restore_archive(source,DATA,safe_child,revision)
                    else:directory, meta = restore_archive(store.path(data.get('archive')), DATA, safe_child, revision)
                    return self.send(public_meta(directory, meta), 201)
                if parts == ['api', 'cloud', 'open-folder']:
                    store = cloud_store()
                    if store.provider=='nutstore':return self.send({'error':'WebDAV 存档请在云端列表中查看'},400)
                    if os.name == 'nt':
                        os.startfile(str(store.root if store.root.is_dir() else store.folder))
                    return self.send({'ok': True})
                if parts == ["api", "import"]:
                    files = [(f["path"], base64.b64decode(f["data"], validate=True)) for f in data["files"]]
                    directory, meta = create_deck(data.get("name", ""), data["entry"], files)
                    return self.send(public_meta(directory, meta), 201)
                if parts == ['api', 'import-start']:
                    # Staging is on disk; each file is uploaded and released sequentially.
                    for old in DATA.glob('.import-*'):
                        if old.is_dir() and not old.is_symlink() and old.resolve().parent == DATA.resolve() and time.time() - old.stat().st_mtime > 3600:
                            shutil.rmtree(old)
                    if len(list(DATA.glob('.import-*'))) >= 4:
                        raise ValueError('有未完成的导入，请稍后重试')
                    upload = secrets.token_hex(12)
                    staged = DATA / ('.import-' + upload)
                    staged.mkdir()
                    atomic_json(staged / 'upload.json', {'name': str(data.get('name', ''))[:120], 'entry': data['entry'], 'size': 0})
                    return self.send({'upload': upload}, 201)
                if parts in (['api', 'import-finish'], ['api', 'import-cancel']):
                    staged = self.import_directory(data.get('upload', ''))
                    if parts[-1] == 'import-cancel':
                        shutil.rmtree(staged)
                        return self.send({'ok': True})
                    upload = json.loads((staged / 'upload.json').read_text(encoding='utf8'))
                    entry = upload['entry']
                    if not entry.lower().endswith(('.html', '.htm')) or not safe_child(staged / 'document', entry).is_file():
                        raise ValueError('未找到 HTML 主文件')
                    deck_id = 'deck-' + secrets.token_hex(5)
                    directory = DATA / deck_id
                    atomic_json(staged / 'meta.json', {'id': deck_id, 'name': upload['name'].strip() or '未命名稿件', 'entry': entry, 'created': now(), 'versions': []})
                    (staged / 'upload.json').unlink()
                    staged.rename(directory)
                    return self.send(public_meta(directory, snapshot(directory, '导入稿件')), 201)
                if parts == ["api", "shutdown"]:
                    self.send({"ok": True})
                    threading.Thread(target=SERVER.shutdown, daemon=True).start()
                    return
                if len(parts) != 4 or parts[:2] != ["api", "decks"]:
                    return self.send({"error": "未找到操作"}, 404)
                directory = deck_dir(parts[2])
                action = parts[3]
                meta = current_meta(directory)
                if action in ("render-start", "prepare", "save", "restore", "export-edit", "cloud-backup", "delete-file") and data.get("baseRevision") != meta["revision"]:
                    return self.send({"error": "文件已在外部更新。请先载入外部版本，避免覆盖。", "meta": public_meta(directory, meta)}, 409)
                if action=='delete-file':
                    name=data.get('path','')
                    target=safe_child(directory/'document',name)
                    if target==safe_child(directory/'document',meta['entry']):raise ValueError('不能删除作品入口文件')
                    if not target.is_file():raise FileNotFoundError()
                    target.unlink()
                    return self.send(public_meta(directory,snapshot(directory,'CLI 删除文件：'+name)))
                if action=='render-start':
                    return self.send(render_exports.start(directory,meta,data,tree_files(directory/'document'),safe_child,lambda:revision(directory)),202)
                if action == 'cloud-backup':
                    store = cloud_store()
                    archive_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + secrets.token_hex(6) + '.jemu.zip'
                    with tempfile.TemporaryDirectory(prefix='jemu-archive-') as temp:
                        target = Path(temp) / archive_id
                        manifest = create_archive(directory, target, meta)
                        # External AI can write without our lock; do not publish an inconsistent archive.
                        if revision(directory) != meta['revision']:
                            raise ValueError('备份期间原稿有外部修改，请载入后重试')
                        stored = store.put(target, archive_id, manifest)
                        if isinstance(stored,dict):return self.send(stored)
                    return self.send({'id': archive_id, 'versionCount': manifest['versionCount'], 'bytes': stored.stat().st_size, 'status': 'written-to-sync-folder', 'remoteStatus': 'unknown'})
                if action == 'view-mode':
                    mode = data.get('mode')
                    if mode not in ('auto', 'native', 'slides'):
                        raise ValueError('展示方式无效')
                    meta['viewMode'] = mode
                    atomic_json(directory / 'meta.json', meta)
                    return self.send(public_meta(directory, meta))
                if action == 'prepare':
                    entry = data.get('entry') or meta['entry']
                    target = safe_child(directory / 'document', entry)
                    if not entry.lower().endswith(('.html', '.htm')) or not target.is_file():
                        raise ValueError('只能准备稿件目录内已有的 HTML 页面')
                    html = data.get('html')
                    if html is not None and (not isinstance(html,str) or len(html.encode('utf-8')) > 10*1024*1024):
                        raise ValueError('HTML 内容过大')
                    # current_meta has already archived any external edits. Never replace
                    # the working copy before the exact source has a recoverable snapshot.
                    receipt = meta.get('jemuVersion') or {}
                    if html is None and receipt:
                        return self.send(public_meta(directory,meta))
                    encoded = html.encode('utf-8') if html is not None else None
                    if encoded is not None and receipt.get('entry') == entry and target.stat().st_size == len(encoded) and target.read_bytes() == encoded:
                        return self.send(public_meta(directory,meta))
                    original = meta['versions'][-1]['id']
                    if html is not None:
                        atomic_write(target,encoded)
                    meta = snapshot(directory,'Jemu 工作版本 · 完整幻灯片' if html is not None else 'Jemu 工作版本',force=not receipt)
                    meta['jemuVersion'] = {'entry':entry,'sourceVersion':original,'version':meta['versions'][-1]['id'],'revision':meta['revision'],'created':now(),'kind':'normalized' if html is not None else 'native'}
                    atomic_json(directory/'meta.json',meta)
                    return self.send(public_meta(directory,meta))
                if action == "save":
                    html = data["html"]
                    if not isinstance(html, str) or len(html.encode("utf-8")) > 10 * 1024 * 1024:
                        raise ValueError("HTML 内容过大")
                    entry = data.get('entry') or meta['entry']
                    target = safe_child(directory / 'document', entry)
                    if not entry.lower().endswith(('.html', '.htm')) or not target.is_file():
                        raise ValueError('只能保存稿件目录内已有的 HTML 页面')
                    atomic_write(target, html.encode("utf-8"))
                    meta = snapshot(directory, data.get("label", "轻编辑保存"), data.get("changes"))
                elif action == "restore":
                    version = next((v for v in meta["versions"] if v["id"] == data.get("version")), None)
                    if not version:
                        raise ValueError("未找到此版本")
                    with zipfile.ZipFile(directory / "versions" / (version["id"] + ".zip")) as archive:
                        # Validate and stage before replacing the live document; keep the replaced tree recoverable.
                        staged = Path(tempfile.mkdtemp(prefix="restore-", dir=directory))
                        total = 0
                        for item in archive.infolist():
                            target = safe_child(staged, item.filename)
                            total += item.file_size
                            if total > MAX_BYTES:
                                raise ValueError("版本文件过大")
                            if not item.is_dir():
                                target.parent.mkdir(parents=True, exist_ok=True)
                                with archive.open(item) as source, target.open('wb') as dest:
                                    shutil.copyfileobj(source, dest, 64 * 1024)
                    old = directory / ("replaced-" + secrets.token_hex(6))
                    (directory / "document").rename(old)
                    try:
                        staged.rename(directory / "document")
                    except OSError:
                        old.rename(directory / "document")
                        raise
                    meta = snapshot(directory, "恢复至 " + version["created"])
                elif action == "export-edit":
                    mode = data.get("mode")
                    if mode not in ("trace", "full"):
                        raise ValueError("请选择仅编辑痕迹或完整编辑包")
                    context = {"intent": str(data.get("intent", ""))[:10000], "scope": data.get("scope", "current"), "selection": data.get("selection")}
                    files = {p.relative_to(directory / "document").as_posix(): p for p in tree_files(directory / "document")}
                    artifact, markdown = build_artifacts(meta, directory, files, context, now())
                    atomic_json(directory / "export-context.json", context)
                    atomic_write(directory / "exports" / "jemu-edits.md", markdown.encode("utf-8"))
                    atomic_json(directory / "exports" / "changes.json", artifact)
                    if mode == "trace":
                        if data.get('prepare'):
                            return self.send({'downloadUrl': f'/api/decks/{meta["id"]}/download-trace'})
                        return self.send(markdown.encode("utf-8"), content_type="text/markdown; charset=utf-8", disposition='attachment; filename="jemu-edits.md"')
                    target = directory / 'exports' / 'jemu-full.zip'
                    temporary = target.with_suffix('.zip.tmp')
                    try:
                        full_bundle(artifact, markdown, files, temporary)
                        os.replace(temporary, target)
                    finally:
                        temporary.unlink(missing_ok=True)
                    if data.get('prepare'):
                        return self.send({'downloadUrl': f'/api/decks/{meta["id"]}/download-full'})
                    return self.send_file(target, 'application/zip', 'attachment; filename="jemu-full.zip"')
                elif action == "ai-request":
                    request = {"created": now(), "entryPath": str(directory / "document" / meta["entry"]), "revision": meta["revision"],
                               "selection": data.get("selection"), "changes": data.get("changes", []), "scope": data.get("scope", "current"), "intent": data.get("intent", "")}
                    atomic_json(directory / "ai-request.json", request)
                    scope = {"current": "仅当前元素／页面", "similar": "查找其他页中语义或结构相近的元素，提出修复方案", "all": "检查全稿的一致性，提出修复方案"}.get(request["scope"], "仅当前页")
                    prompt = (f"请继续编辑这个本地 HTML 演示稿：\n{request['entryPath']}\n\n"
                              f"用户意图：{request['intent'] or '根据以下编辑示例理解我的排版意图。'}\n范围：{scope}\n\n"
                              "编辑上下文：\n```json\n" + json.dumps(request, ensure_ascii=False, indent=2) + "\n```\n\n"
                              "请先检查当前文件和布局。根据页面角色、内容层级理解修改意图，不要把坐标直接复制到所有页面。"
                              "相近页面的改动先列出页码和方案，等用户确认后再执行；仅当前页的明确修改可直接完成。"
                              "保留 data-slide-id / data-node-id、logo 比例、演讲稿、翻页与打印功能。文件保存后 Jemu（简幕）会检测更新。\n")
                    atomic_write(directory / "ai-request.md", prompt.encode("utf-8"))
                    return self.send({"prompt": prompt, "path": str(directory / "ai-request.md")})
                elif action == "open-folder":
                    if os.name == "nt":
                        os.startfile(str(directory / "document"))
                    return self.send({"ok": True})
                else:
                    return self.send({"error": "未找到操作"}, 404)
                self.send(public_meta(directory, meta))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except FileNotFoundError:
            self.send({"error": "未找到文件"}, 404)
        except (KeyError, ValueError, OSError, zipfile.BadZipFile) as error:
            self.send({"error": str(error)}, 400)

    def write_document_file(self,deck_id,length):
        if length>MAX_BYTES:raise ValueError('文件超过 100 MiB')
        directory=deck_dir(deck_id);meta=current_meta(directory)
        query=parse_qs(urlparse(self.path).query)
        if query.get('baseRevision',[''])[0]!=meta['revision']:
            return self.send({'error':'文件已更新，请读取新版本后再修改','meta':public_meta(directory,meta)},409)
        name=query.get('path',[''])[0];target=safe_child(directory/'document',name)
        total=sum(p.stat().st_size for p in tree_files(directory/'document'))
        if total-(target.stat().st_size if target.is_file() else 0)+length>MAX_BYTES:raise ValueError('稿件超过 100 MiB')
        # Stage outside document so polling and snapshots never see partial contents.
        with tempfile.TemporaryDirectory(prefix='cli-write-',dir=directory) as temp:
            staged=Path(temp)/'payload';remaining=length
            with staged.open('wb') as dest:
                while remaining:
                    chunk=self.rfile.read(min(65536,remaining))
                    if not chunk:raise ValueError('文件上传未完成')
                    dest.write(chunk);remaining-=len(chunk)
            if revision(directory)!=meta['revision']:return self.send({'error':'写入期间文件被外部更新，请重试'},409)
            target.parent.mkdir(parents=True,exist_ok=True)
            os.replace(staged,target)
        return self.send(public_meta(directory,snapshot(directory,'CLI 写入文件：'+name)))

    def import_directory(self, upload):
        if not isinstance(upload, str) or len(upload) != 24 or any(c not in '0123456789abcdef' for c in upload):
            raise ValueError('导入会话无效')
        staged = DATA / ('.import-' + upload)
        if staged.is_symlink() or staged.resolve().parent != DATA.resolve() or not (staged / 'upload.json').is_file():
            raise ValueError('导入会话已失效，请重试')
        return staged

    def import_file(self, length):
        query = parse_qs(urlparse(self.path).query)
        staged = self.import_directory(query.get('upload', [''])[0])
        upload = json.loads((staged / 'upload.json').read_text(encoding='utf8'))
        target = safe_child(staged / 'document', query.get('path', [''])[0])
        if target.exists() or upload['size'] + length > MAX_BYTES:
            raise ValueError('文件重复或稿件超过 100 MB')
        target.parent.mkdir(parents=True, exist_ok=True)
        remaining = length
        with target.open('wb') as dest:
            while remaining:
                chunk = self.rfile.read(min(64 * 1024, remaining))
                if not chunk:
                    raise ValueError('文件上传未完成')
                dest.write(chunk)
                remaining -= len(chunk)
        upload['size'] += length
        atomic_json(staged / 'upload.json', upload)
        return self.send({'ok': True})


def open_window(url):
    runtime = ROOT / ".runtime"
    edge = next((Path(p) for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if Path(p).is_file()), None)
    if edge:
        key=native_window.allocate(edge)
        url+=('&' if '?' in url else '?')+urlencode({'window':key})
        subprocess.Popen([str(edge), "--app=" + url, "--user-data-dir=" + str(runtime / "browser"), "--no-first-run", "--window-size=1440,940"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def main():
    global DATA, SERVER, TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "library")
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--no-seed", action="store_true")
    args = parser.parse_args()
    DATA = args.data_dir.resolve()
    DATA.mkdir(parents=True, exist_ok=True)
    if DATA == (ROOT / "library").resolve():
        secret = ROOT / ".runtime" / "session-secret"
        if secret.is_file():
            TOKEN = secret.read_text(encoding="utf-8").strip()
        else:
            atomic_write(secret, TOKEN.encode("utf-8"))
    if not args.no_seed:
        seed_template()
    SERVER = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    SERVER.daemon_threads = True
    url = f"http://127.0.0.1:{SERVER.server_port}"
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    if DATA == (ROOT / "library").resolve():
        atomic_json(runtime / "session.json", {"url": url, "pid": os.getpid(), "library": str(DATA)})
    print(url, flush=True)
    if args.desktop:
        open_window(url)
    try:
        SERVER.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        native_window.restore_all()
        render_exports.shutdown()
        SERVER.server_close()


if __name__ == "__main__":
    main()
