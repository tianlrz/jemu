import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import native_window as native

class Call:
    def __init__(self,fn):self.fn=fn
    def __call__(self,*args):return self.fn(*args)

class Windows(unittest.TestCase):
    def setUp(self):
        native.WINDOWS.clear();self.style=0x00CF0000;self.bounds=(20,30,1200,800);self.moves=[];self.pid=42;self.nonce=7
        class User:pass
        self.user=User()
        def placement(hwnd,p):p._obj.normal=w.RECT(20,30,1220,830);p._obj.show=1;return 1
        def monitor(handle,p):p._obj.monitor=w.RECT(0,0,1920,1080);return 1
        def position(hwnd,order,x,y,width,height,flags):
            self.moves.append((order,x,y,width,height,flags))
            if not flags&2:self.bounds=(x,y,width,height)
            return 1
        def style(hwnd,index,value):self.style=value;return 1
        def restore(hwnd,p):self.bounds=(20,30,1200,800);return 1
        def rect(hwnd,p):x,y,width,height=self.bounds;p._obj.left=x;p._obj.top=y;p._obj.right=x+width;p._obj.bottom=y+height;return 1
        for name,fn in {'IsWindow':lambda hwnd:True,'GetWindowThreadProcessId':lambda hwnd,p:setattr(p._obj,'value',self.pid),'GetPropW':lambda *args:self.nonce,'GetWindowPlacement':placement,'SetWindowPlacement':restore,'GetWindowRect':rect,'GetMonitorInfoW':monitor,'MonitorFromWindow':lambda *args:1,'GetWindowLongPtrW':lambda *args:self.style,'SetWindowLongPtrW':style,'SetWindowPos':position}.items():setattr(self.user,name,Call(fn))
        self.mock=patch.object(native,'functions',return_value=(c,w,self.user,None));self.mock.start()
        native.WINDOWS['owned']={'hwnd':123,'pid':42,'nonce':7,'created':0,'executable':'mock'}
    def tearDown(self):self.mock.stop();native.WINDOWS.clear()
    def test_full_monitor_no_caption_restore_original_bounds(self):
        native.fullscreen('owned',True);state=native.status('owned')
        self.assertTrue(state['borderless']);self.assertEqual(state['bounds'],state['monitor']);self.assertEqual(self.moves[0][0],0)
        self.assertEqual(self.moves[0][1:5],(0,0,1920,1080))
        self.assertEqual(len(self.moves),1);native.fullscreen('owned',True);self.assertEqual(len(self.moves),1)
        native.fullscreen('owned',False);self.assertEqual(self.style,0x00CF0000);self.assertFalse(native.status('owned')['borderless']);self.assertEqual(self.bounds,(20,30,1200,800))
    def test_no_arbitrary_or_reused_window_access(self):
        for key in ('other',None,[],42):
            with self.assertRaises(ValueError):native.fullscreen(key,True)
        self.pid=43
        with self.assertRaises(ValueError):native.fullscreen('owned',True)
        self.pid=42;self.nonce=9
        with self.assertRaises(ValueError):native.fullscreen('owned',True)
        self.assertFalse(self.moves)
    def test_shutdown_restores_and_invalid_state_rejected(self):
        with self.assertRaises(ValueError):native.fullscreen('owned','true')
        native.fullscreen('owned',True);native.restore_all();self.assertFalse(native.status('owned')['fullscreen']);self.assertEqual(self.bounds,(20,30,1200,800))

if __name__=='__main__':unittest.main()
