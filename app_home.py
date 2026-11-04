"""Where Jemu keeps its library and runtime files.

A normal folder copy keeps them next to the code, as before. Inside a macOS .app bundle the code
may be read-only (opened from a DMG, App Translocation) and is replaced on update, so data goes to
~/Library/Application Support/Jemu instead. JEMU_HOME overrides both.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def in_app_bundle(root=ROOT):
    return any(part.endswith('.app') for part in root.parts) and root.parent.name == 'Resources' and root.parent.parent.name == 'Contents'


def home(root=ROOT, environ=os.environ):
    value = environ.get('JEMU_HOME')
    if value:
        return Path(value).expanduser().resolve()
    if in_app_bundle(root):
        return Path.home() / 'Library' / 'Application Support' / 'Jemu'
    return root


HOME = home()
LIBRARY = HOME / 'library'
RUNTIME = HOME / '.runtime'
