"""Install the pinned emulator adapter into a local uv environment."""
import io
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "pokebot-gen3"
REVISION = "5dd898f830775d448b06db6f5cd65b930540f146"

if not VENDOR.exists():
    VENDOR.parent.mkdir(exist_ok=True)
    subprocess.run(["git", "clone", "https://github.com/40Cakes/pokebot-gen3.git", str(VENDOR)], check=True)
subprocess.run(["git", "checkout", REVISION], cwd=VENDOR, check=True)

# `uv sync` builds .venv from pyproject.toml (jevemon + its own deps, plus pytest). The vendored
# adapter's own requirements aren't a pyproject dependency (it's git-cloned, not pip-installable),
# so they're layered into that same venv afterward.
subprocess.run(["uv", "sync", "--extra", "dev"], cwd=ROOT, check=True)
sys.path.insert(0, str(VENDOR))
from requirements import required_modules

subprocess.run(["uv", "pip", "install", "--python", str(ROOT / ".venv/bin/python"), *required_modules], check=True)
url = "https://github.com/hanzi/libmgba-py/releases/download/0.2.0-2/libmgba-py_0.2.0_macos-arm64.zip"
if not (VENDOR / "mgba").exists():
    with urllib.request.urlopen(url, timeout=60) as response:
        with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
            archive.extractall(VENDOR)
print("Emulator dependencies installed. Run uv run python -m jevemon --help")
