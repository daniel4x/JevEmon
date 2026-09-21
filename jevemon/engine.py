"""Low-level emulator stepping: boot the ROM and build the journey checkpoint."""
from datetime import datetime

import modules.battle_handler as battle_handler
from modules.context import context
from modules.game import set_rom
from modules.libmgba import LibmgbaEmulator
from modules.profiles import Profile
from modules.roms import load_rom_data
from modules.save_import import get_state_data_from_png
from modules.stats import StatsDatabase

from .paths import DATA, ROOT, VENDOR, load_env
from .strategy import handle_action_selection, handle_fainted_replacement

FRAME_HOOK = None


def initialise():
    load_env()
    battle_handler.handle_fainted_pokemon = handle_fainted_replacement
    battle_handler.handle_battle_action_selection = handle_action_selection
    DATA.mkdir(exist_ok=True)
    rom = load_rom_data(ROOT / "Pokemon - Fire Red Version (U) (V1.1).gba")
    context.profile = Profile(rom, DATA, datetime.now())
    set_rom(rom)
    context.testing = True
    # Trainer battles never touch this, but get_encounter_type() reads context.stats.last_fishing_attempt
    # unconditionally for any non-trainer battle, so wild encounters (journey mode) need it set.
    context.stats = StatsDatabase(context.profile)
    context.emulator = LibmgbaEmulator(context.profile, lambda: None, is_test_run=True)
    context.emulator.set_audio_enabled(False)
    context.emulator.set_video_enabled(True)
    context.emulator.set_throttle(False)
    context.bot_mode = "Jev"


def tick():
    global FRAME_HOOK
    context.frame += 1
    context.emulator.run_single_frame()
    if FRAME_HOOK:
        FRAME_HOOK()


def prepare_journey():
    fixture = VENDOR / "tests/states/firered/in_front_of_player_house_after_getting_starter.ss1"
    with fixture.open("rb") as file:
        state, save = get_state_data_from_png(file)
    context.emulator.load_save_game(save)
    context.emulator.load_save_state(state)
    tick()
    (DATA / "journey.state").write_bytes(context.emulator.get_save_state())
    (DATA / "journey.sav").write_bytes(context.emulator.read_save_data())
    context.emulator.get_screenshot().save(DATA / "journey.png")
    print("Prepared journey checkpoint (Pallet Town)", flush=True)
