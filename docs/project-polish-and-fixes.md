# Polish & Fixes
**Linear:** https://linear.app/dm21/project/polish-and-fixes-5573e5cf93b9/overview
**Sealed:** 2026-06-14
**Run ID:** wfl-2026-06-12-135116

## What this milestone delivered
This is a catch-all "Polish & Fixes" milestone — small bugs, polish, and
quality-of-life work that doesn't belong to a feature project — not a cohesive
feature. It delivered four largely independent items: a `/dm:debug` slash
command for filing Linear issues mid-story (DM2-15), a repair of two stale
adventure-parser cache tests (DM2-16), a spellcaster spell-slot self-heal
(DM2-17), and a paged terminal character HUD (DM2-18). Three of the four are
standalone; the only genuine cross-ticket integration is DM2-17 -> DM2-18,
where the HUD renders the spell slots the model self-heals.

## Tickets included
| Ticket | What it did | PR |
|---|---|---|
| DM2-15 | `/dm:debug` slash command to file Linear issues mid-story | [#13](https://github.com/TandersT/dm21/pull/13) |
| DM2-16 | Fixed 2 stale adventure-parser cache tests (lowercase normalization) | [#14](https://github.com/TandersT/dm21/pull/14) |
| DM2-17 | Spellcaster spell-slot self-heal (validator + SRD table + inline repair) | [#15](https://github.com/TandersT/dm21/pull/15) |
| DM2-18 | Paged terminal character HUD (Textual TUI) | [#16](https://github.com/TandersT/dm21/pull/16) |

## How the pieces fit together
Honestly: most of these tickets do not connect.

- **DM2-15** (`.claude/commands/dm/debug.md`) is a self-contained markdown slash
  command. It touches no Python surface and shares no behavior with the others.
- **DM2-16** (`tests/test_adventure_parser.py`) is a test-only repair of stale
  assertions. It is isolated to the adventure-parser cache path.
- **DM2-17 -> DM2-18 is the one real seam.** DM2-17's
  `Character.heal_missing_spell_slots()` (backed by
  `src/dm20_protocol/srd_spell_slots.py`'s `slots_for_class`) populates a
  caster's `spell_slots` as a `dict[int, int]` from the SRD progression.
  DM2-18's HUD (`scripts/character_hud.py`) consumes that exact shape through
  its pure helpers `parse_spell_slots`, `slot_pips`, and `spell_level_header`.
  The model produces, the HUD consumes — and because the HUD is a stdlib-only
  standalone script that can't import `src`, it re-implements its own slot
  parsing, so the contract is only enforced at the boundary. Pydantic also
  stringifies the int slot-level keys on serialization, and the HUD reads
  campaign JSON, so `parse_spell_slots`' `int(key)` is what bridges the two.
  These cross-ticket invariants are pinned in
  `tests/test_milestone_seal_polish_and_fixes.py`.

## Key design decisions
Each pinned in design review by the user, not chosen silently.

- **DM2-15 (three picks).** Command named `/dm:debug` (matches the ticket title
  and the user's own phrasing). **Inline synchronous filing** over a background
  subagent — a 3-call MCP flow is fast enough not to derail the scene, and a
  silent background failure losing a bug report is worse than a brief pause.
  **Team Dm21 only, no project** — keeps the committed command file evergreen
  rather than baking in a stale triage-batch project reference.
- **DM2-16.** Fix the tests only, switching both to the normalized `lmop.json`
  cache path; no product change. The lowercase normalization (716024e) is
  intentional and matches 5etools URL conventions, so the tests were the stale
  side. The two repaired tests themselves pin the write-side and read-side
  normalization contracts.
- **DM2-17.** Model-level self-heal via a pydantic validator on `Character`
  (the one choke point every load/creation path flows through, following the
  existing `_migrate_character_class` precedent), backed by a built-in SRD slot
  table (`srd_spell_slots.py`) as a fallback when no rulebook is loaded —
  rulebook data still wins when present. The builder gained the same SRD
  fallback so new casters never start broken. **Notably, for Q3 the user chose
  inline auto-repair-and-consume inside `use_spell_slot` over the *recommended*
  diagnose-only error** — an explicit non-recommended pick, made transparent via
  a repair note in the tool output.
- **DM2-18.** Self-contained Textual TUI with a PEP 723 inline `textual` dep,
  reading campaign JSON directly and polling file mtimes (~1s) rather than
  using watchdog/inotify; four pages (Vitals/Spells/Features/Inventory). The
  module level is stdlib-only so the pure helpers are unit-testable without
  textual. A review fixup escaped the `[no slot data]` Spells-header literal
  (it was being silently swallowed as Rich markup for exactly the empty-slot
  casters DM2-17 repairs).

## What's NOT in this milestone
- **The `level_up_engine` sibling bug.** `level_up_engine._update_spell_slots`
  returns early when a rulebook class def lacks slot data, leaving `spell_slots`
  stale vs. level — the same empty-slots bug family as DM2-17 but outside its
  ACs. Flagged for a follow-up ticket.
- **The 5 info-severity sweep findings** (left unapplied): `render_spell_detail`
  `range` field interpolated without `markup_escape`; HUD `_render_all`
  re-renders all four pages on every poll; redundant filesystem stats
  (double-stat between `_poll` and `load_snapshot`); and full campaign
  re-discovery on every idle poll while no campaign is resolved.
- **The pre-existing whole-suite test pollution.** Running the full pytest suite
  on `main` yields ~143 failures that vanish in isolation (cross-test pollution
  in `test_validators` / `test_rulebook_manager` / `test_rulebook_sources` /
  `test_mcp_query_tools`); it predates this batch and warrants its own ticket.
- **Any shared behavior across DM2-15 / DM2-16 and the rest** — there is none;
  see "How the pieces fit together".

## Tests
- `tests/test_milestone_seal_polish_and_fixes.py` — pins the DM2-17 -> DM2-18
  seam: a self-healed caster's `dict[int, int]` slots parse and render through
  the HUD's pure helpers, the un-healed empty-slot state renders the escaped
  `[no slot data]` hint, and pydantic's JSON string keys round-trip back through
  `parse_spell_slots`' `int(key)`.

Per-ticket unit tests live in each ticket's PR (see table).
