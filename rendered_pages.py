"""Bounded recovery of GUI-edited pages from the old single-page React save format."""
from html.parser import HTMLParser
import re
import zipfile

MAX_VERSIONS=8
MAX_ENTRY=10*1024*1024
MAX_PAGE=512*1024
MAX_OUTPUT=4*1024*1024
VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False);self.stack=[];self.toolbar=None;self.viewport=None;self.capture=None;self.parts=[];self.size=0;self.nav=[];self.html=None
    def append(self,text):
        self.size+=len(text.encode('utf8'))
        if self.size>MAX_PAGE:raise ValueError('渲染页恢复超过大小上限')
        self.parts.append(text)
    def handle_starttag(self,tag,attributes):
        attrs=dict(attributes);classes=(attrs.get('class') or '').split();depth=len(self.stack)
        if 'deck-toolbar' in classes:self.toolbar=depth
        if 'deck-viewport' in classes:self.viewport=depth
        if self.viewport is not None and 'slide-canvas' in classes and self.html is None and self.capture is None:self.capture=depth
        if self.capture is not None:self.append(self.get_starttag_text())
        if tag not in VOID:self.stack.append(tag)
    def handle_endtag(self,tag):
        if self.capture is not None:self.append('</'+tag+'>')
        if tag in self.stack:
            depth=len(self.stack)-1-self.stack[::-1].index(tag);self.stack=self.stack[:depth]
            if self.capture==depth:self.html=''.join(self.parts);self.capture=None
            if self.toolbar==depth:self.toolbar=None
            if self.viewport==depth:self.viewport=None
    def handle_startendtag(self,tag,attrs):
        if self.capture is not None:self.append(self.get_starttag_text())
    def handle_data(self,text):
        if self.toolbar is not None:self.nav.append(text)
        if self.capture is not None:self.append(text)
    def handle_entityref(self,name):
        if self.capture is not None:self.append('&'+name+';')
    def handle_charref(self,name):
        if self.capture is not None:self.append('&#'+name+';')
    def handle_comment(self,text):
        if self.capture is not None:self.append('<!--'+text+'-->')
    def result(self):
        match=re.search(r'(\d+)\s*/\s*(\d+)',''.join(self.nav))
        if not match or not self.html or 'data-node-id=' not in self.html:return None
        page,total=map(int,match.groups())
        if not 1<=page<=total<=200:return None
        return {'index':page-1,'html':self.html}


def recover(directory,meta,entry):
    pages={};checked=0;size=0;omitted=False
    # Only consecutive old GUI saves are eligible. Never resurrect edits across a restore/import/external rewrite.
    versions=list(reversed(meta.get('versions',[])))
    if not versions or versions[0].get('label')!='轻编辑保存':return {'pages':[],'checkedVersions':0,'omitted':False}
    import codecs
    for version in versions[1:]:
        if version.get('label')!='轻编辑保存':break
        if checked>=MAX_VERSIONS:omitted=True;break
        checked+=1
        try:
            version_id=version.get('id','')
            if not isinstance(version_id,str) or not re.fullmatch(r'\d{8}-\d{6}-[a-f0-9]{6}',version_id):omitted=True;continue
            target=directory/'versions'/(version_id+'.zip')
            if target.is_symlink():omitted=True;continue
            with zipfile.ZipFile(target) as archive:
                info=archive.getinfo(entry)
                if info.file_size>MAX_ENTRY:omitted=True;continue
                parser=PageParser();decoder=codecs.getincrementaldecoder('utf8')('replace')
                with archive.open(info) as stream:
                    for chunk in iter(lambda:stream.read(65536),b''):parser.feed(decoder.decode(chunk))
                    parser.feed(decoder.decode(b'',final=True));parser.close()
                page=parser.result()
                if page and page['index'] not in pages:
                    count=len(page['html'].encode('utf8'))
                    if size+count>MAX_OUTPUT:omitted=True;break
                    pages[page['index']]=page;size+=count
        except (OSError,ValueError,KeyError,zipfile.BadZipFile):omitted=True
    return {'pages':list(pages.values()),'checkedVersions':checked,'omitted':omitted}
