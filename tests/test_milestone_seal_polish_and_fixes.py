"""Milestone seal integration tests for the "Polish & Fixes" project.

This grab-bag milestone delivered four largely independent items (DM2-15
/dm:debug command, DM2-16 stale-test repair, DM2-17 spell-slot self-heal,
DM2-18 paged terminal HUD). The one genuine cross-ticket seam is
**DM2-17 -> DM2-18**: the model self-heals a caster's ``spell_slots`` into a
``dict[int, int]`` (SRD progression), and the HUD's pure helpers parse and
render exactly that shape. These tests pin that contract at the boundary —
the model produces, the HUD consumes — which is invisible from either side
in isolation.

DM2-15 and DM2-16 have no behavioral seam with the rest and are not covered
here; their per-ticket unit tests live in their own PRs.
"""

import importlib.util
import json
import sys
from pathlib import Path

from dm20_protocol.models import Character, CharacterClass, Race, Spell
from dm20_protocol.srd_spell_slots import slots_for_class

# Load the HUD script the same way tests/test_character_hud.py does: it is a
# self-contained PEP 723 Textual app whose module level is stdlib-only, so its
# pure helpers import without textual installed.
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "character_hud.py"
_spec = importlib.util.spec_from_file_location("character_hud", SCRIPT_PATH)
hud = importlib.util.module_from_spec(_spec)
sys.modules["character_hud"] = hud  # dataclasses resolve fields via sys.modules
_spec.loader.exec_module(hud)


# ---------------------------------------------------------------------------
# Fixtures — mirror the construction helpers in test_character_model_v2.py.
# ---------------------------------------------------------------------------


def _spell(name: str, level: int) -> Spell:
    return Spell(
        name=name,
        level=level,
        school="evocation",
        casting_time="1 action",
        duration="instantaneous",
        components=["V", "S"],
        description=f"A {name} spell.",
    )


def _healed_caster(class_name: str = "Wizard", level: int = 5) -> Character:
    """A caster that knows leveled spells; the model self-heals slots on build."""
    return Character(
        name="Broden Arolio",
        character_class=CharacterClass(name=class_name, level=level, hit_dice="1d6"),
        race=Race(name="Human"),
        spellcasting_ability="intelligence",
        spells_known=[_spell("Magic Missile", 1), _spell("Fireball", 3)],
    )


# ------------------------------------------------------- DM2-17 -> DM2-18 seam


def test_healed_caster_slots_render_in_hud():
    """The HUD parses the dict[int,int] DM2-17's self-heal produces.

    Verifies the combined behavior of DM2-17's slot heal (the model populates
    spell_slots from the SRD progression on construction) and DM2-18's HUD
    parse_spell_slots, which must accept that exact shape.
    """
    char = _healed_caster("Wizard", 5)
    # DM2-17: the validator healed an empty-slot caster from the SRD table.
    assert char.spell_slots == slots_for_class("Wizard", 5) == {1: 4, 2: 3, 3: 2}

    # DM2-18: the HUD parses the healed mapping to a non-empty {int: int}.
    parsed = hud.parse_spell_slots(char.spell_slots)
    assert parsed == {1: 4, 2: 3, 3: 2}
    assert all(isinstance(k, int) and isinstance(v, int) for k, v in parsed.items())


def test_healed_caster_pips_and_headers_render_visible_content():
    """slot_pips / spell_level_header render visible content for a healed caster.

    Combines DM2-17's populated slots with DM2-18's pip/header rendering.
    """
    char = _healed_caster("Cleric", 3)  # full caster: {1: 4, 2: 2}
    parsed = hud.parse_spell_slots(char.spell_slots)
    assert parsed == {1: 4, 2: 2}

    for level, max_slots in parsed.items():
        pips = hud.slot_pips(max_slots, used=1)
        assert pips  # non-empty pip string
        assert "●" in pips and "○" in pips  # one used, the rest available
        header = hud.spell_level_header(level, pips)
        assert pips in header
        assert "no slot data" not in header  # the hint path must NOT fire


def test_hud_tolerates_unhealed_empty_slots():
    """The HUD renders the '[no slot data]' hint for an un-healed caster.

    Pins the cross-ticket contract that DM2-18's HUD tolerates the pre-heal /
    old-save state (a caster with leveled spells but spell_slots == {}) that
    DM2-17 exists to repair. The model heals on construction, so the empty
    state is re-created afterward — exactly how DM2-17's own unit tests do it.
    """
    char = _healed_caster("Sorcerer", 4)
    char.spell_slots = {}  # simulate the pre-DM2-17 broken/old-save state

    parsed = hud.parse_spell_slots(char.spell_slots)
    assert parsed == {}

    # DM2-18 review fixup: the bracketed hint is markup-escaped so Textual does
    # not silently swallow it as a Rich style span for empty-slot casters.
    pips = hud.slot_pips(0, 0)
    assert pips == ""
    header = hud.spell_level_header(1, pips)
    assert "no slot data" in header  # words stay visible to the player
    assert r"\[no slot data]" in header  # bracket escaped, not swallowed


def test_pydantic_json_string_keys_round_trip_through_hud():
    """Pydantic serializes int slot-level keys to JSON strings; the HUD re-parses.

    The structural coupling the seal sweep flagged: DM2-17's model owns the
    canonical int-keyed dict, but persistence/JSON stringifies the keys, and
    DM2-18's standalone HUD reloads from that JSON. parse_spell_slots' int(key)
    is exactly what bridges the two.
    """
    char = _healed_caster("Druid", 9)  # full caster: {1:4,2:3,3:3,4:3,5:1}
    expected = slots_for_class("Druid", 9)
    assert char.spell_slots == expected

    # Round-trip through JSON the way the HUD reads campaign files: int keys
    # become strings on serialization.
    raw = json.loads(char.model_dump_json())["spell_slots"]
    assert all(isinstance(k, str) for k in raw), "JSON must stringify int keys"

    parsed = hud.parse_spell_slots(raw)
    assert parsed == expected  # the HUD's int(key) parse restores the int keys
    assert all(isinstance(k, int) for k in parsed)


def test_warlock_pact_slots_render_in_hud():
    """A pact caster's single-level slot dict survives the heal -> HUD seam.

    Warlocks are the asymmetric caster type in DM2-17's SRD table (one slot
    level, scaling count); confirms DM2-18 renders that shape too.
    """
    char = _healed_caster("Warlock", 11)  # pact: {5: 3} per slots_for_class
    expected = slots_for_class("Warlock", 11)
    assert char.spell_slots == expected
    assert len(expected) == 1  # warlocks have exactly one slot level

    parsed = hud.parse_spell_slots(char.spell_slots)
    assert parsed == expected
    (level, count), = parsed.items()
    pips = hud.slot_pips(count, used=0)
    assert pips == "●" * count  # all available, none spent
    assert hud.spell_level_header(level, pips).count("●") == count
