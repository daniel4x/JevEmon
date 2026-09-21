"""Configuration validation for a journey run."""
from modules.items import get_item_by_name
from modules.pokemon import get_species_by_name, get_move_by_name, get_nature_by_name, StatusCondition


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Configuration must be an object.")
    if config.get("game") != "firered-v1.1":
        raise ValueError("This ROM adapter supports FireRed USA v1.1 only.")
    if config.get("agent") not in ("jev", "baseline"):
        raise ValueError("Agent must be jev or baseline.")
    if config.get("speed") not in (1, 2, 4, 0):
        raise ValueError("Speed must be 1, 2, 4, or 0 for unthrottled testing.")
    if type(config.get("max_decisions")) is not int or not 1 <= config["max_decisions"] <= 1000:
        raise ValueError("Decision limit must be between 1 and 1000.")
    if type(config.get("max_legs")) is not int or not 1 <= config["max_legs"] <= 2000:
        raise ValueError("max_legs must be between 1 and 2000.")
    if type(config.get("stuck_limit")) is not int or not 1 <= config["stuck_limit"] <= 100:
        raise ValueError("stuck_limit must be between 1 and 100.")
    party = config.get("party")
    if not isinstance(party, list) or not 1 <= len(party) <= 6:
        raise ValueError("party must contain 1 to 6 Pokémon.")
    alive = False
    for pokemon in party:
        species = get_species_by_name(pokemon["species"])
        if species.national_dex_number < 1 or species.national_dex_number > 386:
            raise ValueError("Choose a Generation 1-3 species.")
        if type(pokemon.get("level")) is not int or not 1 <= pokemon["level"] <= 100:
            raise ValueError("Levels must be integers from 1 to 100.")
        moves = pokemon.get("moves")
        if not isinstance(moves, list) or not 1 <= len(moves) <= 4 or len(set(moves)) != len(moves):
            raise ValueError("Each Pokémon needs 1 to 4 distinct moves.")
        for move in moves:
            get_move_by_name(move)
        get_nature_by_name(pokemon.get("nature", "Hardy"))
        if pokemon.get("item"):
            get_item_by_name(pokemon["item"])
        if pokemon.get("ability") and pokemon["ability"] not in [a.name for a in species.abilities]:
            raise ValueError(f"Invalid ability for {species.name}.")
        for stat_set, default, cap in (("ivs", 31, 31), ("evs", 0, 255)):
            stats = pokemon.get(stat_set, {})
            for stat in ("hp", "attack", "defence", "speed", "special_attack", "special_defence"):
                value = stats.get(stat, default)
                if type(value) is not int or not 0 <= value <= cap:
                    raise ValueError(f"Invalid {stat_set} {stat}.")
            if stat_set == "evs" and sum(stats.values()) > 510:
                raise ValueError("Total EVs must be at most 510.")
        hp = pokemon.get("current_hp")
        if hp is not None and (type(hp) is not int or hp < 0):
            raise ValueError("current_hp must be a nonnegative integer.")
        StatusCondition[pokemon.get("status", "Healthy")]
        alive |= hp is None or hp > 0
    if not alive:
        raise ValueError("party needs at least one Pokémon with HP.")
    return config
