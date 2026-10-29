"""Nutstore WebDAV adapter: explicit requests, streaming bodies, immutable archives."""
import base64
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import re
import socket
import ssl
import tempfile
import threading
from urllib.parse import quote, unquote, urlparse
import xml.etree.ElementTree as ET

from cloud_archives import CHUNK, MAX_ARCHIVE_BYTES, MAX_MANIFEST

NUTSTORE_URL = 'https://dav.jianguoyun.com/dav/'
UPLOAD_LIMIT = 450 * 1024 * 1024
_INDEX_CACHE = {}
# Requests to the cloud run outside the server's global lock.
_INDEX_LOCK = threading.Lock()


class WebDAVError(ValueError):
    pass


def archive_parts(archive_id):
    if not isinstance(archive_id, str) or not re.fullmatch(r'\d{8}T\d{6}Z-[a-f0-9]{12}\.jemu\.zip', archive_id):
        raise ValueError('存档标识无效')
    return archive_id[:4] + '-' + archive_id[4:6], archive_id[6:8]


class NutstoreArchiveStore:
    provider = 'nutstore'

    def __init__(self, username, password, *, _test_url=None):
        if not isinstance(username, str) or not username or len(username)>320 or any(c in username for c in ':\r\n'):
            raise ValueError('请填写有效的坚果云账号邮箱')
        if not isinstance(password, str) or not password or len(password)>4096:
            raise ValueError('请填写坚果云应用密码')
        url = urlparse(_test_url or NUTSTORE_URL)
        if _test_url and not (url.scheme == 'http' and url.hostname == '127.0.0.1'):
            raise ValueError('模拟服务器仅允许本机回环地址')
        self.url = url
        self.base = url.path.rstrip('/')
        self.username = username
        self.authorization = 'Basic ' + base64.b64encode((username + ':' + password).encode('utf8')).decode('ascii')

    @contextmanager
    def response(self, method, relative='', *, body=None, length=None, headers=None, allowed=(200,)):
        connection = None
        try:
            factory = http.client.HTTPSConnection if self.url.scheme=='https' else http.client.HTTPConnection
            connection = factory(self.url.hostname, self.url.port, timeout=25, blocksize=CHUNK)
            path = self.base + '/' + quote(relative, safe='/')
            request_headers = {'Authorization': self.authorization, 'User-Agent': 'Jemu/0.1 WebDAV', **(headers or {})}
            if length is not None:request_headers['Content-Length'] = str(length)
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            if response.status not in allowed:
                messages = {401:'账号或应用密码不正确，请使用坚果云第三方应用密码',403:'服务器拒绝访问，请检查应用授权和账号权限',404:'云端存档尚不存在或未上传完整',409:'云端目录尚未创建，请重试备份',412:'此存档已存在，未覆盖原文件',413:'云端拒绝大文件，请减少视频或历史后再备份',429:'云端请求过于频繁，请稍后重试',507:'云端空间或流量不足，请检查坚果云账户'}
                raise WebDAVError(messages.get(response.status, 'WebDAV 操作未完成（HTTP '+str(response.status)+'）'))
            yield response
        except (socket.timeout, TimeoutError):
            raise WebDAVError('连接超时；本地稿件仍保留，可稍后重试') from None
        except ssl.SSLError:
            raise WebDAVError('无法验证云端 HTTPS 连接，请检查网络和系统证书') from None
        except (OSError, http.client.HTTPException):
            raise WebDAVError('云端连接中断；本地稿件仍保留，请检查网络后重试') from None
        finally:
            if connection:connection.close()

    def propfind(self, relative='', depth=1, missing_ok=False):
        body = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getcontentlength/></d:prop></d:propfind>'
        allowed = (207,404) if missing_ok else (207,)
        directory=relative.rstrip('/')+'/' if relative else ''
        with self.response('PROPFIND', directory, body=body, length=len(body), headers={'Depth':str(depth),'Content-Type':'application/xml; charset=utf-8'}, allowed=allowed) as response:
            if response.status==404:return []
            raw = response.read(2*1024*1024+1)
        if len(raw)>2*1024*1024 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise WebDAVError('云端目录响应过大或格式不支持')
        try:root = ET.fromstring(raw)
        except ET.ParseError:raise WebDAVError('云端返回了无法识别的 WebDAV 目录') from None
        responses=root.findall('{DAV:}response')
        if not responses:raise WebDAVError('服务器没有返回可读取的 WebDAV 目录')
        if depth==0 and not any(p.find('.//{DAV:}collection') is not None and re.search(r'\s2\d\d\s',(p.findtext('{DAV:}status') or '')+' ') for item in responses for p in item.findall('{DAV:}propstat')):
            raise WebDAVError('服务器未授权读取此目录')
        if len(responses)>=750:raise WebDAVError('该日期的存档达到服务器目录上限，请分开整理后重试；未显示不完整列表')
        parent = (self.base + '/' + relative).rstrip('/')
        items=[]
        for item in responses:
            statuses = item.findall('{DAV:}propstat')
            valid = [p for p in statuses if re.search(r'\s2\d\d\s', (p.findtext('{DAV:}status') or '')+' ')]
            if not valid:continue
            href = urlparse(item.findtext('{DAV:}href') or '')
            if href.netloc and href.netloc!=self.url.netloc:continue
            path=unquote(href.path).rstrip('/')
            if path==parent or not path.startswith(parent+'/'):continue
            name=path[len(parent)+1:]
            if not name or '/' in name or '\\' in name:continue
            is_dir=any(p.find('.//{DAV:}collection') is not None for p in valid)
            items.append({'name':name,'directory':is_dir})
        return items

    def test(self):
        self.propfind('', depth=0)
        return {'ok':True,'message':'连接成功，可以读取账户。上传权限将在备份时验证。'}

    def ensure_directory(self, relative):
        with self.response('MKCOL', relative, length=0, allowed=(201,405)) as response:
            if response.status==405:
                self.propfind(relative, depth=0)

    def remote_path(self, archive_id):
        month,day=archive_parts(archive_id)
        return 'Jemu Archives/'+month+'/'+day+'/'+archive_id

    def put(self, source, archive_id, manifest):
        size=source.stat().st_size
        if size>UPLOAD_LIMIT:raise ValueError('此存档超过 450 MiB 的坚果云适配上限，请减少视频或历史后再备份')
        month,day=archive_parts(archive_id)
        for relative in ('Jemu Archives','Jemu Archives/'+month,'Jemu Archives/'+month+'/'+day):self.ensure_directory(relative)
        relative=self.remote_path(archive_id)
        with source.open('rb') as stream:
            with self.response('PUT',relative,body=stream,length=size,headers={'Content-Type':'application/zip','If-None-Match':'*'},allowed=(201,204)):pass
        # Publish the small immutable index last: incomplete ZIPs never enter the list.
        summary={'id':archive_id,'name':manifest['deck']['name'],'created':manifest['created'],'versionCount':manifest['versionCount'],'bytes':size}
        raw=json.dumps(summary,ensure_ascii=False,separators=(',',':')).encode('utf8')
        with self.response('PUT',relative[:-4]+'.json',body=raw,length=len(raw),headers={'Content-Type':'application/json; charset=utf-8','If-None-Match':'*'},allowed=(201,204)):pass
        return {'id':archive_id,'bytes':size,'versionCount':manifest['versionCount'],'status':'uploaded','remoteStatus':'server-accepted','month':month}

    def read_index(self, relative, archive_id):
        key=(self.url.netloc,self.username,relative)
        with _INDEX_LOCK:
            if key in _INDEX_CACHE:return _INDEX_CACHE[key]
        with self.response('GET',relative) as response:raw=response.read(16385)
        if len(raw)>16384:raise WebDAVError('云端存档索引过大')
        try:value=json.loads(raw)
        except (ValueError,UnicodeError):raise WebDAVError('云端存档索引无效') from None
        if not isinstance(value,dict) or value.get('id')!=archive_id or not all(isinstance(value.get(key),str) for key in ('name','created')) or not all(isinstance(value.get(key),int) for key in ('bytes','versionCount')):
            raise WebDAVError('云端存档索引格式无效')
        result={key:value[key] for key in ('id','name','created','bytes','versionCount')}
        with _INDEX_LOCK:
            while len(_INDEX_CACHE)>=100:del _INDEX_CACHE[next(iter(_INDEX_CACHE))]
            _INDEX_CACHE[key]=result
        return result

    def list(self, limit=50, offset=0, month=None):
        from datetime import datetime,timezone
        month=month or datetime.now(timezone.utc).strftime('%Y-%m')
        if not isinstance(month,str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):raise ValueError('请选择有效的存档月份')
        relative='Jemu Archives/'+month
        days=sorted((p['name'] for p in self.propfind(relative,missing_ok=True) if p['directory'] and re.fullmatch(r'(0[1-9]|[12][0-9]|3[01])',p['name'])),reverse=True)
        candidates=[]
        for day in days:
            names=[]
            for item in self.propfind(relative+'/'+day):
                if item['directory'] or not item['name'].endswith('.jemu.json'):continue
                archive_id=item['name'][:-5]+'.zip'
                try:m,d=archive_parts(archive_id)
                except ValueError:continue
                if (m,d)==(month,day):names.append(archive_id)
            candidates.extend(sorted(names,reverse=True))
            if len(candidates)>offset+limit:break
        items=[self.read_index(self.remote_path(archive_id)[:-4]+'.json',archive_id) for archive_id in candidates[offset:offset+limit]]
        # Older days may exist even when this page ends exactly at a day boundary.
        more=len(candidates)>offset+limit or (bool(days) and day!=days[-1])
        return {'items':items,'hasMore':more,'month':month}

    @contextmanager
    def materialize(self, archive_id):
        relative=self.remote_path(archive_id)
        with tempfile.TemporaryDirectory(prefix='jemu-webdav-') as temp:
            target=Path(temp)/archive_id
            with self.response('GET',relative) as response,target.open('wb') as dest:
                declared=response.getheader('Content-Length')
                if declared and int(declared)>MAX_ARCHIVE_BYTES+MAX_MANIFEST:raise WebDAVError('云端存档过大')
                size=0
                while True:
                    chunk=response.read(CHUNK)
                    if not chunk:break
                    size+=len(chunk)
                    if size>MAX_ARCHIVE_BYTES+MAX_MANIFEST:raise WebDAVError('云端存档过大')
                    dest.write(chunk)
                if declared and size!=int(declared):raise WebDAVError('云端下载未完成，请稍后重试')
            yield target
