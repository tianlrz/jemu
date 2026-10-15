import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
from rendered_pages import PageParser,recover,MAX_PAGE


def document(page,text):
    return ('<!doctype html><script>const ignored="1 / 19";</script><div id="root"><div class="deck-toolbar"><button>previous</button><span>'+str(page)+' / 19</span></div><div class="deck-viewport"><div class="slide-canvas"><h1 data-node-id="edited">'+text+'</h1><img src="logo.png"><svg><path d="M0 0"/></svg></div></div></div>').encode()


class Recovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();server.DATA=Path(self.temp.name)/'library';server.DATA.mkdir();server.REVISION_CACHE.clear()
        self.deck,meta=server.create_deck('legacy','index.html',[('index.html',b'<div id="root"></div>')])
        self.write(1,'cover edited')
        self.meta=self.write(10,'method edited')
    def tearDown(self):self.temp.cleanup()
    def write(self,page,text,label='轻编辑保存'):
        (self.deck/'document/index.html').write_bytes(document(page,text));return server.snapshot(self.deck,label,[{'type':'text'}])
    def test_previous_gui_pages_recovered_without_modifying_sources(self):
        before=(self.deck/'meta.json').read_bytes();current=(self.deck/'document/index.html').read_bytes()
        result=recover(self.deck,self.meta,'index.html')
        self.assertEqual([p['index'] for p in result['pages']],[0]);self.assertIn('cover edited',result['pages'][0]['html'])
        self.assertNotIn('method edited',result['pages'][0]['html']);self.assertFalse(result['omitted'])
        self.assertEqual((self.deck/'meta.json').read_bytes(),before);self.assertEqual((self.deck/'document/index.html').read_bytes(),current)
    def test_latest_snapshot_for_each_page_wins(self):
        self.write(1,'cover newer');meta=self.write(3,'third page')
        pages={p['index']:p['html'] for p in recover(self.deck,meta,'index.html')['pages']}
        self.assertIn('cover newer',pages[0]);self.assertIn('method edited',pages[9])
    def test_never_resurrect_across_restore_or_external_edit(self):
        for label in ('恢复至 earlier','外部文件修改'):
            meta=self.write(4,label,label);self.assertEqual(recover(self.deck,meta,'index.html')['pages'],[])
    def test_parser_preserves_entities_and_void_nodes(self):
        parser=PageParser();parser.feed(document(2,'saved &amp; edited').decode());parser.close();value=parser.result()
        self.assertEqual(value['index'],1);self.assertIn('&amp;',value['html']);self.assertIn('<img',value['html']);self.assertIn('<path',value['html'])
        parser=PageParser()
        with self.assertRaises(ValueError):parser.feed(document(1,'a'*(MAX_PAGE+1)).decode())
    def test_history_scan_is_bounded(self):
        for i in range(12):meta=self.write(i+1,'edit '+str(i))
        result=recover(self.deck,meta,'index.html');self.assertEqual(result['checkedVersions'],8);self.assertTrue(result['omitted'])


if __name__=='__main__':unittest.main()
