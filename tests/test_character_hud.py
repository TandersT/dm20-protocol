"""Unit tests for the pure helpers in scripts/character_hud.py.

The HUD script is a self-contained PEP 723 Textual app; its module level is
stdlib-only so the helpers can be imported and tested without textual
installed. The UI layer is run-verified, not unit-tested.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "character_hud.py"

_spec = importlib.util.spec_from_file_location("character_hud", SCRIPT_PATH)
hud = importlib.util.module_from_spec(_spec)
sys.modules["character_hud"] = hud  # dataclasses resolve fields via sys.modules
_spec.loader.exec_module(hud)


# ---------------------------------------------------------------- formatting


@pytest.mark.parametrize(
    "text,expected",
    [
        ("plain", "plain"),
        ("Fire [red]ball[/]", r"Fire \[red]ball[/]"),
        ("Acid Splash [boom", r"Acid Splash \[boom"),
        ("stray [/] tag", r"stray \[/] tag"),
        ("", ""),
    ],
)
def test_markup_escape_neutralizes_open_brackets(text, expected):
    # Escaping '[' alone prevents any stray ']' or '[/]' from opening a tag,
    # which is what keeps campaign-controlled names from crashing the HUD.
    assert hud.markup_escape(text) == expected


@pytest.mark.parametrize(
    "score,expected",
    [(1, -5), (8, -1), (10, 0), (11, 0), (16, 3), (20, 5), (30, 10)],
)
def test_ability_modifier(score, expected):
    assert hud.ability_modifier(score) == expected


@pytest.mark.parametrize("mod,expected", [(3, "+3"), (0, "+0"), (-1, "-1")])
def test_format_modifier_is_signed(mod, expected):
    assert hud.format_modifier(mod) == expected


@pytest.mark.parametrize(
    "current,maximum,expected",
    [
        (8, 8, 1.0),
        (4, 8, 0.5),
        (0, 8, 0.0),
        (-2, 8, 0.0),  # clamped low
        (10, 8, 1.0),  # clamped high
        (5, 0, 0.0),  # zero max never divides
    ],
)
def test_hp_fraction(current, maximum, expected):
    assert hud.hp_fraction(current, maximum) == expected


@pytest.mark.parametrize(
    "fraction,expected",
    [
        (1.0, "green"),
        (0.51, "green"),
        (0.5, "yellow"),
        (0.26, "yellow"),
        (0.25, "red"),
        (0.0, "red"),
    ],
)
def test_hp_color_thresholds(fraction, expected):
    assert hud.hp_color(fraction) == expected


# --------------------------------------------------------------- spell slots


def test_parse_spell_slots_converts_string_json_keys():
    assert hud.parse_spell_slots({"1": 4, "2": 2}) == {1: 4, 2: 2}


def test_parse_spell_slots_accepts_int_keys():
    assert hud.parse_spell_slots({1: 4}) == {1: 4}


def test_parse_spell_slots_drops_junk_entries():
    assert hud.parse_spell_slots({"x": 2, "1": "lots", "2": 3}) == {2: 3}


def test_parse_spell_slots_tolerates_none_and_empty():
    assert hud.parse_spell_slots(None) == {}
    assert hud.parse_spell_slots({}) == {}


def test_slot_pips_filled_are_available():
    assert hud.slot_pips(3, 1) == "●●○"
    assert hud.slot_pips(2, 0) == "●●"


def test_slot_pips_clamps_overspent_and_zero():
    assert hud.slot_pips(2, 5) == "○○"
    assert hud.slot_pips(0, 0) == ""


# ---------------------------------------------------------------- resolution


def test_resolve_storage_dir_flag_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("DM20_STORAGE_DIR", "/somewhere/else")
    assert hud.resolve_storage_dir(str(tmp_path)) == tmp_path.resolve()


def test_resolve_storage_dir_env_when_no_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("DM20_STORAGE_DIR", str(tmp_path))
    assert hud.resolve_storage_dir(None) == tmp_path.resolve()


def test_resolve_storage_dir_defaults_to_repo_data(monkeypatch):
    monkeypatch.delenv("DM20_STORAGE_DIR", raising=False)
    expected = SCRIPT_PATH.resolve().parents[1] / "data"
    assert hud.resolve_storage_dir(None) == expected


def _make_campaign(storage: Path, name: str, files=("characters.json",)) -> Path:
    cdir = storage / "campaigns" / name
    cdir.mkdir(parents=True)
    for fname in files:
        (cdir / fname).write_text("{}")
    return cdir


def test_find_campaign_dirs_requires_marker_files(tmp_path):
    a = _make_campaign(tmp_path, "Alpha", ("characters.json",))
    b = _make_campaign(tmp_path, "Beta", ("campaign.json",))
    junk = tmp_path / "campaigns" / "junk"
    junk.mkdir()
    (junk / "fact_database.json").write_text("{}")
    (tmp_path / "campaigns" / "stray.txt").write_text("not a dir")

    assert hud.find_campaign_dirs(tmp_path) == [a, b]


def test_find_campaign_dirs_missing_storage_is_empty(tmp_path):
    assert hud.find_campaign_dirs(tmp_path / "nope") == []


def test_resolve_campaign_dir_by_name(tmp_path):
    _make_campaign(tmp_path, "Alpha")
    b = _make_campaign(tmp_path, "Beta")
    assert hud.resolve_campaign_dir(tmp_path, "Beta") == b


def test_resolve_campaign_dir_unknown_name_is_none(tmp_path):
    _make_campaign(tmp_path, "Alpha")
    assert hud.resolve_campaign_dir(tmp_path, "Gamma") is None


def test_resolve_campaign_dir_picks_most_recently_modified(tmp_path):
    a = _make_campaign(tmp_path, "Alpha")
    b = _make_campaign(tmp_path, "Beta")
    os.utime(a / "characters.json", (1000, 1000))
    os.utime(a, (1000, 1000))
    os.utime(b / "characters.json", (2000, 2000))
    os.utime(b, (2000, 2000))

    assert hud.resolve_campaign_dir(tmp_path, None) == b


def test_resolve_campaign_dir_no_campaigns_is_none(tmp_path):
    (tmp_path / "campaigns").mkdir()
    assert hud.resolve_campaign_dir(tmp_path, None) is None


# ----------------------------------------------------------- tolerant loading


def test_load_json_tolerant_reads_valid_dict(tmp_path):
    p = tmp_path / "ok.json"
    p.write_text(json.dumps({"a": 1}))
    assert hud.load_json_tolerant(p) == {"a": 1}


def test_load_json_tolerant_missing_file_is_none(tmp_path):
    assert hud.load_json_tolerant(tmp_path / "absent.json") is None


def test_load_json_tolerant_truncated_json_is_none(tmp_path):
    p = tmp_path / "midwrite.json"
    p.write_text('{"name": "Brod')  # caught mid-write
    assert hud.load_json_tolerant(p) is None


def test_load_json_tolerant_non_dict_is_none(tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[1, 2]")
    assert hud.load_json_tolerant(p) is None


# ------------------------------------------------------------ hero resolution


def test_resolve_hero_single_character_auto_selected():
    assert hud.resolve_hero({"Broden": {}}, None) == "Broden"


def test_resolve_hero_multiple_picks_first_sorted():
    assert hud.resolve_hero({"Zed": {}, "Anna": {}}, None) == "Anna"


def test_resolve_hero_requested_name_case_insensitive():
    assert hud.resolve_hero({"Broden Arolio": {}}, "broden arolio") == "Broden Arolio"


def test_resolve_hero_requested_missing_is_none():
    assert hud.resolve_hero({"Broden": {}}, "Strahd") is None


def test_resolve_hero_empty_is_none():
    assert hud.resolve_hero({}, None) is None


def test_cycle_hero_wraps_sorted_order():
    chars = {"Anna": {}, "Mira": {}, "Zed": {}}
    assert hud.cycle_hero(chars, "Anna") == "Mira"
    assert hud.cycle_hero(chars, "Zed") == "Anna"


def test_cycle_hero_unknown_current_falls_back_to_first():
    assert hud.cycle_hero({"Anna": {}, "Zed": {}}, None) == "Anna"
    assert hud.cycle_hero({}, "Anna") is None


# ------------------------------------------------------------------ snapshots


def _write_campaign_files(cdir: Path, characters=None, game_state=None, quests=None):
    if characters is not None:
        (cdir / "characters.json").write_text(json.dumps(characters))
    if game_state is not None:
        (cdir / "game_state.json").write_text(json.dumps(game_state))
    if quests is not None:
        (cdir / "quests.json").write_text(json.dumps(quests))


def test_load_snapshot_reads_all_sections(tmp_path):
    cdir = _make_campaign(tmp_path, "Alpha")
    _write_campaign_files(
        cdir,
        characters={"Broden": {"name": "Broden"}},
        game_state={"in_combat": False},
        quests={"Q1": {"title": "Q1", "status": "active"}},
    )

    snap = hud.load_snapshot(cdir, None)

    assert snap.characters == {"Broden": {"name": "Broden"}}
    assert snap.game_state == {"in_combat": False}
    assert snap.quests == {"Q1": {"title": "Q1", "status": "active"}}


def test_load_snapshot_missing_file_section_is_none(tmp_path):
    cdir = _make_campaign(tmp_path, "Alpha")
    _write_campaign_files(cdir, characters={"Broden": {}})

    snap = hud.load_snapshot(cdir, None)

    assert snap.characters == {"Broden": {}}
    assert snap.game_state is None
    assert snap.quests is None


def test_load_snapshot_keeps_last_good_section_on_midwrite(tmp_path):
    cdir = _make_campaign(tmp_path, "Alpha")
    _write_campaign_files(cdir, characters={"Broden": {"hp": 8}}, game_state={"in_combat": False})
    last = hud.load_snapshot(cdir, None)

    (cdir / "characters.json").write_text('{"Broden": {"hp"')  # mid-write
    _write_campaign_files(cdir, game_state={"in_combat": True})
    snap = hud.load_snapshot(cdir, last)

    assert snap.characters == {"Broden": {"hp": 8}}  # last good copy kept
    assert snap.game_state == {"in_combat": True}  # fresh section still read


def test_snapshot_mtimes_change_when_a_file_changes(tmp_path):
    cdir = _make_campaign(tmp_path, "Alpha")
    _write_campaign_files(cdir, characters={}, game_state={}, quests={})
    for f in ("characters.json", "game_state.json", "quests.json"):
        os.utime(cdir / f, (1000, 1000))
    before = hud.snapshot_mtimes(cdir)

    os.utime(cdir / "game_state.json", (2000, 2000))

    assert hud.snapshot_mtimes(cdir) != before


def test_snapshot_mtimes_missing_files_are_zero(tmp_path):
    cdir = tmp_path / "empty"
    cdir.mkdir()
    assert hud.snapshot_mtimes(cdir) == (0.0, 0.0, 0.0)


# -------------------------------------------------------------------- quests


def test_active_quest_titles_filters_by_status():
    quests = {
        "Q1": {"title": "Find the bones", "status": "active"},
        "Q2": {"title": "Old favor", "status": "completed"},
    }
    assert hud.active_quest_titles(quests, None) == ["Find the bones"]


def test_active_quest_titles_falls_back_to_game_state():
    gs = {"active_quests": ["Chapter 1"]}
    assert hud.active_quest_titles(None, gs) == ["Chapter 1"]


def test_active_quest_titles_empty_when_no_data():
    assert hud.active_quest_titles(None, None) == []
