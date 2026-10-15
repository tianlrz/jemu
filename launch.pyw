"""Open/reuse the local workspace without showing a console window."""
import json
from pathlib import Path
import sys
import urllib.request

import server

runtime = server.ROOT / ".runtime"
runtime.mkdir(exist_ok=True)
sys.stdout = (runtime / "server.log").open("a", encoding="utf-8")
sys.stderr = (runtime / "error.log").open("a", encoding="utf-8")

def launch():
    try:
        session = json.loads((runtime / "session.json").read_text(encoding="utf-8"))
        url = session["url"]
        if not url.startswith("http://127.0.0.1:"):
            raise ValueError("Invalid local address")
        with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
            health = json.load(response)
        if health.get("app") == "deckroom" and Path(health["library"]) == server.ROOT / "library":
            server.open_window(url)
            return
    except (OSError, ValueError, KeyError):
        pass
    sys.argv = [str(server.ROOT / "server.py"), "--desktop"]
    server.main()

if __name__ == "__main__":
    launch()
