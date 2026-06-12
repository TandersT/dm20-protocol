# DM2-18 — Paged terminal character HUD for play sessions

**Ticket:** [DM2-18](https://linear.app/dm21/issue/DM2-18/add-a-paged-terminal-character-hud-for-play-sessions)
**Size:** Big
**Base:** sta/dm2-17-spellcaster-spell-slots

## Problem

During play (Claude Code story session + dm20 MCP), the hero's state — HP, spell
slots, known spells, features, inventory — is buried in campaign JSON and chat
scrollback. There is no glanceable view of the character while the story runs.

## Decisions (approved design, 2026-06-11 — pinned in the ticket)

- **Stack:** self-contained Textual TUI at `scripts/character_hud.py`, PEP 723
  inline deps (`textual`) so `uv run` resolves them — no change to dm20's
  `pyproject.toml`.
- **Data:** reads `characters.json`, `game_state.json`, `quests.json` from the
  campaign dir. Storage dir resolution: `--storage-dir` flag → `DM20_STORAGE_DIR`
  env → `main/data` default (mirrors `.mcp.json`). Campaign: `--campaign` flag,
  else most recently modified campaign dir. Hero: single character auto-selected;
  `c` cycles when there are several (shared-world-gm multi-hero).
- **Refresh:** poll file mtimes every 1 s (dm20 writes atomically via temp +
  rename — `SplitStorageBackend._atomic_write` in `storage.py` — so mtime polling
  is reliable; simpler than watchdog).
- **Pages:**
  1. *Vitals* — name/class/level/race/background, HP bar (green→yellow→red) +
     temp HP, AC/speed/prof/inspiration, ability scores with modifiers,
     spell-slot pips, conditions/concentration/death saves when relevant, plus
     location, in-world date, gold, and active quests from game state.
  2. *Spells* — `spells_known` grouped by level with slot pips; select a spell
     for its full description.
  3. *Features & Feats* — `features` grouped by source, expandable descriptions.
  4. *Inventory* — `equipment` slots, then `inventory` with qty/weight.
- **tmux:** no automation; the docstring shows the one-liner to run it in a
  second window.
- **Testing:** unit tests for pure helpers (ability modifiers, HP fraction,
  campaign/hero resolution, tolerant loading) under the existing pytest setup;
  visual behavior verified by running it.

### Alternatives (resolved by the approved design block)

- watchdog/inotify file watching — rejected for 1 s mtime polling (atomic
  temp+rename writes make polling reliable and simpler).
- Adding `textual` to project deps — rejected for PEP 723 inline metadata.
- tmux pane automation — rejected; docstring one-liner only.

## Implementation notes (consequences of the pinned decisions, not new forks)

- **Raw JSON, not dm20 models.** The HUD reads the campaign JSON directly so it
  stays self-contained (no `PYTHONPATH` / project import requirement) and stays
  up even when a file is mid-write or partially invalid. Consequence: it must
  tolerate `spell_slots: {}` on casters — older saves predate the DM2-17
  self-heal and only repair on the next campaign load/save.
- **Import-safe helpers.** The pinned testing strategy (pytest on pure helpers)
  plus PEP 723 (textual is NOT in the project env) forces the module layout:
  stdlib-only at module level (helpers, data loading), `textual` imported inside
  the app-factory function that `main()` calls. Tests import the script by path
  and never touch the UI.
- **`main/data` default:** resolved as `<script repo root>/data`
  (`Path(__file__).resolve().parents[1] / "data"`). Running from the `main`
  clone — the documented usage — this is exactly the `.mcp.json` value, without
  hardcoding a user-specific absolute path.
- **Campaign dir detection:** a directory under `<storage>/campaigns/` counts as
  a campaign only if it contains `characters.json` or `campaign.json`
  (`data/campaigns/` also holds non-campaign dirs, e.g. claudmaster session
  artifacts). "Most recently modified" = max mtime across the three tracked
  JSON files (fallback: dir mtime).
- **Tolerant loading:** each of the three files loads independently; missing
  file or `json.JSONDecodeError` (file caught mid-write) yields the last good
  copy of that section, never a crash. Before any good data exists, a
  placeholder page renders; recovery happens on a later poll.
- **Slot pips:** filled `●` = available, hollow `○` = used; per spell level,
  derived from `spell_slots` (max) minus `spell_slots_used`. JSON object keys
  are strings — parse to int.
- **Combat banner:** when `game_state.in_combat`, a banner above the page
  content (every page) lists `initiative_order` (`{name, initiative}` dicts,
  already sorted by the server) and highlights `current_turn`.
- **Quests on Vitals:** titles of `quests.json` entries with
  `status == "active"`; fall back to `game_state.active_quests` when
  `quests.json` is unavailable.

## Acceptance criteria (from the ticket)

- [ ] `uv run scripts/character_hud.py` in a tmux window shows the active
      campaign's hero without any arguments (flags exist for storage dir /
      campaign / character)
- [ ] Four pages switchable with `1`–`4` and `←`/`→`: Vitals, Spells,
      Features & Feats, Inventory
- [ ] An HP change saved by the story session appears in the HUD within ~1 s,
      without restarting it
- [ ] When `game_state.in_combat` is true, every page shows a banner with
      initiative order and current turn
- [ ] Missing campaign files or JSON caught mid-write never crash the HUD
      (placeholder / last good render, recovers on next poll)
