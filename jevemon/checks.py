"""Offline regression check for journey mode (no API calls, no ROM RAM readback needed beyond RNG-free walking)."""
import json

from .paths import CONFIG


def check_journey(session_class, validate):
    base = json.loads(CONFIG.read_text())
    config = {**base, "agent": "baseline", "speed": 0, "audio": False}
    session = session_class()
    session.start(config)
    session.thread.join(timeout=180)
    if session.thread.is_alive():
        session.stop.set()
        session.thread.join(timeout=50)
    assert session.status["phase"] in ("finished", "stuck"), session.status.get("message")
    print(f"PASS journey walk (baseline, no API calls): {session.status['phase']} - {session.status.get('outcome')}")
