"""On-demand static HTML -> PDF -> image-only PPTX. No resident render process."""
from __future__ import annotations
import importlib.util
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from xml.sax.saxutils import escape
import zipfile

ROOT=Path(__file__).resolve().parent
JOBS={}
JOB_LOCK=threading.RLock()
MAX_PAGES=200
MAX_HTML=10*1024*1024
MAX_OUTPUT=100*1024*1024

def browser_path():
    candidates=[Path(os.environ.get(key,''))/suffix for key,suffix in [('PROGRAMFILES(X86)','Microsoft/Edge/Application/msedge.exe'),('PROGRAMFILES','Microsoft/Edge/Application/msedge.exe'),('PROGRAMFILES','Google/Chrome/Application/chrome.exe')]]
    return next((str(path) for path in candidates if path.is_file()),shutil.which('chromium') or shutil.which('google-chrome') or shutil.which('msedge'))

def capabilities():
    browser=bool(browser_path());pdfium=importlib.util.find_spec('pypdfium2') is not None and importlib.util.find_spec('PIL') is not None
    return {'pdf':browser,'pptx':browser and pdfium,'imagePptx':True,'maxPages':MAX_PAGES,'maxConcurrentJobs':1,'rasterLongEdge':2560,'optionalDependency':'pypdfium2 + Pillow','residentRenderer':False}

def public(job):
    return {k:job[k] for k in ('id','deck','kind','status','message','created','pages','bytes','downloadUrl') if k in job}

def prune():
    finished=sorted((job for job in JOBS.values() if job['status'] in ('completed','failed')),key=lambda job:job['created'])
    for job in finished:
        if time.time()-job['created']>600 or len(JOBS)>4:
            # Paths are generated inside a private export directory, never original files.
            for path in job.get('outputs',[]):path.unlink(missing_ok=True)
            JOBS.pop(job['id'],None)

def status(deck,key):
    with JOB_LOCK:
        prune();job=JOBS.get(key)
        if not job or job['deck']!=deck:raise FileNotFoundError('导出任务已过期，请重新导出')
        return public(job)

def download(deck,key):
    with JOB_LOCK:
        status(deck,key);job=JOBS[key]
        if job['status']!='completed':raise ValueError('导出尚未完成')
        return job['target'],job['kind']

VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
class StaticDeck(HTMLParser):
    """Extract explicit stored page groups without executing any template code."""
    def __init__(self):
        super().__init__(convert_charrefs=False);self.stack=[];self.head=[];self.pages=[];self.current=None;self.depth=0;self.host_depth=None;self.host_start=None;self.host_tag=None;self.width=1920;self.height=1080;self.skip=0
    def handle_starttag(self,tag,attrs):
        values=dict(attrs);raw=self.get_starttag_text()
        if tag in ('script','iframe','object','embed'):
            if tag not in VOID:self.skip+=1
            return
        if self.skip:return
        if any(name.startswith('on') for name,value in attrs):raise ValueError('静态导出不接受事件处理脚本')
        if tag=='base':return
        if 'head' in self.stack or (self.current is None and tag in ('style','title','link','meta')):self.head.append(raw)
        if self.host_depth is None and ('data-jemu-deck' in values or tag=='deck-stage'):
            self.host_depth=len(self.stack);self.host_start=raw[:-1]+' data-jemu-export>';self.host_tag=tag
            self.width=float(values.get('data-width',values.get('width',1920)));self.height=float(values.get('data-height',values.get('height',1080)))
        elif self.host_depth is not None and len(self.stack)==self.host_depth+1 and (tag=='section' or 'data-slide-id' in values or 'slide' in values.get('class','').split()):
            self.current=[raw[:-1]+' data-jemu-export-page>'];self.depth=len(self.stack)
        elif self.current is not None:self.current.append(raw)
        if tag not in VOID:self.stack.append(tag)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID:self.handle_endtag(tag)
    def handle_endtag(self,tag):
        if self.skip:
            if tag in ('script','iframe','object'):self.skip-=1
            return
        if tag in VOID:return
        if ('head' in self.stack and tag!='head') or (self.current is None and tag in ('style','title')):self.head.append('</'+tag+'>')
        if self.current is not None:self.current.append('</'+tag+'>')
        if tag in self.stack:
            index=len(self.stack)-1-self.stack[::-1].index(tag);self.stack=self.stack[:index]
            if self.current is not None and index==self.depth:
                self.pages.append(''.join(self.current));self.current=None
                if len(self.pages)>MAX_PAGES:raise ValueError('导出超过 200 页')
    def handle_data(self,value):
        if self.skip:return
        if self.current is None and any(tag in self.stack for tag in ('head','style','title')):self.head.append(value)
        if self.current is not None:self.current.append(value)
    def handle_entityref(self,value):self.handle_data('&'+value+';')
    def handle_charref(self,value):self.handle_data('&#'+value+';')

class StaticAudit(HTMLParser):
    def __init__(self):super().__init__();self.pages=0
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag in ('script','iframe','object','embed','base'):raise ValueError('导出只接受无脚本和临时基址的静态页组')
        if any(name.startswith('on') or (name in ('href','src','action') and str(value).lstrip().lower().startswith('javascript:')) for name,value in attrs):raise ValueError('导出 HTML 含动态事件或脚本地址')
        if tag=='meta' and values.get('http-equiv','').lower()=='refresh':raise ValueError('导出 HTML 不能重定向')
        if 'data-jemu-export-page' in values:self.pages+=1
    def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs)

def stored_layout(directory,meta,entry):
    path=directory/'document'/entry
    if path.stat().st_size>MAX_HTML:raise ValueError('HTML 超过 10 MiB')
    parser=StaticDeck();parser.feed(path.read_text(encoding='utf-8-sig'));parser.close()
    if not parser.pages:raise ValueError('保存的作品没有明确的幻灯片页组，请先在窗口完成适配')
    w,h=parser.width,parser.height
    css=f'html,body{{margin:0!important;padding:0!important;width:{w}px!important;height:auto!important;overflow:visible!important;background:white!important}}[data-jemu-export]{{display:block!important;position:static!important;width:{w}px!important;height:auto!important;margin:0!important;padding:0!important;gap:0!important}}[data-jemu-export-page]{{display:block!important;position:relative!important;inset:auto!important;visibility:visible!important;width:{w}px!important;height:{h}px!important;min-height:{h}px!important;max-height:{h}px!important;box-sizing:border-box!important;overflow:hidden!important;margin:0!important;break-after:page;break-inside:avoid;transform:none!important;zoom:1!important}}[data-jemu-export-page]:last-child{{break-after:auto}}[data-jemu-export-page]>.slide-canvas{{transform:none!important;box-shadow:none!important}}*{{animation:none!important;transition:none!important;-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}}@page{{size:{w}px {h}px;margin:0}}'
    html='<!doctype html><html><head>'+''.join(parser.head)+'<meta charset="utf-8"><style>'+css+'</style></head><body>'+parser.host_start+''.join(parser.pages)+'</'+parser.host_tag+'></body></html>'
    return {'html':html,'pages':len(parser.pages),'width':w,'height':h}

def start(directory,meta,data,files,safe_child,revision_check=None):
    kind=data.get('kind','pdf');caps=capabilities()
    if kind not in ('pdf','pptx'):raise ValueError('导出格式无效')
    if not caps[kind]:raise ValueError('PPT 导出需要可选组件 pypdfium2 和 Pillow' if kind=='pptx' else '未找到系统 Edge／Chromium，暂时不能导出 PDF')
    entry=data.get('entry') or meta['entry']
    source=safe_child(directory/'document',entry)
    if source.suffix.lower() not in ('.html','.htm') or not source.is_file():raise ValueError('导出入口必须是作品内的 HTML')
    if data.get('html') is None:data={**data,**stored_layout(directory,meta,entry)}
    html=data.get('html');pages=data.get('pages');width=data.get('width');height=data.get('height')
    if not isinstance(html,str) or len(html.encode('utf8'))>MAX_HTML:raise ValueError('请提供不超过 10 MiB 的完整静态导出 HTML')
    if type(pages) is not int or not 1<=pages<=MAX_PAGES:raise ValueError('导出支持 1～200 页')
    audit=StaticAudit();audit.feed(html);audit.close()
    if audit.pages!=pages:raise ValueError('静态导出页组与声明页数不一致')
    if any(type(value) not in (int,float) or not 100<=value<=10000 for value in (width,height)):raise ValueError('导出画布尺寸无效')
    if not 0.1<=width/height<=10:raise ValueError('导出画布比例超出范围')
    with JOB_LOCK:
        prune()
        if any(job['status'] in ('queued','rendering') for job in JOBS.values()):raise ValueError('已有导出任务，请等待完成后再导出')
        key=secrets.token_hex(12);target=directory/'exports'/'rendered'/(key+'.'+kind);target.parent.mkdir(parents=True,exist_ok=True)
        job={'id':key,'deck':meta['id'],'kind':kind,'status':'queued','message':'准备独立导出稿件','created':time.time(),'target':target,'outputs':[target]}
        # Stream a frozen copy to disk. The worker never reads the changing live deck.
        temp=tempfile.TemporaryDirectory(prefix='jemu-render-');work=Path(temp.name);content=work/'document'
        try:
            total=0
            for path in files:
                total+=path.stat().st_size
                if total>100*1024*1024:raise ValueError('稿件超过导出素材上限')
                dest=content/path.relative_to(directory/'document');dest.parent.mkdir(parents=True,exist_ok=True)
                with path.open('rb') as src,dest.open('wb') as dst:shutil.copyfileobj(src,dst,65536)
            page=content/entry;page.write_text(html,encoding='utf8')
            if revision_check and revision_check()!=meta['revision']:raise ValueError('准备导出时原稿有外部修改，请重新载入')
            manifest={'entry':str(page),'browser':browser_path(),'profile':str(work/'browser'),'pdf':str(work/'deck.pdf'),'target':str(work/('result.'+kind)),'kind':kind,'pages':pages,'result':str(work/'result.json')}
            (work/'job.json').write_text(json.dumps(manifest),encoding='utf8')
        except Exception:temp.cleanup();raise
        JOBS[key]=job;prune()
        def execute():
            try:
                with JOB_LOCK:job.update(status='rendering',message='正在排出整稿；PPT 会逐页转换图片')
                flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
                process=subprocess.Popen([sys.executable,str(ROOT/'render_exports.py'),'--worker',str(work/'job.json')],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
                with JOB_LOCK:job['process']=process
                try:process.wait(timeout=110)
                finally:stop_tree(process)
                result=json.loads((work/'result.json').read_text(encoding='utf8'))
                if not result.get('ok'):raise ValueError(result.get('error','导出失败'))
                output=Path(manifest['target']);size=output.stat().st_size
                if size>MAX_OUTPUT:raise ValueError('导出文件超过 100 MiB')
                staged=target.with_suffix(target.suffix+'.tmp')
                with output.open('rb') as src,staged.open('wb') as dst:shutil.copyfileobj(src,dst,65536)
                os.replace(staged,target)
                with JOB_LOCK:job.update(status='completed',message='导出完成',pages=result['pages'],bytes=size,downloadUrl=f"/api/decks/{meta['id']}/render-jobs/{key}/download")
            except Exception as error:
                with JOB_LOCK:job.update(status='failed',message=str(error) if isinstance(error,ValueError) else '导出未完成或超时，请重试')
            finally:
                with JOB_LOCK:job.pop('process',None)
                temp.cleanup()
        thread=threading.Thread(target=execute,daemon=True,name='jemu-export');job['thread']=thread;thread.start()
        return public(job)

def shutdown():
    with JOB_LOCK:active=[job.copy() for job in JOBS.values() if job['status'] in ('queued','rendering')]
    for job in active:
        if job.get('process'):stop_tree(job['process'])
    for job in active:
        if job.get('thread'):job['thread'].join(timeout=10)

def process_job(process):
    """Windows closes this handle to release every child of the render browser."""
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',wintypes.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('active',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('schedule',wintypes.DWORD)]
    class IO(ctypes.Structure):_fields_=[(name,ctypes.c_ulonglong) for name in ('read','write','other','read_bytes','write_bytes','other_bytes')]
    class Extended(ctypes.Structure):_fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateJobObjectW(None,None);limits=Extended();limits.basic.flags=0x2000
    if not handle:return None
    if not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle,wintypes.HANDLE(int(process._handle))):kernel.CloseHandle(handle);return None
    return lambda:kernel.CloseHandle(handle)

def stop_tree(process):
    if process.poll() is not None:return
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
    else:process.kill()
    process.wait(timeout=10)

def make_pdf(manifest):
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    args=[manifest['browser'],'--headless=new','--disable-gpu','--disable-extensions','--disable-background-networking','--no-first-run','--no-default-browser-check','--no-pdf-header-footer','--virtual-time-budget=5000','--timeout=15000','--user-data-dir='+manifest['profile'],'--print-to-pdf='+manifest['pdf'],Path(manifest['entry']).as_uri()]
    process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
    close_job=process_job(process)
    try:process.wait(timeout=55)
    finally:
        stop_tree(process)
        if close_job:close_job()
    pdf=Path(manifest['pdf'])
    if not pdf.is_file() or not pdf.stat().st_size:raise ValueError('系统浏览器没有生成 PDF，请检查素材后重试')
    if pdf.stat().st_size>MAX_OUTPUT:raise ValueError('PDF 超过 100 MiB')

P='http://schemas.openxmlformats.org/presentationml/2006/main'
A='http://schemas.openxmlformats.org/drawingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL='http://schemas.openxmlformats.org/package/2006/relationships'
def relationships(items):
    return '<Relationships xmlns="'+REL+'">'+''.join(f'<Relationship Id="{rid}" Type="{R}/{kind}" Target="{target}"/>' for rid,kind,target in items)+'</Relationships>'
def tree():
    return '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
def envelope(tag,body):return f'<p:{tag} xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">{body}</p:{tag}>'

def pptx_from_pdf(pdf_path,target,expected):
    import pypdfium2 as pdfium
    pdf=pdfium.PdfDocument(pdf_path)
    try:
        count=len(pdf)
        if count!=expected:raise ValueError(f'排版页数不符：预期 {expected} 页，实际 {count} 页，已停止导出')
        first=pdf[0];width,height=first.get_size();first.close()
        long_emu=12192000;cx=round(long_emu*width/max(width,height));cy=round(long_emu*height/max(width,height))
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
            def xml(name,value):z.writestr(name,'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'+value)
            overrides=[('/ppt/presentation.xml','presentationml.presentation.main'),('/ppt/slideMasters/slideMaster1.xml','presentationml.slideMaster'),('/ppt/slideLayouts/slideLayout1.xml','presentationml.slideLayout'),('/ppt/theme/theme1.xml','theme')]+[(f'/ppt/slides/slide{i}.xml','presentationml.slide') for i in range(1,count+1)]
            xml('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'+''.join(f'<Override PartName="{name}" ContentType="application/vnd.openxmlformats-officedocument.{kind}+xml"/>' for name,kind in overrides)+'</Types>')
            xml('_rels/.rels',relationships([('rId1','officeDocument','ppt/presentation.xml')]))
            xml('ppt/presentation.xml',envelope('presentation','<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>'+''.join(f'<p:sldId id="{255+i}" r:id="rId{i+1}"/>' for i in range(1,count+1))+f'</p:sldIdLst><p:sldSz cx="{cx}" cy="{cy}"/><p:notesSz cx="6858000" cy="9144000"/>'))
            xml('ppt/_rels/presentation.xml.rels',relationships([('rId1','slideMaster','slideMasters/slideMaster1.xml')]+[(f'rId{i+1}','slide',f'slides/slide{i}.xml') for i in range(1,count+1)]))
            xml('ppt/slideMasters/slideMaster1.xml',envelope('sldMaster','<p:cSld><p:spTree>'+tree()+'</p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>'))
            xml('ppt/slideMasters/_rels/slideMaster1.xml.rels',relationships([('rId1','slideLayout','../slideLayouts/slideLayout1.xml'),('rId2','theme','../theme/theme1.xml')]))
            xml('ppt/slideLayouts/slideLayout1.xml',envelope('sldLayout','<p:cSld name="Blank"><p:spTree>'+tree()+'</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>').replace('<p:sldLayout ', '<p:sldLayout type="blank" preserve="1" '))
            xml('ppt/slideLayouts/_rels/slideLayout1.xml.rels',relationships([('rId1','slideMaster','../slideMasters/slideMaster1.xml')]))
            colors=''.join(f'<a:{name}><a:srgbClr val="{value}"/></a:{name}>' for name,value in [('dk1','000000'),('lt1','FFFFFF'),('dk2','222222'),('lt2','EEEEEE'),('accent1','284E40'),('accent2','660874'),('accent3','808080'),('accent4','4488AA'),('accent5','AA8844'),('accent6','775599'),('hlink','0000FF'),('folHlink','800080')])
            fill='<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            xml('ppt/theme/theme1.xml',f'<a:theme xmlns:a="{A}" name="Jemu"><a:themeElements><a:clrScheme name="Jemu">{colors}</a:clrScheme><a:fontScheme name="Jemu"><a:majorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme><a:fmtScheme name="Jemu"><a:fillStyleLst>{fill*3}</a:fillStyleLst><a:lnStyleLst>'+''.join(f'<a:ln w="{w}">{fill}<a:prstDash val="solid"/></a:ln>' for w in (6350,12700,19050))+f'</a:lnStyleLst><a:effectStyleLst>{"<a:effectStyle><a:effectLst/></a:effectStyle>"*3}</a:effectStyleLst><a:bgFillStyleLst>{fill*3}</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>')
            for index in range(count):
                page=pdf[index];bitmap=None;image=None
                try:
                    w,h=page.get_size()
                    if abs(w/h-width/height)>0.005:raise ValueError('PPT 暂不支持混合尺寸 PDF 页面')
                    scale=min(2560/max(w,h),(12_000_000/(w*h))**0.5)
                    bitmap=page.render(scale=scale);image=bitmap.to_pil()
                    with tempfile.NamedTemporaryFile(suffix='.png',delete=False) as stream:path=Path(stream.name)
                    try:image.save(path,format='PNG');z.write(path,f'ppt/media/image{index+1}.png')
                    finally:path.unlink(missing_ok=True)
                finally:
                    if image is not None:image.close()
                    if bitmap is not None:bitmap.close()
                    page.close()
                picture=f'<p:pic><p:nvPicPr><p:cNvPr id="2" name="Page {index+1}"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
                xml(f'ppt/slides/slide{index+1}.xml',envelope('sld','<p:cSld><p:spTree>'+tree()+picture+'</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'))
                xml(f'ppt/slides/_rels/slide{index+1}.xml.rels',relationships([('rId1','image',f'../media/image{index+1}.png'),('rId2','slideLayout','../slideLayouts/slideLayout1.xml')]))
                if Path(target).stat().st_size>MAX_OUTPUT:raise ValueError('PPT 超过 100 MiB')
        return count
    finally:pdf.close()

def worker(manifest_path):
    manifest=json.loads(Path(manifest_path).read_text(encoding='utf8'))
    try:
        make_pdf(manifest)
        if manifest['kind']=='pptx':count=pptx_from_pdf(manifest['pdf'],manifest['target'],manifest['pages'])
        else:
            count=None
            if importlib.util.find_spec('pypdfium2'):
                import pypdfium2 as pdfium
                pdf=pdfium.PdfDocument(manifest['pdf'])
                try:count=len(pdf)
                finally:pdf.close()
                if count!=manifest['pages']:raise ValueError(f"排版页数不符：预期 {manifest['pages']} 页，实际 {count} 页")
            shutil.copyfile(manifest['pdf'],manifest['target'])
        result={'ok':True,'pages':count}
    except Exception as error:result={'ok':False,'error':str(error) if isinstance(error,ValueError) else '渲染组件未完成导出，请检查依赖和素材'}
    Path(manifest['result']).write_text(json.dumps(result,ensure_ascii=False),encoding='utf8')

if __name__=='__main__' and len(sys.argv)==3 and sys.argv[1]=='--worker':worker(sys.argv[2])
