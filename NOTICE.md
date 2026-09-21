# Third-party notices

This project drives a real Pokémon FireRed ROM through the following third-party software. None of it is bundled or redistributed here — `scripts/setup.py` fetches each of these into a local, gitignored `vendor/`/`.venv` at setup time.

- **[PokéBot Gen3](https://github.com/40Cakes/pokebot-gen3)** — provides the RAM/menu adapter (pathfinding, battle menuing, map and species data) this project builds on. Licensed under the GNU General Public License v3.0. `scripts/setup.py` clones it and pins revision `5dd898f830775d448b06db6f5cd65b930540f146`.
- **[libmgba-py](https://github.com/hanzi/libmgba-py)** — Python bindings for mGBA, used by PokéBot Gen3.
- **[mGBA](https://mgba.io/)** — the Game Boy Advance emulator core.

This project's own code is licensed under the GNU General Public License v3.0 — see [LICENSE](LICENSE).
