"""Session orchestration: recording, pause/stop control, and the journey run loop."""
import io
import json
import os
import queue
import subprocess
import threading
import time
import traceback
import wave
from datetime import datetime

from modules.context import context
from modules.debug_utilities import debug_write_party
from modules.map_data import MapFRLG

from . import engine
from .config import validate_config
from .journey import Journey
from .paths import DATA, load_env
from .strategy import make_party

JOURNEY_TARGET = MapFRLG.VIRIDIAN_CITY.value


class Session:
    def __init__(self):
        self.status = {"phase": "ready", "message": "Set your lineup, then go live.", "decisions": 0}
        self.jpeg = (DATA / "journey.png").read_bytes() if (DATA / "journey.png").exists() else b""
        self.thread = None
        self.stop = threading.Event()
        self.paused = threading.Event()
        self.lock = threading.Lock()

    def check_stop(self):
        if self.stop.is_set():
            raise InterruptedError("Run stopped by player.")

    def start(self, config):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("A run is already in progress. Stop it before starting another.")
            self.config = validate_config(config)
            load_env()
            key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
            if config["agent"] == "jev" and not key:
                raise ValueError("Set TYPESAFE_API_KEY in .env or in the environment.")
            self.key = key if config["agent"] == "jev" else None
            self.stop.clear()
            self.paused.clear()
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def play_journey(self):
        journey = Journey(self)
        journey.settle()
        while True:
            self.check_stop()
            if JOURNEY_TARGET in journey.visited_maps and journey.battles >= 1:
                self.status.update(phase="finished",
                    message=f"Reached Viridian City after {journey.battles} battle(s).",
                    outcome="Reached Viridian City")
                return
            if journey.legs >= self.config["max_legs"]:
                self.status.update(phase="stuck", message=f"Stopped after {journey.legs} legs (leg budget reached).", outcome="Budget reached")
                return
            if journey.legs_since_progress >= self.config["stuck_limit"]:
                self.status.update(phase="stuck", message=f"No progress in {journey.legs_since_progress} legs. Stopping.", outcome="Stuck")
                return
            options = journey.valid_destinations()
            if not options:
                self.status.update(phase="stuck", message="No reachable destinations remain.", outcome="No destinations")
                return
            destination = journey.decide(options)
            journey.take(destination)
            journey.settle()

    def frame(self):
        self.check_stop()
        while self.paused.is_set():
            self.check_stop()
            time.sleep(0.05)
        self.frames += 1
        audio_queue = context.emulator.get_last_audio_data()
        while True:
            try:
                audio = audio_queue.get_nowait()
                if self.audio_file:
                    self.audio_file.writeframesraw(audio)
            except queue.Empty:
                break
        if self.frames % 2 == 0:
            image = context.emulator.get_screenshot()
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90)
            self.jpeg = buffer.getvalue()
            self.video.stdin.write(image.tobytes())
        self.status["frames"] = self.frames

    def run(self):
        self.run_dir = DATA / "recordings" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.run_dir.mkdir(parents=True)
        self.status = {"phase": "starting", "message": "Loading your party into FireRed", "decisions": 0,
            "run": self.run_dir.name, "agent": self.config["agent"]}
        self.metrics = {"api_calls": 0, "input_tokens": 0, "output_tokens": 0, "last_latency_ms": 0,
            "estimated_cost_usd": 0, "reused_decisions": 0, "latency_ms": 0}
        self.status["metrics"] = self.metrics
        self.frames = 0
        self.video = None
        self.audio_file = None
        try:
            context.emulator.load_save_game((DATA / "journey.sav").read_bytes())
            context.emulator.load_save_state((DATA / "journey.state").read_bytes())
            context.emulator.reset_held_buttons()
            engine.tick()
            debug_write_party(make_party(self.config["party"]))
            context.bot_mode = "Jev"
            context.emulator.set_audio_enabled(self.config["audio"])
            context.emulator.set_throttle(self.config["speed"] != 0)
            context.emulator.set_speed_factor(self.config["speed"] or 1)
            if self.config["audio"] and self.config["speed"]:
                self.audio_file = wave.open(str(self.run_dir / "audio.wav"), "wb")
                self.audio_file.setnchannels(2)
                self.audio_file.setsampwidth(2)
                self.audio_file.setframerate(round(context.emulator.get_sample_rate() / self.config["speed"]))
            (self.run_dir / "config.json").write_text(json.dumps(self.config, indent=2))
            (self.run_dir / "start.state").write_bytes(context.emulator.get_save_state())
            self.video = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "240x160", "-framerate", "29.86375",
                "-i", "pipe:0", "-vf", "scale=720:480:flags=neighbor", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(self.run_dir / "replay.mp4")], stdin=subprocess.PIPE)
            engine.FRAME_HOOK = self.frame
            for _ in range(4):
                # Guarantees a real screenshot reaches the UI before any step that might block --
                # the journey checkpoint can load straight into a controllable, script-free overworld,
                # skipping every tick before the first Jev call.
                engine.tick()
            self.play_journey()
        except InterruptedError as error:
            self.status.update(phase="stopped", message=str(error))
        except Exception as error:
            traceback.print_exc()
            self.status.update(phase="error", message=str(error))
        finally:
            engine.FRAME_HOOK = None
            context.emulator.set_audio_enabled(False)
            context.emulator.set_throttle(False)
            context.emulator.reset_held_buttons()
            if self.video:
                self.video.stdin.close()
                code = self.video.wait(timeout=30)
                if code:
                    self.status.update(phase="error", message=f"Video recording failed with code {code}")
            if self.audio_file:
                self.audio_file.close()
                muxed = self.run_dir / "with-audio.mp4"
                mux = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(self.run_dir / "replay.mp4"), "-i", str(self.run_dir / "audio.wav"),
                    "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(muxed)])
                if mux.returncode == 0:
                    muxed.replace(self.run_dir / "replay.mp4")
                else:
                    self.status.update(phase="error", message="Could not add game audio to recording.")
            self.status["agent"] = self.config["agent"]
            context.emulator.get_screenshot().save(self.run_dir / "final.png")
            (self.run_dir / "final.state").write_bytes(context.emulator.get_save_state())
            (self.run_dir / "summary.json").write_text(json.dumps(self.status, indent=2))
            print(json.dumps({k: v for k, v in self.status.items() if k not in ("journey", "last_decision")}), flush=True)
