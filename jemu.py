"""Jemu CLI: the same local API as the GUI, Python standard library only."""
import argparse
import getpass
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode, urlparse

ROOT=Path(__file__).resolve().parent
MAX_JSON=16*1024*1024


class CLIError(Exception):
    def __init__(self,message,code='operation_failed',exit_code=5,details=None):
        super().__init__(message);self.code=code;self.exit_code=exit_code;self.details=details


class Parser(argparse.ArgumentParser):
    def error(self,message):raise CLIError(message,'usage',2)


class Client:
    def __init__(self,url=None):
        if not url:
            try:url=json.loads((ROOT/'.runtime/session.json').read_text(encoding='utf8'))['url']
            except (OSError,ValueError,KeyError):raise CLIError('服务尚未启动，请先 jemu start','not_running',3)
        parsed=urlparse(url)
        if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost') or not parsed.port or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            raise CLIError('仅允许本机 HTTP 服务地址','invalid_url',2)
        self.url=url.rstrip('/');self.host=parsed.hostname;self.port=parsed.port;self.token=None
        health=self.json('GET','/api/health')
        if health.get('app')!='deckroom':raise CLIError('此地址不是简幕服务','wrong_service',3)

    def auth(self):
        if not self.token:
            raw=self.request('GET','/',json_response=False)
            match=re.search(rb"window.DECKROOM_TOKEN='([^']+)'",raw)
            if not match:raise CLIError('无法获取本机会话','session_error',3)
            self.token=match.group(1).decode('ascii')
        return self.token

    def request(self,method,path,body=None,length=None,json_response=True,out=None,overwrite=False,content_type='application/octet-stream'):
        if not path.startswith('/') or path.startswith('//') or any(c in path for c in '\r\n#'):
            raise CLIError('接口路径无效','usage',2)
        headers={}
        if method!='GET':headers={'X-Deckroom-Token':self.auth(),'Content-Type':content_type}
        if length is not None:headers['Content-Length']=str(length)
        connection=http.client.HTTPConnection(self.host,self.port,timeout=120,blocksize=65536)
        try:
            connection.request(method,path,body=body,headers=headers)
            response=connection.getresponse()
            if not 200<=response.status<300:
                raw=response.read(MAX_JSON+1)
                try:detail=json.loads(raw);message=detail.get('error','请求失败')
                except (ValueError,UnicodeError):detail=None;message='本地接口请求失败'
                raise CLIError(message,'conflict' if response.status==409 else 'http_error',4 if response.status==409 else 5,{'status':response.status,'response':detail})
            if out is not None:
                target=Path(out).expanduser().resolve();target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists() and not overwrite:raise CLIError('目标文件已存在；另选路径或添加 --overwrite','output_exists',2)
                size=0;temporary=None
                try:
                    with tempfile.NamedTemporaryFile(prefix='.jemu-download-',dir=target.parent,delete=False) as stream:
                        temporary=Path(stream.name)
                        while True:
                            chunk=response.read(65536)
                            if not chunk:break
                            stream.write(chunk);size+=len(chunk)
                    expected=response.getheader('Content-Length')
                    if expected and size!=int(expected):raise CLIError('文件下载未完成','incomplete_download')
                    if overwrite:os.replace(temporary,target)
                    else:
                        # Atomic no-clobber publication, even if another process creates the target.
                        try:
                            if os.name=='nt':os.rename(temporary,target)
                            else:os.link(temporary,target)
                        except FileExistsError:raise CLIError('目标文件已由其他进程创建','output_exists',2)
                    return {'path':str(target),'bytes':size}
                finally:
                    if temporary:temporary.unlink(missing_ok=True)
            raw=response.read(MAX_JSON+1)
            if len(raw)>MAX_JSON:raise CLIError('响应过大，请使用 --out 流式保存','response_limit')
            return json.loads(raw) if json_response else raw
        except (OSError,http.client.HTTPException) as error:
            raise CLIError('无法完成本地服务请求，请检查 jemu status','connection_error',3) from error
        finally:connection.close()

    def json(self,method,path,data=None):
        raw=None if data is None else json.dumps(data,ensure_ascii=False).encode('utf8')
        return self.request(method,path,raw,None if raw is None else len(raw),content_type='application/json')

    def meta(self,deck):return self.json('GET','/api/decks/'+deck)


def parser():
    p=Parser(prog='jemu',description='简幕 CLI：设置、文件、版本、存档、演示窗口；结果为 JSON。')
    p.add_argument('--url',help='指定本机服务地址；默认自动发现')
    p.add_argument('--json',action='store_true',help='紧凑 JSON 输出，便于 AI／脚本读取')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('status');sub.add_parser('capabilities')
    desktop=sub.add_parser('desktop');desktop.add_argument('--deck',help='在独立桌面窗口打开指定作品')
    start=sub.add_parser('start');start.add_argument('--port',type=int,default=0);start.add_argument('--desktop',action='store_true')
    stop=sub.add_parser('stop');stop.add_argument('--force',action='store_true',help='即使窗口有未保存编辑也停止服务')
    settings=sub.add_parser('settings').add_subparsers(dest='operation',required=True)
    settings.add_parser('get')
    backup=settings.add_parser('backup').add_subparsers(dest='action',required=True)
    for name in ('get','test','disconnect'):backup.add_parser(name)
    cfg=backup.add_parser('set');cfg.add_argument('--provider',required=True,choices=['nutstore','google-drive','onedrive','quark','dropbox','folder'])
    cfg.add_argument('--folder');cfg.add_argument('--username')
    secret=cfg.add_mutually_exclusive_group();secret.add_argument('--password-stdin',action='store_true');secret.add_argument('--prompt-password',action='store_true')
    decks=sub.add_parser('decks').add_subparsers(dest='operation',required=True)
    decks.add_parser('list')
    show=decks.add_parser('show');show.add_argument('deck')
    imp=decks.add_parser('import');imp.add_argument('source');imp.add_argument('--entry');imp.add_argument('--name')
    mode=decks.add_parser('mode');mode.add_argument('deck');mode.add_argument('mode',choices=['auto','native','slides'])
    prepare=decks.add_parser('prepare');prepare.add_argument('deck');prepare.add_argument('--base-revision',required=True);prepare.add_argument('--entry');prepare.add_argument('--source',help='可选：AI 已准备好的完整 UTF-8 HTML')
    files=sub.add_parser('files').add_subparsers(dest='operation',required=True)
    for name in ('list','read','write','delete'):
        c=files.add_parser(name);c.add_argument('deck')
        if name!='list':c.add_argument('path')
        if name=='read':c.add_argument('--out');c.add_argument('--overwrite',action='store_true')
        if name=='write':c.add_argument('--from',dest='source',required=True)
        if name in ('write','delete'):c.add_argument('--base-revision',required=True)
    versions=sub.add_parser('versions').add_subparsers(dest='operation',required=True)
    for name in ('list','restore'):
        c=versions.add_parser(name);c.add_argument('deck')
        if name=='restore':c.add_argument('version');c.add_argument('--base-revision',required=True)
    export=sub.add_parser('export');export.add_argument('deck');export.add_argument('--kind',choices=['source','trace','full','pdf','pptx'],default='source')
    export.add_argument('--out',required=True);export.add_argument('--overwrite',action='store_true');export.add_argument('--intent',default='');export.add_argument('--scope',choices=['current','similar','all'],default='current')
    backups=sub.add_parser('backup').add_subparsers(dest='operation',required=True)
    c=backups.add_parser('create');c.add_argument('deck')
    c=backups.add_parser('list');c.add_argument('--month');c.add_argument('--offset',type=int,default=0)
    c=backups.add_parser('restore');c.add_argument('archive')
    ui=sub.add_parser('ui').add_subparsers(dest='operation',required=True)
    ui.add_parser('status');c=ui.add_parser('result');c.add_argument('id')
    for name in ('open','next','previous','goto','reset','edit','select','text','format','move','delete','copy','paste','undo','redo','save','present','exit','presenter','timer','settings'):
        c=ui.add_parser(name);c.add_argument('--client');c.add_argument('--no-wait',action='store_true');c.add_argument('--timeout',type=float,default=15)
        c.add_argument('--expect-deck');c.add_argument('--expect-revision')
        if name=='open':c.add_argument('deck')
        if name=='goto':c.add_argument('page',type=int)
        if name=='edit':c.add_argument('enabled',choices=['on','off'])
        if name=='select':c.add_argument('selector')
        if name=='text':c.add_argument('--from',dest='source',required=True,help='UTF-8 文本文件，或 - 从标准输入读取')
        if name=='format':
            c.add_argument('--font');c.add_argument('--size',type=float);c.add_argument('--bold',choices=['on','off']);c.add_argument('--italic',choices=['on','off'])
        if name=='move':c.add_argument('x',type=float);c.add_argument('y',type=float)
        if name=='timer':c.add_argument('timer_operation',choices=['start','pause','reset'])
    raw=sub.add_parser('request');raw.add_argument('method',choices=['GET','POST']);raw.add_argument('path');raw.add_argument('--body',help='JSON 文件或 -（标准输入）')
    return p


def read_text(source,limit=MAX_JSON):
    if source=='-':value=sys.stdin.read(limit+1)
    else:
        path=Path(source)
        if path.stat().st_size>limit:raise CLIError('输入文件过大','input_limit',2)
        value=path.read_text(encoding='utf-8-sig')
    if len(value.encode('utf8'))>limit:raise CLIError('输入内容过大','input_limit',2)
    return value


def start_service(args):
    if args.url:raise CLIError('start 不接受 --url','usage',2)
    try:
        existing=Client();return {'running':True,'url':existing.url,'reused':True}
    except CLIError as error:
        if error.code not in ('not_running','connection_error'):raise
    runtime=ROOT/'.runtime';runtime.mkdir(exist_ok=True)
    command=[sys.executable,str(ROOT/'server.py'),'--port',str(args.port)]
    if args.desktop:command.append('--desktop')
    with (runtime/'server.log').open('ab') as stdout,(runtime/'error.log').open('ab') as stderr:
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,start_new_session=os.name!='nt')
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if process.poll() is not None:raise CLIError('服务启动失败，请查看 .runtime/error.log','start_failed',3)
        try:
            session=json.loads((runtime/'session.json').read_text(encoding='utf8'))
            if session.get('pid')==process.pid:
                client=Client(session['url']);return {'running':True,'url':client.url,'pid':process.pid,'reused':False}
        except (OSError,ValueError,KeyError,CLIError):pass
        time.sleep(.1)
    raise CLIError('启动等待超时，请先检查 jemu status','start_timeout',3)


def import_deck(client,args):
    source=Path(args.source).expanduser().resolve()
    if source.is_file():root=source.parent;paths=[source];entry=source.name
    elif source.is_dir():
        root=source
        paths=sorted(p for p in root.rglob('*') if p.is_file() and not any(part.startswith('.') or part=='__pycache__' for part in p.relative_to(root).parts))
        html=[p.relative_to(root).as_posix() for p in paths if p.suffix.lower() in ('.html','.htm')]
        entry=args.entry or ('index.html' if 'index.html' in html else html[0] if len(html)==1 else None)
        if not entry:raise CLIError('目录有多个或没有 HTML，请用 --entry 指定主文件','entry_required',2)
    else:raise CLIError('导入路径不存在','source_missing',2)
    if args.entry:entry=args.entry
    if not paths or len(paths)>10000 or sum(p.stat().st_size for p in paths)>100*1024*1024:raise CLIError('导入最多 100 MiB、10000 个文件','input_limit',2)
    if entry not in [p.relative_to(root).as_posix() for p in paths]:raise CLIError('主文件不在导入文件中','entry_missing',2)
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root):raise CLIError('不支持链接到目录外的素材','unsafe_path',2)
    session=client.json('POST','/api/import-start',{'name':args.name or source.stem,'entry':entry})['upload']
    try:
        for path in paths:
            with path.open('rb') as stream:client.request('POST','/api/import-file?'+urlencode({'upload':session,'path':path.relative_to(root).as_posix()}),stream,path.stat().st_size)
        return client.json('POST','/api/import-finish',{'upload':session})
    except BaseException:
        try:client.json('POST','/api/import-cancel',{'upload':session})
        except CLIError:pass
        raise


def ui_command(client,args):
    name=args.operation
    if name=='status':return client.json('GET','/api/ui/state')
    if name=='result':return client.json('GET','/api/ui/commands/'+args.id)
    values={}
    if args.expect_deck:values['deck']=args.expect_deck
    if args.expect_revision:values['revision']=args.expect_revision
    if name=='open':values['deck']=args.deck
    if name=='goto':values['page']=args.page
    if name=='edit':values['enabled']=args.enabled=='on'
    if name=='select':values['selector']=args.selector
    if name=='text':values['text']=read_text(args.source,50000)
    if name=='move':values.update(x=args.x,y=args.y)
    if name=='timer':values['operation']=args.timer_operation
    if name=='format':
        for key,attr in [('fontFamily','font'),('fontSize','size'),('bold','bold'),('italic','italic')]:
            value=getattr(args,attr)
            if value is not None:values[key]=(value=='on') if key in ('bold','italic') else value
        if not values:raise CLIError('请指定字体属性','usage',2)
    if not 0<args.timeout<=120:raise CLIError('--timeout 应在 0～120 秒内','usage',2)
    result=client.json('POST','/api/ui/commands',{'action':name,'args':values,'client':args.client})
    if args.no_wait:return result
    deadline=time.monotonic()+args.timeout
    while time.monotonic()<deadline:
        result=client.json('GET','/api/ui/commands/'+result['id'])
        if result['status']=='completed':return result
        if result['status']=='failed':raise CLIError(result['result']['message'],'ui_failed',5,result)
        time.sleep(.2)
    raise CLIError('界面命令等待超时，可能仍在执行；请先 ui result '+result['id']+' 检查，避免重复操作','ui_pending',5,result)


def execute(args):
    if args.command=='start':return start_service(args)
    client=Client(args.url);command=args.command
    if command=='status':return {'url':client.url,**client.json('GET','/api/health')}
    if command=='capabilities':return client.json('GET','/api/capabilities')
    if command=='stop':
        if not args.force and any(c.get('dirty') for c in client.json('GET','/api/ui/state')['clients']):raise CLIError('窗口有未保存编辑，请先 ui save；或明确指定 --force','unsaved_edits',4)
        return client.json('POST','/api/shutdown',{})
    if command=='settings':
        if args.operation=='get':return client.json('GET','/api/settings')
        action=args.action
        if action=='get':return client.json('GET','/api/settings/backup')
        if action in ('test','disconnect'):return client.json('POST','/api/cloud/'+action,{})
        data={'provider':args.provider}
        if args.provider=='nutstore':
            if not args.username:raise CLIError('坚果云需要 --username','usage',2)
            data['username']=args.username
            if args.password_stdin:data['password']=read_text('-',4096).rstrip('\r\n')
            elif args.prompt_password:data['password']=getpass.getpass('坚果云应用密码（不回显）：')
            else:data['password']=''
        else:
            if args.password_stdin or args.prompt_password or args.username:raise CLIError('文件夹适配不需要账号密码','usage',2)
            if not args.folder:raise CLIError('文件夹适配需要 --folder','usage',2)
            data['folder']=str(Path(args.folder).expanduser().resolve())
        return client.json('POST','/api/settings/backup',data)
    if command=='decks':
        if args.operation=='list':return client.json('GET','/api/decks')
        if args.operation=='import':return import_deck(client,args)
        if args.operation=='show':return client.meta(args.deck)
        if args.operation=='prepare':
            data={'baseRevision':args.base_revision,'entry':args.entry}
            if args.source:
                source=Path(args.source)
                if source.stat().st_size>10*1024*1024:raise CLIError('HTML 超过 10 MiB','usage',2)
                data['html']=source.read_text(encoding='utf-8-sig')
            return client.json('POST','/api/decks/'+args.deck+'/prepare',data)
        return client.json('POST','/api/decks/'+args.deck+'/view-mode',{'mode':args.mode})
    if command=='desktop':return client.json('POST','/api/open-desktop',{'deck':args.deck})
    if command=='files':
        prefix='/api/decks/'+args.deck
        if args.operation=='list':return client.json('GET',prefix+'/files')
        path=prefix+'/file?'+urlencode({'path':args.path})
        if args.operation=='read':
            if args.out:return client.request('GET',path,out=args.out,overwrite=args.overwrite)
            raw=client.request('GET',path,json_response=False)
            try:text=raw.decode('utf-8-sig')
            except UnicodeError:raise CLIError('此文件不是 UTF-8 文本，请用 --out 保存原始文件','binary_output',2)
            return {'path':args.path,'text':text}
        if args.operation=='delete':return client.json('POST',prefix+'/delete-file',{'path':args.path,'baseRevision':args.base_revision})
        source=Path(args.source)
        with source.open('rb') as stream:return client.request('POST',path+'&'+urlencode({'baseRevision':args.base_revision}),stream,source.stat().st_size)
    if command=='versions':
        if args.operation=='list':
            meta=client.meta(args.deck);return {'revision':meta['revision'],'versions':meta['versions']}
        return client.json('POST','/api/decks/'+args.deck+'/restore',{'version':args.version,'baseRevision':args.base_revision})
    if command=='export':
        target=Path(args.out).expanduser()
        if target.exists() and not args.overwrite:raise CLIError('输出文件已存在；另选路径或添加 --overwrite','output_exists',2)
        prefix='/api/decks/'+args.deck
        if args.kind in ('pdf','pptx'):
            meta=client.meta(args.deck);job=client.json('POST',prefix+'/render-start',{'kind':args.kind,'baseRevision':meta['revision']})
            deadline=time.monotonic()+120
            while job['status'] in ('queued','rendering'):
                if time.monotonic()>deadline:raise CLIError('导出仍在运行，请查询任务状态','export_pending',5,job)
                time.sleep(.5);job=client.json('GET',prefix+'/render-jobs/'+job['id'])
            if job['status']!='completed':raise CLIError(job.get('message','导出失败'),'render_failed',5,job)
            return {**client.request('GET',job['downloadUrl'],out=args.out,overwrite=args.overwrite),'pages':job.get('pages'),'kind':args.kind}
        elif args.kind=='source':path=prefix+'/export'
        else:
            meta=client.meta(args.deck)
            prepared=client.json('POST',prefix+'/export-edit',{'mode':args.kind,'prepare':True,'baseRevision':meta['revision'],'intent':args.intent,'scope':args.scope})
            path=prepared['downloadUrl']
        return client.request('GET',path,out=args.out,overwrite=args.overwrite)
    if command=='backup':
        if args.operation=='list':return client.json('GET','/api/cloud/archives?'+urlencode({k:v for k,v in {'month':args.month,'offset':args.offset}.items() if v is not None}))
        if args.operation=='restore':return client.json('POST','/api/cloud/restore',{'archive':args.archive})
        meta=client.meta(args.deck);return client.json('POST','/api/decks/'+args.deck+'/cloud-backup',{'baseRevision':meta['revision']})
    if command=='ui':return ui_command(client,args)
    if command=='request':
        if not args.path.startswith('/api/'):raise CLIError('request 只允许 /api/ 路径','usage',2)
        data=json.loads(read_text(args.body)) if args.body else None
        if args.method=='GET' and data is not None:raise CLIError('GET 不接受 --body','usage',2)
        return client.json(args.method,args.path,data if args.method=='GET' else data or {})
    raise CLIError('未知命令','usage',2)


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    compact='--json' in argv
    # Allow --json before or after a subcommand without changing positional values.
    if compact:argv.remove('--json')
    try:
        args=parser().parse_args(argv);result=execute(args);output={'ok':True,'data':result};code=0
    except CLIError as error:
        output={'ok':False,'error':{'code':error.code,'message':str(error)}}
        if error.details is not None:output['error']['details']=error.details
        code=error.exit_code
    except (OSError,ValueError,KeyError) as error:
        output={'ok':False,'error':{'code':'invalid_input','message':'输入文件、编码或参数无效，请检查命令和输入文件'}};code=2
    except KeyboardInterrupt:
        output={'ok':False,'error':{'code':'interrupted','message':'操作已中断；请检查状态后再重试'}};code=130
    print(json.dumps(output,ensure_ascii=False,indent=None if compact else 2));return code


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
    raise SystemExit(main())
