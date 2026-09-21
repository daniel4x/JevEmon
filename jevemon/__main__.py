"""CLI: build the journey checkpoint, run an offline check, run one walk, or serve the UI."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _ensure_mgba_library_path():
    """macOS only: libmgba-py's compiled extension links against @rpath/libmgba, which
    Homebrew provides but dyld only resolves from DYLD_LIBRARY_PATH set at process launch —
    setting it mid-process has no effect. Re-exec once with it in place so a plain
    `uv run python -m jevemon` works with no wrapper script."""
    if sys.platform != "darwin" or os.environ.get("_JEVEMON_REEXEC"):
        return
    try:
        prefix = subprocess.run(["brew", "--prefix", "mgba"], capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return
    lib_dir = f"{prefix}/lib"
    if lib_dir in os.environ.get("DYLD_LIBRARY_PATH", "").split(":"):
        return
    env = {**os.environ, "_JEVEMON_REEXEC": "1", "DYLD_LIBRARY_PATH": lib_dir}
    os.execve(sys.executable, [sys.executable, "-m", "jevemon", *sys.argv[1:]], env)


_ensure_mgba_library_path()

from . import engine
from .config import validate_config
from .paths import CONFIG, DATA
from .runner import Session


def main():
    parser = argparse.ArgumentParser(prog="python -m jevemon")
    parser.add_argument("--prepare", action="store_true", help="Build the Pallet Town journey checkpoint")
    parser.add_argument("--run", action="store_true", help="Run one configured walk and exit")
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--speed", type=int, choices=(0, 1, 2, 4))
    parser.add_argument("--agent", choices=("jev", "baseline"))
    parser.add_argument("--check-journey", action="store_true", help="Walk the Pallet Town journey offline (agent: baseline, no API calls)")
    args = parser.parse_args()
    engine.initialise()
    if args.check_journey:
        from .checks import check_journey
        check_journey(Session, validate_config)
    elif args.prepare:
        engine.prepare_journey()
    else:
        if not (DATA / "journey.state").exists():
            engine.prepare_journey()
        session = Session()
        if args.run:
            config = json.loads(Path(args.config).read_text())
            if args.speed is not None:
                config["speed"] = args.speed
                config["audio"] = args.speed != 0
            if args.agent:
                config["agent"] = args.agent
            session.start(config)
            session.thread.join()
            if session.status["phase"] != "finished":
                sys.exit(1)
        else:
            from .server import serve
            serve(session, args.port)


if __name__ == "__main__":
    main()
