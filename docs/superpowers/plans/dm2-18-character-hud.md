# DM2-18 — Implementation plan

**Spec:** `docs/superpowers/specs/dm2-18-character-hud.md`

TDD for the pure helpers (red test first, minimal implementation). The Textual
UI layer is verified by running it (pinned testing strategy), not unit-tested.

## Task 1 — Pure helper layer (TDD)

New file: `scripts/character_hud.py` (module-level, stdlib-only section)
New tests: `tests/test_character_hud.py` (loads the script via importlib by path)

Helpers, each with red tests first:

- `ability_modifier(score) -> int` and `format_modifier(mod) -> str`
  (8 → -1 → "-1", 16 → +3 → "+3", 10 → "+0").
- `hp_fraction(current, maximum) -> float` — clamped to [0, 1], safe on
  `maximum <= 0`.
- `hp_color(fraction) -> str` — green > 0.5 ≥ yellow > 0.25 ≥ red.
- `parse_spell_slots(raw) -> dict[int, int]` — string JSON keys → int, junk
  entries dropped.
- `slot_pips(max_slots, used) -> str` — `●` available / `○` used, clamped.
- `resolve_storage_dir(flag) -> Path` — flag → `DM20_STORAGE_DIR` env →
  `<script repo root>/data`.
- `find_campaign_dirs(storage_dir) -> list[Path]` — subdirs of
  `<storage>/campaigns/` holding `characters.json` or `campaign.json`.
- `campaign_mtime(campaign_dir) -> float` — max mtime of tracked files.
- `resolve_campaign_dir(storage_dir, name) -> Path | None` — by name, else
  most recently modified; None when nothing qualifies.
- `load_json_tolerant(path) -> dict | None` — None on missing file or invalid
  JSON (mid-write), never raises.
- `resolve_hero(characters, requested) -> str | None` — requested name
  (case-insensitive) when present, else first sorted key; None on empty.
- `cycle_hero(characters, current) -> str | None` — next sorted key, wraps.
- `load_snapshot(campaign_dir, last) -> Snapshot` — per-file tolerant load,
  keeping `last`'s section when a file fails; carries the mtime tuple used by
  the 1 s poll.

## Task 2 — Textual UI (run-verified, not unit-tested)

Same file, inside `build_app()` (textual imported there, keeping the module
import-safe for pytest):

- PEP 723 header (`dependencies = ["textual"]`, `requires-python = ">=3.12"`);
  module docstring with the tmux one-liner.
- `main()` — argparse (`--storage-dir`, `--campaign`, `--character`), resolves
  storage/campaign before importing textual; `--help` therefore needs no tty.
- App: header line (hero · campaign · page tabs), combat banner (hidden unless
  `in_combat`), `ContentSwitcher` with the four pages, footer with key hints.
- Bindings: `1`–`4` direct page, `left`/`right` cycle pages, `c` cycle hero,
  `q` quit.
- `set_interval(1.0, poll)` — compare snapshot mtime tuple, rebuild widgets
  only on change; placeholder Static until first good data.
- Pages:
  1. Vitals — identity line, HP bar (Rich markup, `hp_color`) + temp HP,
     AC/speed/prof/inspiration, abilities grid with modifiers, slot pips,
     conditions/concentration/death saves only when relevant, location /
     in-game date / `party_funds` / active quests.
  2. Spells — `OptionList` grouped by level (slot pips in group headers),
     highlight → full description pane.
  3. Features & Feats — `Collapsible` per feature, grouped under source
     headings.
  4. Inventory — equipment slot table, then inventory table (qty/weight/value).

## Task 3 — Visual verification + docs touch

- `uv run --script scripts/character_hud.py --help` (no tty needed) proves
  PEP 723 resolution.
- Run against `main/data` (Curse of Strahd) in a real terminal: pages, combat
  banner (toggle `in_combat` in a scratch copy), HP edit appearing within ~1 s,
  truncated-JSON tolerance. Recorded as a manual checklist in the PR body.
- Add a short section for the HUD to `scripts/README.md`.

## Verification

1. `uv run pytest tests/test_character_hud.py`
2. `uv run --script scripts/character_hud.py --help`
3. Manual visual checklist (Task 3) — noted in the PR as run-verified.
