"""macOS launch, export-browser and Keychain paths, checked on any OS with fakes (no real Keychain or browser)."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app_home
import credentials
import render_exports
import server

ROOT=Path(__file__).resolve().parents[1]

class FakeKeychain:
    items={}
    def write(self,account,value):self.items[account]=bytes(value)
    def read(self,account):
        if account not in self.items:raise ValueError('钥匙串中没有找到应用密码，请重新填写')
        return self.items[account]
    def delete(self,account):self.items.pop(account,None)

def write(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)

class Keychain(unittest.TestCase):
    def setUp(self):
        FakeKeychain.items={};self.temp=tempfile.TemporaryDirectory();self.secret=Path(self.temp.name)/'.settings/webdav.secret'
        self.patches=[patch.object(credentials.sys,'platform','darwin'),patch.object(credentials,'_keychain',FakeKeychain)]
        for item in self.patches:item.start()
    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()
    def test_password_stays_in_keychain_not_in_file(self):
        credentials.save_secret(self.secret,'test@example.invalid','local-test-secret',write)
        self.assertNotIn(b'local-test-secret',self.secret.read_bytes())
        marker=json.loads(self.secret.read_bytes());self.assertEqual(marker['store'],'macos-keychain');self.assertNotIn('username',marker)
        self.assertEqual(credentials.load_secret(self.secret,'test@example.invalid'),'local-test-secret')
        with self.assertRaises(ValueError):credentials.load_secret(self.secret,'other@example.invalid')
    def test_resave_reuses_account_and_delete_clears_item(self):
        credentials.save_secret(self.secret,'a@example.invalid','one',write);first=json.loads(self.secret.read_bytes())['account']
        credentials.save_secret(self.secret,'b@example.invalid','two',write)
        self.assertEqual(json.loads(self.secret.read_bytes())['account'],first);self.assertEqual(len(FakeKeychain.items),1)
        self.assertEqual(credentials.load_secret(self.secret,'b@example.invalid'),'two')
        credentials.delete_secret(self.secret)
        self.assertFalse(self.secret.exists());self.assertEqual(FakeKeychain.items,{})
    def test_missing_item_or_bad_marker_asks_to_reenter(self):
        credentials.save_secret(self.secret,'a@example.invalid','one',write);FakeKeychain.items={}
        with self.assertRaises(ValueError):credentials.load_secret(self.secret,'a@example.invalid')
        self.secret.write_bytes(b'not json')
        with self.assertRaises(ValueError):credentials.load_secret(self.secret,'a@example.invalid')
        credentials.delete_secret(self.secret);self.assertFalse(self.secret.exists())

class Browsers(unittest.TestCase):
    def test_mac_app_bundles_are_found_for_export_and_desktop(self):
        with tempfile.TemporaryDirectory() as home:
            chrome=Path(home)/'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
            everything=render_exports.mac_browsers(home)
            self.assertIn(Path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'),everything);self.assertIn(chrome,everything)
            self.assertFalse(any('Safari' in str(path) for path in everything))
            # Only the temporary home: a real Mac or CI runner may have Chrome in /Applications.
            candidates=[path for path in everything if str(path).startswith(home)]
            write(chrome,b'')
            with patch.object(render_exports.sys,'platform','darwin'),patch.object(render_exports,'mac_browsers',lambda:candidates),patch.object(render_exports.shutil,'which',lambda name:None):
                self.assertEqual(render_exports.browser_path(),str(chrome))
            with patch.object(server.os,'name','posix'),patch.object(server.sys,'platform','darwin'),patch.object(render_exports,'mac_browsers',lambda:candidates):
                self.assertEqual(server.desktop_browser(),chrome)
    @unittest.skipIf(os.name=='nt','POSIX launch path')
    def test_mac_window_uses_app_mode_without_native_window_key(self):
        browser=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        with patch.object(server,'desktop_browser',lambda:browser),patch.object(server.subprocess,'Popen') as popen,patch.object(server.native_window,'allocate') as allocate:
            server.open_window('http://127.0.0.1:9/?deck=deck-1')
        allocate.assert_not_called();args=popen.call_args.args[0]
        self.assertEqual(args[0],str(browser));self.assertIn('--app=http://127.0.0.1:9/?deck=deck-1',args);self.assertNotIn('window=',' '.join(args))
    @unittest.skipIf(os.name=='nt','POSIX process groups')
    def test_stop_tree_kills_browser_helpers_in_process_group(self):
        process=subprocess.Popen(['sh','-c','sleep 30 & sleep 30 & wait'],start_new_session=True)
        render_exports.stop_tree(process);self.assertIsNotNone(process.returncode)
        with self.assertRaises(ProcessLookupError):os.killpg(process.pid,0)

class Launchers(unittest.TestCase):
    def test_launchers_are_executable_and_need_python_310(self):
        for name in ('Start.command','jemu'):
            path=ROOT/name;self.assertTrue(path.read_bytes().startswith(b'#!/bin/sh\n'))
            self.assertNotIn(b'\r\n',path.read_bytes())
            if os.name!='nt':self.assertTrue(os.access(path,os.X_OK),name)
        self.assertIn(b'(3, 10)',(ROOT/'jemu_python.sh').read_bytes())
        self.assertIn(b'launch.pyw',(ROOT/'Start.command').read_bytes())
    @unittest.skipIf(os.name=='nt','POSIX shell')
    def test_cli_wrapper_runs_jemu(self):
        run=subprocess.run([str(ROOT/'jemu'),'--help'],capture_output=True,text=True,timeout=30)
        self.assertEqual(run.returncode,0,run.stderr);self.assertIn('usage: jemu',run.stdout)

class AppHome(unittest.TestCase):
    def test_folder_copy_keeps_data_beside_code(self):
        root=Path('/Users/me/Downloads/jemu')
        self.assertFalse(app_home.in_app_bundle(root));self.assertEqual(app_home.home(root,{}),root)
    def test_app_bundle_uses_application_support(self):
        root=Path('/Applications/Jemu.app/Contents/Resources/app')
        self.assertTrue(app_home.in_app_bundle(root))
        self.assertEqual(app_home.home(root,{}),Path.home()/'Library/Application Support/Jemu')
        # Translocated copies from a DMG are read-only random paths; still detected.
        self.assertTrue(app_home.in_app_bundle(Path('/private/var/folders/x/AppTranslocation/ABC/d/Jemu.app/Contents/Resources/app')))
    def test_override_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(app_home.home(Path('/Applications/Jemu.app/Contents/Resources/app'),{'JEMU_HOME':temp}),Path(temp).resolve())

class Bundle(unittest.TestCase):
    @unittest.skipIf(os.name=='nt' or sys.platform=='darwin','POSIX build without DMG; macOS builds the DMG in its own workflow')
    def test_app_bundle_layout(self):
        if subprocess.run(['git','-C',str(ROOT),'rev-parse','HEAD'],capture_output=True).returncode:self.skipTest('needs a git checkout')
        with tempfile.TemporaryDirectory() as out:
            run=subprocess.run(['sh',str(ROOT/'packaging/macos/build_dmg.sh'),out],capture_output=True,text=True,timeout=60)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            app=Path(out)/'Jemu.app/Contents';code=app/'Resources/app'
            self.assertTrue(os.access(app/'MacOS/Jemu',os.X_OK))
            plist=(app/'Info.plist').read_text();self.assertNotIn('@VERSION@',plist);self.assertIn('<string>Jemu</string>',plist)
            for name in ('server.py','launch.pyw','app_home.py','jemu_python.sh','web/index.html','starter'):self.assertTrue((code/name).exists(),name)
            for name in ('tests','library','.runtime','Start.cmd','packaging'):self.assertFalse((code/name).exists(),name)
            self.assertTrue(app_home.in_app_bundle(code.resolve()))

if __name__=='__main__':unittest.main()
