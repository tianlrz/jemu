"""Store WebDAV app credentials with Windows DPAPI; never expose them in config JSON."""
import ctypes
from ctypes import wintypes
import json
import os


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(value, decrypt=False):
    if os.name != 'nt':
        raise ValueError('当前版本仅在 Windows 上保存 WebDAV 凭据')
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source = Blob(len(value), buffer); target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = function(ctypes.byref(source), 'WebDAV app credential', None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise ValueError('无法读取或保护本机凭据，请重新填写应用密码')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def save_secret(path, username, password, atomic_write):
    value = json.dumps({'username': username, 'password': password}, ensure_ascii=False).encode('utf8')
    atomic_write(path, _crypt(value))


def load_secret(path, username):
    if not path.is_file():
        raise ValueError('请先保存坚果云账号和应用密码')
    if path.stat().st_size > 32768:
        raise ValueError('本机凭据文件无效，请重新设置')
    value = json.loads(_crypt(path.read_bytes(), True))
    if value.get('username') != username or not isinstance(value.get('password'), str):
        raise ValueError('账号已变化，请为此账号重新填写应用密码')
    return value['password']
