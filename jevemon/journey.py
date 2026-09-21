"""Destination-choice overworld walking: Jev picks where to go, navigate_to() does the rest."""
import re
from dataclasses import dataclass

from modules.battle_handler import handle_battle
from modules.battle_state import battle_is_active
from modules.context import context
from modules.map import get_map_data_for_current_position
from modules.map_data import MapFRLG
from modules.map_path import PathFindingError, calculate_path
from modules.memory import GameState, get_game_state
from modules.modes._interface import BotModeError
from modules.modes.util.higher_level_actions import heal_in_pokemon_center
from modules.modes.util.map import find_closest_pokemon_center
from modules.modes.util.walking import navigate_to
from modules.player import get_player_avatar, get_player_location, player_avatar_is_controllable, player_avatar_is_standing_still
from modules.pokemon_party import get_party
from modules.tasks import get_global_script_context, task_is_active

from .decisions import ask_jev
from .engine import tick
from .strategy import JevStrategy, describe_party_hp

JOURNEY_LEG_FRAME_LIMIT = 12000


@dataclass
class Destination:
    key: str
    label: str
    kind: str
    map: tuple
    coordinates: tuple
    center: object = None
    alt_coordinates: tuple = ()


class Journey:
    """Destination-choice overworld walking: Jev picks where to go, navigate_to() does the rest."""

    def __init__(self, session):
        self.session = session
        self.journal = {}
        self.blocked = set()
        self.recent_legs = []
        self.legs = 0
        self.legs_since_progress = 0
        self.battles = 0
        self.visited_maps = {get_map_data_for_current_position().map_group_and_number}

    def settle(self, frame_limit=3000):
        # Also drains any whiteout dialogue: the rush-to-center cutscene and the "AfterWhiteOutHeal"
        # script both leave the avatar uncontrollable, and GameState briefly leaves OVERWORLD, so the
        # same three checks that clear an ordinary dialogue box clear a whiteout too.
        for _ in range(frame_limit):
            if (get_game_state() == GameState.OVERWORLD and not get_global_script_context().is_active
                    and player_avatar_is_controllable()):
                return
            context.emulator.press_button("B")
            tick()
        raise RuntimeError("Overworld did not settle before the next decision.")

    def _map_label(self, map_group_and_number, strip_prefix=None):
        try:
            name = MapFRLG(map_group_and_number).name
        except ValueError:
            return f"Map {map_group_and_number}"
        name = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", name).replace("_", " ")
        if strip_prefix and name.upper().startswith(strip_prefix.upper() + " "):
            name = name[len(strip_prefix) + 1:]
        return name.title()

    def _connection_target(self, here, connection):
        # A MapConnection only carries a direction and a 1-D offset along the shared border,
        # not a destination tile. Mirrors the coordinate stitching in map_path.py: the shared
        # axis maps 1:1 minus the offset, and we step a few tiles past the border either way.
        dest_size = connection.destination_map.map_size
        if connection.direction in ("North", "South"):
            perpendicular = max(0, min(dest_size[0] - 1, here.map_size[0] // 2 - connection.offset))
            depth = dest_size[1] - 3 if connection.direction == "North" else 2
            return (perpendicular, depth)
        perpendicular = max(0, min(dest_size[1] - 1, here.map_size[1] // 2 - connection.offset))
        depth = 2 if connection.direction == "East" else dest_size[0] - 3
        return (depth, perpendicular)

    def candidate_destinations(self):
        here = get_map_data_for_current_position()
        town = here.map_name
        options = []
        by_key = {}
        for warp in here.warps:
            destination = warp.destination_location
            key = f"warp:{destination.map_group_and_number}:{destination.local_position}"
            if key in by_key:
                # Some buildings have several physical warp tiles that all lead to the same place
                # (e.g. a door's threshold spans more than one tile) -- these collide on this key
                # since it's built from the shared destination, not the tile. Keep every one of
                # them as an alternate approach: travel() tries them in turn, since some don't
                # actually retrigger the warp from every direction (see _retrigger_warp).
                by_key[key].alt_coordinates += (warp.local_coordinates,)
                continue
            by_key[key] = Destination(key, f"Enter {self._map_label(destination.map_group_and_number, town)}",
                "warp", here.map_group_and_number, warp.local_coordinates)
            options.append(by_key[key])
        for connection in here.connections:
            destination_map = (connection.destination_map_group, connection.destination_map_number)
            coordinates = self._connection_target(here, connection)
            key = f"walk:{destination_map}:{coordinates}"
            options.append(Destination(key, f"Walk to {self._map_label(destination_map)}",
                "walk", destination_map, coordinates))
        if any(pokemon.current_hp < pokemon.total_hp for pokemon in get_party()):
            try:
                center = find_closest_pokemon_center(get_player_location())
            except BotModeError:
                center = None
            if center is not None:
                map_enum, coordinates = center.value
                options.append(Destination(f"heal:{center.name}", f"Heal at {self._map_label(map_enum)}",
                    "heal", map_enum, coordinates, center=center))
        return options

    def valid_destinations(self):
        source = get_player_location()
        valid = []
        for option in self.candidate_destinations():
            if option.key in self.blocked:
                continue
            reachable = False
            for coordinates in (option.coordinates,) + option.alt_coordinates:
                try:
                    calculate_path(source, (option.map, coordinates))
                    reachable = True
                    break
                except PathFindingError:
                    continue
            if not reachable:
                self.blocked.add(option.key)
                continue
            valid.append(option)
        return valid

    def describe_state(self, options):
        return {"game": "Pokemon FireRed USA v1.1, actual GBA ROM",
            "information": "Overworld state. Every destination below was pre-validated as reachable.",
            "location": self._map_label(get_player_avatar().map_group_and_number),
            "party": describe_party_hp(get_party()),
            "legs_taken": self.legs, "legs_since_progress": self.legs_since_progress,
            "battles_fought": self.battles, "recent_legs": self.recent_legs[-6:],
            "destinations": [{"label": option.label, "visited": self.journal.get(option.key, 0)} for option in options]}

    def decide(self, options):
        state = self.describe_state(options)
        if len(options) == 1:
            self.session.status.update(phase="playing", message=f"{options[0].label} (only option)", journey=state)
            return options[0]
        criteria = {option.key: f"{option.label}. Visited {self.journal.get(option.key, 0)} time(s) so far." for option in options}
        labels = {option.key: option.label for option in options}

        def baseline_choice():
            unvisited = [option for option in options if self.journal.get(option.key, 0) == 0]
            return (unvisited[0] if unvisited else options[0]).key

        chosen_key = ask_jev(self.session, state, criteria,
            "Choose the next place to walk to. Prefer destinations you have not visited yet, or that make progress "
            "toward unexplored parts of the world. Avoid repeatedly bouncing between the same two places if you "
            "have already been there recently. If a Heal destination is offered and your party is badly hurt, "
            "prefer it over further exploration. Choose only one provided option.",
            labels, state_field="journey", default_baseline=baseline_choice)
        return next(option for option in options if option.key == chosen_key)

    def battle(self):
        # A wild encounter interrupted the leg. Reuse the battle brain whole, then settle (which
        # also drains a whiteout) before the journey re-plans from wherever the player ended up --
        # never resume the closed navigate_to generator, its path may no longer be valid.
        self.battles += 1
        for _ in range(3000):
            if battle_is_active():
                break
            context.emulator.press_button("A")
            tick()
        else:
            raise RuntimeError("Battle did not start after the encounter transition began.")
        strategy = JevStrategy(self.session)
        generator = handle_battle(strategy)
        for _ in range(150000):
            try:
                next(generator)
            except StopIteration as complete:
                outcome = complete.value.outcome.name
                break
            tick()
        else:
            raise RuntimeError("Battle frame limit reached during journey.")
        self.settle(frame_limit=8000)
        return f"battled:{outcome}"

    def _retrigger_warp(self, destination, coordinates):
        # Some warps drop the player exactly onto one of their own exit tiles (e.g. Oak's Lab's
        # entry point) -- navigate_to() to that same tile is a zero-distance no-op, so the warp
        # never re-fires. Try every direction, actually walking back onto the tile after each,
        # until the map genuinely changes. Returns True once it has, False if none worked.
        origin_map = destination.map
        for direction in ("Up", "Down", "Left", "Right"):
            if get_player_avatar().map_group_and_number != origin_map:
                return True
            start = get_player_avatar().local_coordinates
            for _ in range(40):
                context.emulator.press_button(direction)
                tick()
                if get_player_avatar().local_coordinates != start:
                    break
            else:
                continue
            # local_coordinates updates the instant a step begins, well before the walk animation
            # finishes. Let it fully settle before issuing the next navigate_to(), otherwise it
            # fights the still-in-progress movement.
            for _ in range(60):
                if player_avatar_is_standing_still():
                    break
                tick()
            if get_player_avatar().map_group_and_number != origin_map:
                return True
            generator = navigate_to(destination.map, coordinates)
            try:
                for count, _ in enumerate(generator):
                    tick()
                    if count >= JOURNEY_LEG_FRAME_LIMIT:
                        generator.close()
                        break
            except BotModeError:
                pass
            if get_player_avatar().map_group_and_number != origin_map:
                return True
        # A warp transition can still be completing in the background well after navigate_to()
        # itself is done (observed live: map_group_and_number only flipped ~80+ frames after the
        # walk-back generator finished, well past every check above). Give it one last chance,
        # mashing B the way settle() would, before concluding this destination is truly unusable.
        for _ in range(120):
            if get_player_avatar().map_group_and_number != origin_map:
                return True
            context.emulator.press_button("B")
            tick()
        return get_player_avatar().map_group_and_number != origin_map

    def _travel_to_tile(self, destination, coordinates):
        # One physical approach to a destination. Returns "arrived", a "battled:..." outcome, or
        # None if this particular tile didn't work and another approach (if any) should be tried.
        origin_map = get_player_avatar().map_group_and_number
        try:
            avatar = get_player_avatar()
            if avatar.map_group_and_number == destination.map and avatar.local_coordinates == coordinates:
                return "arrived" if self._retrigger_warp(destination, coordinates) else None
            generator = (heal_in_pokemon_center(destination.center) if destination.kind == "heal"
                else navigate_to(destination.map, coordinates))
            for count, _ in enumerate(generator):
                tick()
                if (get_game_state() in (GameState.BATTLE, GameState.BATTLE_STARTING)
                        or task_is_active("Task_BattleStart")):
                    generator.close()
                    # The generator may have been mid-sprint (held B); an un-released hold carries
                    # into the battle intro and can stall it indefinitely.
                    context.emulator.reset_held_buttons()
                    return self.battle()
                if count >= JOURNEY_LEG_FRAME_LIMIT:
                    generator.close()
                    return None
        except BotModeError:
            return None
        if destination.kind in ("warp", "walk") and get_player_avatar().map_group_and_number == origin_map:
            # A warp/connection destination is only ever offered when it leads to a different map,
            # so ending the leg still on the map we started from means the warp never actually
            # fired (some in-ROM warps just don't retrigger from a local footstep, for reasons this
            # code can't diagnose). Report failure like any other unusable tile rather than a false
            # "arrived" and looping on it forever.
            return None
        return "arrived"

    def travel(self, destination):
        # Some buildings have several physical warp tiles that collide on the same destination key
        # (see candidate_destinations()); a couple of those tiles can turn out not to retrigger the
        # warp at all (direction-sensitive, or just not the "real" trigger tile). Try every known
        # approach before giving up -- one bad tile must not permanently block every tile sharing
        # its key, which would strand the run behind an exit that was never actually broken.
        tiles = (destination.coordinates,) + (destination.alt_coordinates if destination.kind == "warp" else ())
        for coordinates in tiles:
            outcome = self._travel_to_tile(destination, coordinates)
            if outcome is not None:
                return outcome
        self.blocked.add(destination.key)
        return "blocked"

    def take(self, destination):
        self.legs += 1
        outcome = self.travel(destination)
        # Only count it as a real visit if the destination was actually reached -- a wild
        # encounter can interrupt the leg partway and strand the player back on the same route,
        # short of the destination. Counting that as a "visit" inflates the chosen destination's
        # visited count on every failed attempt, which then steers Jev's own "prefer unvisited"
        # instruction straight into avoiding the one place it should keep retrying.
        if outcome == "arrived":
            self.journal[destination.key] = self.journal.get(destination.key, 0) + 1
        self.recent_legs = (self.recent_legs + [{"destination": destination.label, "outcome": outcome}])[-10:]
        self.legs_since_progress += 1
        if outcome == "arrived" or outcome.startswith("battled"):
            here = get_map_data_for_current_position().map_group_and_number
            if here not in self.visited_maps:
                self.legs_since_progress = 0
            self.visited_maps.add(here)
        return outcome
