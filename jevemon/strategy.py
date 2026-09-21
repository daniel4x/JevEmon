"""Battle-turn resolution shared by wild encounters (journey mode's only remaining battle path)."""
import json
from dataclasses import asdict

from modules.battle_action_selection import battle_action_use_move
from modules.battle_menuing import scroll_to_battle_action
from modules.battle_state import (
    battle_is_active,
    get_battle_controller_callback,
    get_battle_state,
    get_main_battle_callback,
)
from modules.battle_strategies import TurnAction
from modules.battle_strategies._util import BattleStrategyUtil
from modules.battle_strategies.default import DefaultBattleStrategy
from modules.context import context
from modules.debug_utilities import debug_create_pokemon
from modules.items import get_item_by_name
from modules.memory import GameState, get_game_state
from modules.menuing import get_current_party_menu_index, scroll_to_party_menu_index
from modules.pokemon import LearnedMove, StatsValues, StatusCondition, get_move_by_name, get_nature_by_name, get_species_by_name
from modules.pokemon_party import get_party

from .decisions import ask_jev


def select_living_party_member(personality):
    for _ in range(1200):
        if get_game_state() == GameState.PARTY_MENU:
            break
        yield
    else:
        raise RuntimeError("Party menu did not open.")
    for _ in range(30):
        yield
    index = next(i for i, p in enumerate(get_party()) if p.personality_value == personality)
    if get_party()[index].current_hp <= 0:
        raise RuntimeError("Refusing to select a fainted Pokémon.")
    yield from scroll_to_party_menu_index(index)
    selected = get_party()[get_current_party_menu_index()]
    if selected.personality_value != personality or selected.current_hp <= 0:
        raise RuntimeError("Party cursor does not match the chosen living Pokémon.")
    for _ in range(600):
        if get_game_state() != GameState.PARTY_MENU:
            return
        context.emulator.press_button("A")
        yield
    raise RuntimeError("Could not confirm selected Pokémon.")


def handle_action_selection(strategy):
    while battle_is_active() and get_main_battle_callback() in ("HandleTurnActionSelectionState", "sub_8012324"):
        callback = get_battle_controller_callback(0)
        if callback not in ("HandleInputChooseAction", "sub_802C098", "bx_battle_menu_t6_2"):
            if callback in ("HandleInputChooseMove", "HandleAction_ChooseMove"):
                context.emulator.press_button("B")
            yield
            continue
        battle = get_battle_state()
        action, index = strategy.decide_turn(battle)
        if action == TurnAction.UseMove:
            yield from battle_action_use_move(action, 0, index, battle)
        elif action == TurnAction.RotateLead:
            target = get_party()[index]
            if target.current_hp <= 0:
                raise RuntimeError("Refusing to switch to a fainted Pokémon.")
            personality = target.personality_value
            yield from scroll_to_battle_action(2)
            context.emulator.press_button("A")
            yield
            yield from select_living_party_member(personality)
        else:
            raise RuntimeError("Unsupported battle action.")


def handle_fainted_replacement(strategy):
    battle = get_battle_state()
    chosen = strategy.choose_new_lead_after_faint(battle)
    if get_party()[chosen].current_hp <= 0:
        raise RuntimeError("Refusing a fainted Pokémon before opening replacement menu.")
    personality = get_party()[chosen].personality_value
    for _ in range(1200):
        if get_game_state() == GameState.PARTY_MENU:
            break
        if not battle_is_active():
            return
        context.emulator.press_button("A")
        yield
    else:
        raise RuntimeError("Replacement menu did not open.")
    for _ in range(30):
        yield
    # FireRed reorders its party on menu entry. Identify the selected Pokémon,
    # rather than applying the adapter's stale battle-slot mapping.
    index = next(i for i, p in enumerate(get_party()) if p.personality_value == personality)
    if get_party()[index].current_hp <= 0:
        raise RuntimeError("Refusing to select a fainted replacement.")
    yield from scroll_to_party_menu_index(index)
    selected = get_party()[get_current_party_menu_index()]
    if selected.personality_value != personality or selected.current_hp <= 0:
        raise RuntimeError("Replacement cursor does not match the chosen living Pokémon.")
    for _ in range(600):
        if get_game_state() != GameState.PARTY_MENU:
            strategy.pending_switch = personality
            return
        context.emulator.press_button("A")
        yield
    raise RuntimeError("Could not confirm replacement Pokémon.")


def make_party(entries):
    result = []
    for entry in entries:
        species = get_species_by_name(entry["species"])
        result.append(debug_create_pokemon(species, entry["level"],
            moves=[LearnedMove.create(get_move_by_name(move)) for move in entry["moves"]],
            nature=get_nature_by_name(entry.get("nature", "Hardy")),
            held_item=get_item_by_name(entry["item"]) if entry.get("item") else None,
            has_second_ability=len(species.abilities) > 1 and entry.get("ability") == species.abilities[1].name,
            ivs=StatsValues(**{key: entry.get("ivs", {}).get(key, 31) for key in StatsValues.__dataclass_fields__}),
            evs=StatsValues(**{key: entry.get("evs", {}).get(key, 0) for key in StatsValues.__dataclass_fields__}),
            current_hp=entry.get("current_hp"), status_condition=StatusCondition[entry.get("status", "Healthy")]))
    return result


def describe(pokemon):
    in_battle = hasattr(pokemon, "status_permanent")
    result = {"species": pokemon.species.name, "level": pokemon.level,
        "hp": pokemon.current_hp, "max_hp": pokemon.total_hp,
        "types": [t.name for t in (pokemon.types if in_battle else pokemon.species.types)],
        "ability": pokemon.ability.name, "item": pokemon.held_item.name if pokemon.held_item else None,
        "status": (pokemon.status_permanent if in_battle else pokemon.status_condition).name,
        "stats": asdict(pokemon.stats),
        "moves": [{"name": m.move.name, "pp": m.pp, "type": m.move.type.name,
            "category": m.move.type.kind if m.move.base_power else "Status", "power": m.move.base_power,
            "accuracy": m.move.accuracy, "effect": m.move.effect} for m in pokemon.moves if m is not None]}
    if in_battle:
        result["boosts"] = asdict(pokemon.stats_modifiers)
        result["temporary_status"] = [s.name for s in pokemon.status_temporary]
    return result


def describe_party_hp(party):
    return [{"species": p.species.name, "level": p.level, "hp": p.current_hp, "max_hp": p.total_hp,
        "status": p.status_condition.name} for p in party]


class JevStrategy(DefaultBattleStrategy):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.last_signature = None
        self.last_action = None
        self.history = []
        self.pending_switch = None

    def which_move_should_be_replaced(self, pokemon, move):
        return 4

    def should_allow_evolution(self, pokemon, party_index):
        return False

    def choose(self, battle, forced=False):
        session = self.session
        if session.status["decisions"] >= session.config["max_decisions"]:
            raise RuntimeError("Decision limit reached. Battle stopped.")
        active = battle.own_side.active_battler
        if not forced and self.pending_switch is not None:
            if active.personality_value != self.pending_switch:
                raise RuntimeError("The emulator sent out a different Pokémon than Jev selected.")
            session.status.setdefault("switches_verified", 0)
            session.status["switches_verified"] += 1
            self.pending_switch = None
        signature = (battle.current_turn, active.species.name, active.current_hp, forced,
            tuple(m.pp for m in active.moves if m), battle.opponent.active_battler.species.name,
            battle.opponent.active_battler.current_hp)
        if signature == self.last_signature:
            session.metrics["reused_decisions"] += 1
            return self.last_action
        choices = {}
        actions = {}
        labels = {}
        move_context = []
        if not forced:
            for index, move in enumerate(active.moves):
                if move and active.can_use_move(move.move) and not (active.taunt_turns_remaining and move.move.base_power == 0):
                    key = f"move_{index + 1}"
                    choices[key] = json.dumps(describe(active)["moves"][index])
                    actions[key] = TurnAction.use_move(index)
                    labels[key] = move.move.name
                    estimate = BattleStrategyUtil(battle).calculate_move_damage_range(move.move, active, battle.opponent.active_battler)
                    move_context.append({"move": move.move.name, "estimated_damage": [estimate.min, estimate.max],
                        "note": "Approximate mechanics calculation, not a model score or guaranteed outcome."})
        if forced or BattleStrategyUtil(battle).can_switch():
            for index, pokemon in enumerate(get_party()):
                if pokemon.current_hp > 0 and not pokemon.is_egg and (forced or index != active.party_index):
                    key = f"switch_{index + 1}"
                    choices[key] = "Switch to " + json.dumps(describe(pokemon))
                    actions[key] = TurnAction.rotate_lead(index)
                    labels[key] = f"Switch to {pokemon.species.name}"
        if not actions:
            # With no usable moves, selecting Fight lets the ROM use Struggle.
            choices["struggle"] = "No usable moves remain. Fight using Struggle."
            actions["struggle"] = TurnAction.use_move(0)
            labels["struggle"] = "Struggle"
        state = {"game": "Pokemon FireRed USA v1.1, actual GBA ROM", "turn": battle.current_turn,
            "information": "Privileged RAM state, exact opponent active HP and moves. No images sent to Jev.",
            "forced_switch": forced, "weather": battle.weather.name,
            "you": describe(active), "opponent": describe(battle.opponent.active_battler),
            "party": [describe(p) for p in get_party()], "move_matchups": move_context,
            "recent_actions": self.history[-6:]}

        def baseline_choice():
            if forced:
                return next(iter(actions))
            move_index = BattleStrategyUtil(battle).get_strongest_move_against(active, battle.opponent.active_battler)
            key = f"move_{move_index + 1}" if move_index is not None else ""
            return key if key in actions else next(iter(actions))

        action = ask_jev(session, state, choices,
            "Choose the best legal action to win this Gen 3 Pokemon battle. Consider type immunity, abilities, damage, HP, speed, PP and status. Prefer reliable knockouts and avoid unnecessary repeated switches. Physical/special categories follow move type. Choose only one provided option.",
            labels, state_field="battle", default_baseline=baseline_choice,
            event_extra={"turn": battle.current_turn}, message_prefix=f"{active.species.name} chose ")
        self.last_signature = signature
        self.last_action = actions[action]
        self.history.append({"turn": battle.current_turn, "pokemon": active.species.name, "action": labels[action]})
        if not forced and action.startswith("switch_"):
            self.pending_switch = get_party()[actions[action][1]].personality_value
        return actions[action]

    def decide_turn(self, battle_state):
        return self.choose(battle_state)

    def choose_new_lead_after_faint(self, battle_state):
        return self.choose(battle_state, forced=True)[1]
