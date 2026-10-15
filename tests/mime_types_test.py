from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server

class ScriptTypes(unittest.TestCase):
    def test_system_registry_cannot_make_scripts_plain_text(self):
        for guessed in ('text/plain','application/octet-stream',None):
            with patch('mimetypes.guess_type',return_value=(guessed,None)):
                for name in ('app.js','module.mjs','MODULE.MJS'):
                    self.assertEqual(server.content_mime(Path(name)),'text/javascript')
    def test_other_document_types_keep_their_mappings(self):
        for name,expected in [('style.css','text/css'),('image.png','image/png')]:
            with patch('mimetypes.guess_type',return_value=(expected,None)):
                self.assertEqual(server.content_mime(Path(name)),expected)

if __name__=='__main__':unittest.main()
