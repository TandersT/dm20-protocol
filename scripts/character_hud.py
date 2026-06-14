# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "textual>=0.80",
# ]
# ///
"""Live paged character HUD for dm20-protocol play sessions.

Shows the active campaign's hero — vitals, spells, features, inventory —
and re-renders within ~1 s as dm20 saves state during play. Run it in a
second tmux window alongside the story session:

    tmux new-window -n hud 'uv run scripts/character_hud.py'

Keys: 1-4 jump to a page (Vitals, Spells, Features & Feats, Inventory),
left/right cycle pages, c cycles heroes when the campaign has several,
q quits.

Data comes straight from the campaign JSON files (characters.json,
game_state.json, quests.json). Storage dir resolution: --storage-dir flag,
then the DM20_STORAGE_DIR env var, then <repo>/data. dm20 writes files
atomically (temp + rename), so mtime polling is reliable; a file caught
mid-write or missing never crashes the HUD — the last good data stays on
screen and the next poll recovers.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path

TRACKED_FILES = ("characters.json", "game_state.json", "quests.json")
CAMPAIGN_MARKERS = ("characters.json", "campaign.json")
ABILITY_ORDER = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
PAGES = (
    ("vitals", "Vitals"),
    ("spells", "Spells"),
    ("features", "Features & Feats"),
    ("inventory", "Inventory"),
)


# --------------------------------------------------------------------------
# Pure helpers (stdlib only — unit-tested in tests/test_character_hud.py)
# --------------------------------------------------------------------------


def markup_escape(text: str) -> str:
    """Neutralize Rich markup in campaign-controlled text.

    Escaping ``[`` is sufficient: a stray ``]``, ``[/]``, or unbalanced ``[``
    can no longer open a markup tag, so Textual/Rich never tries to parse it.
    Applied everywhere campaign data flows into markup so a homebrew name with
    a bracket can't crash the HUD.
    """
    return text.replace("[", r"\[")


def ability_modifier(score: int) -> int:
    """D&D 5e ability modifier for a raw score."""
    return (score - 10) // 2


def format_modifier(mod: int) -> str:
    """Signed modifier string: 3 -> '+3', -1 -> '-1'."""
    return f"{mod:+d}"


def hp_fraction(current: int, maximum: int) -> float:
    """Current/max HP as a fraction clamped to [0, 1]; 0.0 when max is unusable."""
    if maximum <= 0:
        return 0.0
    return min(max(current / maximum, 0.0), 1.0)


def hp_color(fraction: float) -> str:
    """Bar color for an HP fraction: green > 0.5 >= yellow > 0.25 >= red."""
    if fraction > 0.5:
        return "green"
    if fraction > 0.25:
        return "yellow"
    return "red"


def parse_spell_slots(raw: dict | None) -> dict[int, int]:
    """Normalize a spell-slot mapping from raw JSON (string keys) to {int: int}."""
    slots: dict[int, int] = {}
    for key, value in (raw or {}).items():
        try:
            level = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, int):
            slots[level] = value
    return slots


def slot_pips(max_slots: int, used: int) -> str:
    """Spell-slot pips: filled = available, hollow = used."""
    if max_slots <= 0:
        return ""
    used = min(max(used, 0), max_slots)
    return "●" * (max_slots - used) + "○" * used


def spell_level_header(level: int, pips: str) -> str:
    """Header label for a non-cantrip spell level in the Spells list.

    ``pips`` is empty when the caster has no slot data for ``level`` (the
    DM2-17 empty-``spell_slots`` case); fall back to a hint. The hint's
    brackets are markup-escaped because Textual parses Option labels as Rich
    markup and would otherwise silently swallow ``[no slot data]`` as a span.
    """
    return f"— Level {level} {pips or markup_escape('[no slot data]')} —"


def resolve_storage_dir(flag: str | None) -> Path:
    """Storage dir: --storage-dir flag, then DM20_STORAGE_DIR env, then <repo>/data."""
    if flag:
        return Path(flag).expanduser().resolve()
    env = os.environ.get("DM20_STORAGE_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "data"


def find_campaign_dirs(storage_dir: Path) -> list[Path]:
    """Campaign dirs under <storage>/campaigns/ (marked by characters/campaign.json)."""
    campaigns_root = storage_dir / "campaigns"
    if not campaigns_root.is_dir():
        return []
    return sorted(
        (
            entry
            for entry in campaigns_root.iterdir()
            if entry.is_dir() and any((entry / marker).is_file() for marker in CAMPAIGN_MARKERS)
        ),
        key=lambda entry: entry.name,
    )


def campaign_mtime(campaign_dir: Path) -> float:
    """Recency of a campaign dir: max mtime of its tracked files (dir mtime fallback)."""
    mtimes = []
    for name in TRACKED_FILES:
        try:
            mtimes.append((campaign_dir / name).stat().st_mtime)
        except OSError:
            continue
    if mtimes:
        return max(mtimes)
    try:
        return campaign_dir.stat().st_mtime
    except OSError:
        return 0.0


def resolve_campaign_dir(storage_dir: Path, name: str | None) -> Path | None:
    """Campaign dir by name (case-insensitive fallback), else most recently modified."""
    candidates = find_campaign_dirs(storage_dir)
    if name:
        for candidate in candidates:
            if candidate.name == name:
                return candidate
        lowered = name.lower()
        for candidate in candidates:
            if candidate.name.lower() == lowered:
                return candidate
        return None
    if not candidates:
        return None
    return max(candidates, key=campaign_mtime)


def load_json_tolerant(path: Path) -> dict | None:
    """Load a JSON object; None on missing file or invalid JSON (e.g. mid-write)."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def resolve_hero(characters: dict, requested: str | None) -> str | None:
    """Pick a hero: the requested name (case-insensitive) or the first sorted one."""
    names = sorted(characters)
    if requested:
        lowered = requested.lower()
        for name in names:
            if name.lower() == lowered:
                return name
        return None
    return names[0] if names else None


def cycle_hero(characters: dict, current: str | None) -> str | None:
    """Next hero in sorted order, wrapping; first when current is unknown."""
    names = sorted(characters)
    if not names:
        return None
    if current in names:
        return names[(names.index(current) + 1) % len(names)]
    return names[0]


@dataclass
class Snapshot:
    """One tolerant read of the campaign files, with the mtimes used for polling."""

    characters: dict | None = None
    game_state: dict | None = None
    quests: dict | None = None
    mtimes: tuple[float, float, float] = (0.0, 0.0, 0.0)


def snapshot_mtimes(campaign_dir: Path) -> tuple[float, float, float]:
    """Mtimes of the tracked files (0.0 when missing) — the poll's change signal."""

    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    times = tuple(mtime(campaign_dir / name) for name in TRACKED_FILES)
    return (times[0], times[1], times[2])


def load_snapshot(campaign_dir: Path, last: Snapshot | None) -> Snapshot:
    """Read all sections tolerantly, keeping the last good copy of any that fail."""
    sections: dict[str, dict | None] = {}
    for attr, filename in (
        ("characters", "characters.json"),
        ("game_state", "game_state.json"),
        ("quests", "quests.json"),
    ):
        data = load_json_tolerant(campaign_dir / filename)
        if data is None and last is not None:
            data = getattr(last, attr)
        sections[attr] = data
    return Snapshot(mtimes=snapshot_mtimes(campaign_dir), **sections)


def active_quest_titles(quests: dict | None, game_state: dict | None) -> list[str]:
    """Active quest titles from quests.json, falling back to game_state.active_quests."""
    if quests:
        return [
            quest.get("title") or key
            for key, quest in quests.items()
            if isinstance(quest, dict) and quest.get("status") == "active"
        ]
    if game_state:
        return [str(title) for title in game_state.get("active_quests") or []]
    return []


# --------------------------------------------------------------------------
# Textual UI (imported lazily so the helpers above stay importable in pytest)
# --------------------------------------------------------------------------


def build_app() -> type:
    """Build the Textual app class; textual is only required from here on."""
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, VerticalScroll
    from textual.widgets import Collapsible, ContentSwitcher, Footer, OptionList, Static
    from textual.widgets.option_list import Option

    def hp_bar(current: int, maximum: int, width: int = 24) -> str:
        fraction = hp_fraction(current, maximum)
        filled = round(fraction * width)
        color = hp_color(fraction)
        return f"[{color}]{'█' * filled}[/][dim]{'─' * (width - filled)}[/]"

    def class_line(char: dict) -> str:
        classes = char.get("classes") or []
        parts = []
        for cls in classes:
            name = cls.get("name", "?")
            level = cls.get("level", "?")
            subclass = cls.get("subclass")
            parts.append(f"{name} {level}" + (f" ({subclass})" if subclass else ""))
        return " / ".join(parts) or "—"

    def slot_lines(char: dict) -> list[str]:
        max_slots = parse_spell_slots(char.get("spell_slots"))
        used_slots = parse_spell_slots(char.get("spell_slots_used"))
        return [
            f"L{level} {slot_pips(max_slots[level], used_slots.get(level, 0))}"
            for level in sorted(max_slots)
            if max_slots[level] > 0
        ]

    def render_vitals(char: dict, game_state: dict | None, quests: dict | None) -> str:
        race = (char.get("race") or {}).get("name", "—")
        lines = [
            f"[bold]{markup_escape(char.get('name', '—'))}[/]"
            f"  [dim]{markup_escape(class_line(char))} · {markup_escape(race)}"
            f" · {markup_escape(char.get('background') or '—')}[/]",
            "",
        ]

        hp_cur = char.get("hit_points_current", 0)
        hp_max = char.get("hit_points_max", 0)
        temp = char.get("temporary_hit_points", 0)
        hp_line = f"HP {hp_bar(hp_cur, hp_max)} {hp_cur}/{hp_max}"
        if temp:
            hp_line += f" [cyan]+{temp} temp[/]"
        lines.append(hp_line)

        stat_bits = [
            f"AC [bold]{char.get('armor_class', '—')}[/]",
            f"Speed [bold]{char.get('speed', '—')}[/] ft",
            f"Prof [bold]{format_modifier(char.get('proficiency_bonus', 0))}[/]",
        ]
        if char.get("inspiration"):
            stat_bits.append("[yellow]★ Inspiration[/]")
        lines.append("  ".join(stat_bits))
        lines.append("")

        abilities = char.get("abilities") or {}
        ability_bits = []
        for name in ABILITY_ORDER:
            score = (abilities.get(name) or {}).get("score", 10)
            mod = format_modifier(ability_modifier(score))
            ability_bits.append(f"[bold]{name[:3].upper()}[/] {score:>2} ({mod})")
        lines.append("  ".join(ability_bits))

        pips = slot_lines(char)
        if pips:
            lines.append("")
            lines.append("Slots  " + "   ".join(pips))

        conditions = char.get("conditions") or []
        if conditions:
            lines.append("")
            lines.append("[red]Conditions:[/] " + ", ".join(map(markup_escape, conditions)))
        concentration = char.get("concentration") or {}
        if concentration.get("spell_name"):
            lines.append(f"[magenta]Concentrating:[/] {markup_escape(concentration['spell_name'])}")
        saves_ok = char.get("death_saves_success", 0)
        saves_bad = char.get("death_saves_failure", 0)
        if hp_cur <= 0 or saves_ok or saves_bad:
            lines.append(
                f"[bold red]Death saves:[/] [green]{'✓' * saves_ok}[/][red]{'✗' * saves_bad}[/]"
            )

        if game_state:
            lines.append("")
            lines.append(
                f"[dim]Location[/] {markup_escape(game_state.get('current_location') or '—')}"
                f"   [dim]Date[/] {markup_escape(game_state.get('current_date_in_game') or '—')}"
                f"   [dim]Funds[/] {markup_escape(game_state.get('party_funds') or '—')}"
            )
        quest_titles = active_quest_titles(quests, game_state)
        if quest_titles:
            lines.append("")
            lines.append("[bold]Active quests[/]")
            lines.extend(f"  • {markup_escape(title)}" for title in quest_titles)

        return "\n".join(lines)

    def render_spell_detail(spell: dict) -> str:
        level = spell.get("level", 0)
        level_text = "Cantrip" if level == 0 else f"Level {level}"
        components = ", ".join(spell.get("components") or [])
        lines = [
            f"[bold]{markup_escape(spell.get('name', '—'))}[/]"
            f"  [dim]{level_text} · {markup_escape(spell.get('school', '—'))}[/]",
            "",
            f"[dim]Casting time[/] {markup_escape(spell.get('casting_time') or '—')}"
            f"   [dim]Range[/] {spell.get('range', '—')} ft"
            f"   [dim]Duration[/] {markup_escape(spell.get('duration') or '—')}",
            f"[dim]Components[/] {markup_escape(components) or '—'}",
            "",
            markup_escape(spell.get("description") or "—"),
        ]
        if spell.get("material_components"):
            lines.append("")
            lines.append(f"[dim]Materials[/] {markup_escape(spell['material_components'])}")
        return "\n".join(lines)

    def render_inventory(char: dict) -> str:
        lines = ["[bold]Equipment[/]"]
        equipment = char.get("equipment") or {}
        for slot, item in equipment.items():
            slot_name = slot.replace("_", " ").title()
            if item:
                qty = item.get("quantity", 1)
                qty_text = f" ×{qty}" if qty and qty != 1 else ""
                lines.append(f"  {slot_name:<14} {markup_escape(item.get('name', '—'))}{qty_text}")
            else:
                lines.append(f"  {slot_name:<14} [dim]—[/]")

        lines.append("")
        lines.append("[bold]Inventory[/]")
        inventory = char.get("inventory") or []
        if not inventory:
            lines.append("  [dim]empty[/]")
        for item in inventory:
            qty = item.get("quantity", 1)
            weight = item.get("weight")
            weight_text = f"{weight:g} lb" if weight is not None else "—"
            value = item.get("value")
            value_text = f"  [yellow]{markup_escape(value)}[/]" if value else ""
            lines.append(
                f"  {qty:>3} × {markup_escape(item.get('name', '—')):<38} "
                f"[dim]{weight_text}[/]{value_text}"
            )
        return "\n".join(lines)

    def render_combat_banner(game_state: dict) -> str:
        current = game_state.get("current_turn")
        entries = []
        for participant in game_state.get("initiative_order") or []:
            name = participant.get("name", "?")
            initiative = participant.get("initiative", "?")
            text = f"{markup_escape(name)} ({initiative})"
            if name == current:
                text = f"[reverse bold] ▶ {text} [/]"
            entries.append(text)
        order = "  ".join(entries) or "[dim]no initiative order[/]"
        return f"[bold]⚔ COMBAT[/]  {order}"

    class CharacterHud(App):
        TITLE = "dm20 Character HUD"

        CSS = """
        #topbar {
            dock: top;
            height: 1;
            background: $panel;
            padding: 0 1;
        }
        #combat-banner {
            dock: top;
            height: auto;
            background: darkred;
            color: white;
            padding: 0 1;
        }
        #pages {
            height: 1fr;
        }
        #pages > * {
            padding: 1 2;
        }
        #spells {
            layout: horizontal;
            padding: 0;
        }
        #spell-list {
            width: 36%;
            border-right: solid $panel;
        }
        #spell-detail-scroll {
            width: 1fr;
            padding: 1 2;
        }
        #features Collapsible {
            margin-bottom: 0;
        }
        .source-heading {
            color: $accent;
            text-style: bold;
            margin-top: 1;
        }
        """

        BINDINGS = [
            Binding("1", "page('vitals')", "Vitals"),
            Binding("2", "page('spells')", "Spells"),
            Binding("3", "page('features')", "Features"),
            Binding("4", "page('inventory')", "Inventory"),
            Binding("left", "prev_page", "◀", show=False),
            Binding("right", "next_page", "▶", show=False),
            Binding("c", "cycle_hero", "Cycle hero"),
            Binding("q", "quit", "Quit"),
        ]

        def __init__(
            self,
            storage_dir: Path,
            campaign_name: str | None = None,
            character_name: str | None = None,
        ) -> None:
            super().__init__()
            self.storage_dir = storage_dir
            self.campaign_name = campaign_name
            self.requested_hero = character_name
            self.campaign_dir: Path | None = None
            self.snapshot: Snapshot | None = None
            self.hero: str | None = None
            self._spell_options: dict[str, dict] = {}

        def compose(self) -> ComposeResult:
            yield Static(id="topbar")
            yield Static(id="combat-banner")
            with ContentSwitcher(initial="vitals", id="pages"):
                yield VerticalScroll(Static(id="vitals-body"), id="vitals")
                with Horizontal(id="spells"):
                    yield OptionList(id="spell-list")
                    yield VerticalScroll(Static(id="spell-detail"), id="spell-detail-scroll")
                yield VerticalScroll(id="features")
                yield VerticalScroll(Static(id="inventory-body"), id="inventory")
            yield Footer()

        async def on_mount(self) -> None:
            self._resolve_campaign()
            await self._refresh_data()
            self.set_interval(1.0, self._poll)

        def _resolve_campaign(self) -> None:
            self.campaign_dir = resolve_campaign_dir(self.storage_dir, self.campaign_name)

        async def _poll(self) -> None:
            if self.campaign_dir is None or not self.campaign_dir.is_dir():
                self._resolve_campaign()
                if self.campaign_dir is None:
                    await self._render_all()
                    return
            if self.snapshot is None or snapshot_mtimes(self.campaign_dir) != self.snapshot.mtimes:
                await self._refresh_data()

        async def _refresh_data(self) -> None:
            if self.campaign_dir is not None:
                self.snapshot = load_snapshot(self.campaign_dir, self.snapshot)
            characters = (self.snapshot.characters if self.snapshot else None) or {}
            if self.hero not in characters:
                self.hero = resolve_hero(characters, self.requested_hero) or resolve_hero(
                    characters, None
                )
            await self._render_all()

        # ------------------------------------------------------------- render

        @property
        def _character(self) -> dict | None:
            characters = (self.snapshot.characters if self.snapshot else None) or {}
            return characters.get(self.hero) if self.hero else None

        def _placeholder(self) -> str:
            if self.campaign_dir is None:
                target = self.campaign_name or "any campaign"
                return (
                    f"[dim]Waiting for campaign data ({markup_escape(target)})…\n"
                    f"storage: {markup_escape(str(self.storage_dir))}[/]"
                )
            return "[dim]Waiting for character data…[/]"

        async def _render_all(self) -> None:
            game_state = self.snapshot.game_state if self.snapshot else None
            quests = self.snapshot.quests if self.snapshot else None
            char = self._character

            self._render_topbar()
            banner = self.query_one("#combat-banner", Static)
            if game_state and game_state.get("in_combat"):
                banner.update(render_combat_banner(game_state))
                banner.display = True
            else:
                banner.display = False

            vitals = self.query_one("#vitals-body", Static)
            inventory = self.query_one("#inventory-body", Static)
            if char is None:
                placeholder = self._placeholder()
                vitals.update(placeholder)
                inventory.update(placeholder)
                self._render_spells(None)
                await self._render_features(None)
                return

            vitals.update(render_vitals(char, game_state, quests))
            inventory.update(render_inventory(char))
            self._render_spells(char)
            await self._render_features(char)

        def _render_topbar(self) -> None:
            current_page = self.query_one("#pages", ContentSwitcher).current
            tabs = []
            for index, (page_id, label) in enumerate(PAGES, start=1):
                tab = f"{index} {label}"
                tabs.append(f"[reverse] {tab} [/]" if page_id == current_page else f" {tab} ")
            campaign = self.campaign_dir.name if self.campaign_dir else "no campaign"
            hero = self.hero or "—"
            self.query_one("#topbar", Static).update(
                f"[bold]{markup_escape(hero)}[/] [dim]· {markup_escape(campaign)}[/] "
                + "[dim]│[/]".join(tabs)
            )

        def _render_spells(self, char: dict | None) -> None:
            option_list = self.query_one("#spell-list", OptionList)
            previous = option_list.highlighted
            option_list.clear_options()
            self._spell_options = {}
            detail = self.query_one("#spell-detail", Static)

            spells = (char.get("spells_known") or []) if char else []
            if not spells:
                detail.update("[dim]No spells known.[/]")
                return

            max_slots = parse_spell_slots(char.get("spell_slots")) if char else {}
            used_slots = parse_spell_slots(char.get("spell_slots_used")) if char else {}
            by_level: dict[int, list[dict]] = {}
            for spell in spells:
                by_level.setdefault(spell.get("level", 0), []).append(spell)

            options = []
            index = 0
            for level in sorted(by_level):
                if level == 0:
                    header = "— Cantrips —"
                else:
                    pips = slot_pips(max_slots.get(level, 0), used_slots.get(level, 0))
                    header = spell_level_header(level, pips)
                options.append(Option(header, disabled=True))
                for spell in sorted(by_level[level], key=lambda s: s.get("name", "")):
                    option_id = f"spell-{index}"
                    index += 1
                    self._spell_options[option_id] = spell
                    options.append(
                        Option(
                            f"  {markup_escape(spell.get('name', '—'))}  "
                            f"[dim]{markup_escape(spell.get('school', ''))}[/]",
                            id=option_id,
                        )
                    )
            option_list.add_options(options)

            if previous is not None and option_list.option_count:
                option_list.highlighted = min(previous, option_list.option_count - 1)
            elif option_list.option_count:
                option_list.highlighted = 1  # first non-header option

        def on_option_list_option_highlighted(self, event) -> None:
            spell = self._spell_options.get(event.option.id or "")
            if spell:
                self.query_one("#spell-detail", Static).update(render_spell_detail(spell))

        async def _render_features(self, char: dict | None) -> None:
            container = self.query_one("#features", VerticalScroll)
            expanded = {
                collapsible.title
                for collapsible in container.query(Collapsible)
                if not collapsible.collapsed
            }
            await container.remove_children()

            features = (char.get("features") or []) if char else []
            if not features:
                fallback = (char.get("features_and_traits") or []) if char else []
                if fallback:
                    body = "\n".join(f"  • {markup_escape(name)}" for name in fallback)
                    await container.mount(Static(f"[bold]Features & Traits[/]\n{body}"))
                else:
                    await container.mount(Static("[dim]No features recorded.[/]"))
                return

            by_source: dict[str, list[dict]] = {}
            for feature in features:
                by_source.setdefault(feature.get("source") or "Other", []).append(feature)

            widgets = []
            for source in sorted(by_source):
                widgets.append(Static(markup_escape(source), classes="source-heading"))
                for feature in by_source[source]:
                    title = markup_escape(feature.get("name", "—"))
                    raw_description = feature.get("description")
                    description = (
                        markup_escape(raw_description)
                        if raw_description
                        else "[dim]no description[/dim]"
                    )
                    widgets.append(
                        Collapsible(
                            Static(description),
                            title=title,
                            collapsed=title not in expanded,
                        )
                    )
            await container.mount(*widgets)

        # ------------------------------------------------------------ actions

        def action_page(self, page_id: str) -> None:
            self.query_one("#pages", ContentSwitcher).current = page_id
            self._render_topbar()

        def _step_page(self, delta: int) -> None:
            switcher = self.query_one("#pages", ContentSwitcher)
            page_ids = [page_id for page_id, _ in PAGES]
            index = page_ids.index(switcher.current) if switcher.current in page_ids else 0
            self.action_page(page_ids[(index + delta) % len(page_ids)])

        def action_prev_page(self) -> None:
            self._step_page(-1)

        def action_next_page(self) -> None:
            self._step_page(1)

        async def action_cycle_hero(self) -> None:
            characters = (self.snapshot.characters if self.snapshot else None) or {}
            self.hero = cycle_hero(characters, self.hero)
            self.requested_hero = None
            await self._render_all()

    return CharacterHud


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live paged character HUD for dm20-protocol play sessions.",
        epilog="Run in a second tmux window: "
        "tmux new-window -n hud 'uv run scripts/character_hud.py'",
    )
    parser.add_argument(
        "--storage-dir",
        help="dm20 storage dir (default: $DM20_STORAGE_DIR, else <repo>/data)",
    )
    parser.add_argument(
        "--campaign",
        help="campaign dir name (default: most recently modified campaign)",
    )
    parser.add_argument(
        "--character",
        help="hero to display (default: auto-selected; press c to cycle)",
    )
    args = parser.parse_args()

    app_class = build_app()
    app_class(
        storage_dir=resolve_storage_dir(args.storage_dir),
        campaign_name=args.campaign,
        character_name=args.character,
    ).run()


if __name__ == "__main__":
    main()
