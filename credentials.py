"""Store WebDAV app credentials with Windows DPAPI or the macOS login Keychain; never expose them in config JSON.

On macOS the secret file holds only a random Keychain account name. The password is passed to
Security.framework in memory, never on a command line, so it does not show up in the process list.
"""
import ctypes
from ctypes import wintypes
import json
import os
import secrets
import sys

KEYCHAIN_SERVICE = b'Jemu WebDAV'
KEYCHAIN_MARKER = 'macos-keychain'


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(value, decrypt=False):
    if os.name != 'nt':
        raise ValueError('当前版本仅在 Windows 和 macOS 上保存 WebDAV 凭据')
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


class _Keychain:
    """Generic-password items in the default (login) Keychain via Security.framework."""
    NOT_FOUND = -25300

    def __init__(self):
        security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        core = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        u32, ptr = ctypes.c_uint32, ctypes.c_void_p
        self.add = security.SecKeychainAddGenericPassword
        self.add.argtypes = [ptr, u32, ctypes.c_char_p, u32, ctypes.c_char_p, u32, ctypes.c_char_p, ctypes.POINTER(ptr)]
        self.find = security.SecKeychainFindGenericPassword
        self.find.argtypes = [ptr, u32, ctypes.c_char_p, u32, ctypes.c_char_p, ctypes.POINTER(u32), ctypes.POINTER(ptr), ctypes.POINTER(ptr)]
        self.free = security.SecKeychainItemFreeContent
        self.free.argtypes = [ptr, ptr]
        self.remove = security.SecKeychainItemDelete
        self.remove.argtypes = [ptr]
        self.release = core.CFRelease
        self.release.argtypes = [ptr]
        for function in (self.add, self.find, self.free, self.remove):
            function.restype = ctypes.c_int32

    def _item(self, account, want_data=False):
        length, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        status = self.find(None, len(KEYCHAIN_SERVICE), KEYCHAIN_SERVICE, len(account), account,
                           ctypes.byref(length) if want_data else None, ctypes.byref(data) if want_data else None, ctypes.byref(item))
        if status == self.NOT_FOUND:
            return None, None
        if status:
            raise ValueError('无法读取钥匙串中的凭据，请在钥匙串访问中允许 Jemu 后重试')
        value = None
        if want_data:
            try:
                value = ctypes.string_at(data, length.value)
            finally:
                self.free(None, data)
        return item, value

    def delete(self, account):
        item, _ = self._item(account)
        if item:
            try:
                self.remove(item)
            finally:
                self.release(item)

    def write(self, account, value):
        self.delete(account)
        item = ctypes.c_void_p()
        if self.add(None, len(KEYCHAIN_SERVICE), KEYCHAIN_SERVICE, len(account), account, len(value), value, ctypes.byref(item)):
            raise ValueError('无法把凭据写入钥匙串，请重新填写应用密码')
        if item:
            self.release(item)

    def read(self, account):
        item, value = self._item(account, True)
        if item:
            self.release(item)
        if value is None:
            raise ValueError('钥匙串中没有找到应用密码，请重新填写')
        return value


def _keychain():
    return _Keychain()


def _account(path):
    try:
        marker = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if isinstance(marker, dict) and marker.get('store') == KEYCHAIN_MARKER and isinstance(marker.get('account'), str):
        return marker['account'].encode('ascii', 'ignore')[:64] or None
    return None


def save_secret(path, username, password, atomic_write):
    value = json.dumps({'username': username, 'password': password}, ensure_ascii=False).encode('utf8')
    if sys.platform == 'darwin':
        account = (_account(path) if path.is_file() else None) or ('library-' + secrets.token_hex(12)).encode('ascii')
        _keychain().write(account, value)
        atomic_write(path, json.dumps({'store': KEYCHAIN_MARKER, 'account': account.decode('ascii')}).encode('utf8'))
        return
    atomic_write(path, _crypt(value))


def load_secret(path, username):
    if not path.is_file():
        raise ValueError('请先保存坚果云账号和应用密码')
    if path.stat().st_size > 32768:
        raise ValueError('本机凭据文件无效，请重新设置')
    if sys.platform == 'darwin':
        account = _account(path)
        if not account:
            raise ValueError('本机凭据文件无效，请重新设置')
        value = json.loads(_keychain().read(account))
    else:
        value = json.loads(_crypt(path.read_bytes(), True))
    if value.get('username') != username or not isinstance(value.get('password'), str):
        raise ValueError('账号已变化，请为此账号重新填写应用密码')
    return value['password']


def delete_secret(path):
    """Remove the local secret; on macOS also its Keychain item. Cloud files are untouched."""
    if sys.platform == 'darwin' and path.is_file():
        account = _account(path)
        if account:
            _keychain().delete(account)
    path.unlink(missing_ok=True)
