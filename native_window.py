"""Manage only Jemu-launched Windows app windows; no browser automation."""
import os
from pathlib import Path
import secrets
import time

WINDOWS={}

def title(key):return 'Jemu · 简幕 · '+key

def allocate(executable):
    for key,value in list(WINDOWS.items()):
        if time.monotonic()-value['created']>120 and not value.get('hwnd'):WINDOWS.pop(key,None)
        elif value.get('hwnd') and not valid(value):WINDOWS.pop(key,None)
    if len(WINDOWS)>=4:raise ValueError('桌面窗口已达上限，请先关闭不使用的窗口')
    key=secrets.token_hex(12);WINDOWS[key]={'created':time.monotonic(),'executable':str(Path(executable).resolve()),'nonce':secrets.randbelow(2**30)+1};return key

def functions():
    if os.name!='nt':raise ValueError('系统无边框窗口目前仅支持 Windows；浏览器可使用全屏按钮')
    import ctypes as c
    from ctypes import wintypes as w
    user=c.WinDLL('user32',use_last_error=True);kernel=c.WinDLL('kernel32',use_last_error=True)
    user.IsWindow.argtypes=[w.HWND];user.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)]
    user.SetPropW.argtypes=[w.HWND,w.LPCWSTR,w.HANDLE];user.GetPropW.argtypes=[w.HWND,w.LPCWSTR];user.GetPropW.restype=w.HANDLE
    user.GetWindowLongPtrW.argtypes=[w.HWND,c.c_int];user.GetWindowLongPtrW.restype=c.c_ssize_t
    user.SetWindowLongPtrW.argtypes=[w.HWND,c.c_int,c.c_ssize_t];user.SetWindowLongPtrW.restype=c.c_ssize_t
    user.SetWindowPos.argtypes=[w.HWND,w.HWND,c.c_int,c.c_int,c.c_int,c.c_int,w.UINT]
    user.MonitorFromWindow.argtypes=[w.HWND,w.DWORD];user.MonitorFromWindow.restype=w.HANDLE
    user.GetWindowTextLengthW.argtypes=[w.HWND];user.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,c.c_int]
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD)];kernel.CloseHandle.argtypes=[w.HANDLE]
    return c,w,user,kernel

def valid(record):
    c,w,user,kernel=functions();hwnd=record.get('hwnd')
    if not hwnd or not user.IsWindow(hwnd):return False
    pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,c.byref(pid))
    return pid.value==record.get('pid') and user.GetPropW(hwnd,'JemuOwnedWindow')==record.get('nonce')

def attach(key):
    if not isinstance(key,str):raise ValueError('桌面窗口会话无效')
    record=WINDOWS.get(key)
    if not record:raise ValueError('桌面窗口会话已失效，请重新打开桌面窗口')
    c,w,user,kernel=functions()
    if record.get('hwnd') and valid(record):return {'attached':True,'fullscreen':bool(record.get('fullscreen'))}
    found=[]
    @c.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
    def visit(hwnd,unused):
        size=user.GetWindowTextLengthW(hwnd)
        if size<1 or size>256:return True
        text=c.create_unicode_buffer(size+1);user.GetWindowTextW(hwnd,text,size+1)
        if text.value!=title(key) and not text.value.startswith(title(key)+' - '):return True
        pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,c.byref(pid))
        handle=kernel.OpenProcess(0x1000,False,pid.value)
        if not handle:return True
        try:
            count=w.DWORD(32768);path=c.create_unicode_buffer(count.value)
            if kernel.QueryFullProcessImageNameW(handle,0,path,c.byref(count)) and os.path.normcase(str(Path(path.value).resolve()))==os.path.normcase(record['executable']):found.append((int(hwnd),pid.value))
        finally:kernel.CloseHandle(handle)
        return True
    user.EnumWindows.argtypes=[type(visit),w.LPARAM];user.EnumWindows(visit,0)
    if len(found)!=1:return {'attached':False,'fullscreen':False}
    if not user.SetPropW(found[0][0],'JemuOwnedWindow',record['nonce']):raise ValueError('无法注册桌面窗口')
    record.update(hwnd=found[0][0],pid=found[0][1]);return {'attached':True,'fullscreen':False}

def fullscreen(key,enabled):
    if not isinstance(key,str):raise ValueError('桌面窗口会话无效')
    record=WINDOWS.get(key)
    if not record or not valid(record):raise ValueError('桌面窗口已关闭或尚未就绪')
    if type(enabled) is not bool:raise ValueError('全屏状态无效')
    if bool(record.get('fullscreen'))==enabled:return {'fullscreen':enabled}
    c,w,user,kernel=functions();hwnd=record['hwnd']
    class Placement(c.Structure):_fields_=[('length',w.UINT),('flags',w.UINT),('show',w.UINT),('minimum',w.POINT),('maximum',w.POINT),('normal',w.RECT)]
    class Monitor(c.Structure):_fields_=[('size',w.DWORD),('monitor',w.RECT),('work',w.RECT),('flags',w.DWORD)]
    user.GetWindowPlacement.argtypes=[w.HWND,c.POINTER(Placement)];user.SetWindowPlacement.argtypes=[w.HWND,c.POINTER(Placement)]
    user.GetMonitorInfoW.argtypes=[w.HANDLE,c.POINTER(Monitor)]
    if enabled:
        placement=Placement();placement.length=c.sizeof(placement)
        monitor=Monitor();monitor.size=c.sizeof(monitor)
        if not user.GetWindowPlacement(hwnd,c.byref(placement)) or not user.GetMonitorInfoW(user.MonitorFromWindow(hwnd,2),c.byref(monitor)):raise ValueError('无法读取窗口与屏幕位置')
        style=user.GetWindowLongPtrW(hwnd,-16)
        record.update(style=style,placement=bytes(placement))
        # No topmost flag: the independent presenter must remain usable.
        user.SetWindowLongPtrW(hwnd,-16,style & ~(0x00C00000|0x00040000|0x00020000|0x00010000))
        r=monitor.monitor
        if not user.SetWindowPos(hwnd,0,r.left,r.top,r.right-r.left,r.bottom-r.top,0x20|0x40|0x10):
            user.SetWindowLongPtrW(hwnd,-16,style);raise ValueError('无法进入系统全屏')
    else:
        user.SetWindowLongPtrW(hwnd,-16,record['style'])
        placement=Placement.from_buffer_copy(record['placement'])
        user.SetWindowPlacement(hwnd,c.byref(placement));user.SetWindowPos(hwnd,None,0,0,0,0,0x20|0x2|0x1|0x4|0x10)
        record.pop('placement',None);record.pop('style',None)
    record['fullscreen']=enabled;return {'fullscreen':enabled}

def status(key):
    if not isinstance(key,str):raise ValueError('桌面窗口会话无效')
    record=WINDOWS.get(key)
    if not record or not valid(record):raise ValueError('桌面窗口未连接')
    c,w,user,kernel=functions()
    class Monitor(c.Structure):_fields_=[('size',w.DWORD),('monitor',w.RECT),('work',w.RECT),('flags',w.DWORD)]
    user.GetWindowRect.argtypes=[w.HWND,c.POINTER(w.RECT)];user.GetMonitorInfoW.argtypes=[w.HANDLE,c.POINTER(Monitor)]
    rect=w.RECT();monitor=Monitor();monitor.size=c.sizeof(monitor)
    user.GetWindowRect(record['hwnd'],c.byref(rect));user.GetMonitorInfoW(user.MonitorFromWindow(record['hwnd'],2),c.byref(monitor))
    def box(r):return {'x':r.left,'y':r.top,'width':r.right-r.left,'height':r.bottom-r.top}
    return {'attached':True,'fullscreen':bool(record.get('fullscreen')),'borderless':not bool(user.GetWindowLongPtrW(record['hwnd'],-16)&(0x00C00000|0x00040000)),'bounds':box(rect),'monitor':box(monitor.monitor)}

def restore_all():
    for key,record in list(WINDOWS.items()):
        if record.get('fullscreen'):
            try:fullscreen(key,False)
            except (ValueError,OSError):pass
