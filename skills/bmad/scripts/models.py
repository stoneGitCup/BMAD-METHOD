#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""The model and effort each installed skill runs on: resolved from the central config, written into SKILL.md.

Claude Code reads `model` and `effort` from a skill's frontmatter. The skills CLI overwrites installed copies
on every update, so `bmad setup` writes them back from `[models]` in the config layers. It records what it
wrote in `_bmad/models-applied.json`, so it only ever replaces or removes its own values.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.dont_write_bytecode = True

from config_utils import ConfigError, load_toml  # noqa: E402

KEYS = ("model", "effort")
CONFIG_LAYERS = ("_bmad/config.toml", "_bmad/custom/config.toml", "_bmad/custom/config.user.toml")
MODEL_ALIASES = frozenset({"best", "fable", "opus", "sonnet", "haiku", "opus[1m]", "sonnet[1m]", "opusplan", "inherit"})
# A full id, optionally behind a provider prefix such as `us.anthropic.`, with a Bedrock `-v1:0` or Vertex `@date` tail.
FULL_MODEL_ID = re.compile(r"(?:[a-z0-9-]+\.)*claude-[a-z0-9][a-z0-9.:@-]*(?:\[1m\])?\Z")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TOP_LEVEL_KEY = re.compile(r"([A-Za-z0-9_-]+):")
NEEDS_QUOTES = re.compile(r"[\[\]:#@]")
RECORD = "_bmad/models-applied.json"


PHASES = ("analysis", "planning", "implementation", "validation", "utility")
MODULE_FILE = "models.toml"


class Module(NamedTuple):
    """An installed module record: its folder, where its `models.toml` would sit, and the skills it lists."""

    folder: Path
    skills: tuple[str, ...]


class Resolution:
    """Each skill's wanted keys, the skills left alone because a value is bad, and the problems found."""

    def __init__(self) -> None:
        self.skills: dict[str, dict[str, str]] = {}
        self.invalid: set[str] = set()
        self.problems: list[dict[str, object]] = []
        # A config layer that cannot be read leaves every skill alone: what it would have set is unknown.
        self.unreadable = False


def resolve(project_root: Path, modules: tuple[Module, ...] = ()) -> Resolution:
    """Each skill's model and effort: its own setting, else its phase's default, else the active preset, per key.

    Each setting is taken after the team and personal layers, from the last layer that sets it. A skill's
    phase and preset values come from the `models.toml` of the module listing it.
    """
    resolution = Resolution()
    phase_of, presets = module_settings(modules, resolution)
    chosen: dict[tuple[str, str], dict[str, tuple[object, str]]] = {}
    preset: tuple[object, str] = ("", "")
    # The layer that last gave an entry something other than a table; a later table clears it.
    not_tables: dict[tuple[str, str], str] = {}
    for relative in CONFIG_LAYERS:
        try:
            layer = load_toml(project_root / relative)
        except ConfigError as error:
            resolution.problems.append(problem(relative, "", str(error)))
            resolution.unreadable = True
            continue
        table = models_table(layer, relative, resolution)
        if "preset" in table:
            preset = (table["preset"], relative)
        for level in ("skills", "phases"):
            entries = table.get(level, {})
            if not isinstance(entries, dict):
                resolution.problems.append(problem(relative, f"models.{level}", "must be a table"))
                continue
            for name, values in entries.items():
                key_path = f"models.{level}.{name}"
                if level == "phases" and name not in PHASES:
                    resolution.problems.append(problem(relative, key_path, f"expected one of {', '.join(PHASES)}"))
                elif not isinstance(values, dict):
                    not_tables[(level, name)] = relative
                else:
                    not_tables.pop((level, name), None)
                    for key, value in values.items():
                        chosen.setdefault((level, name), {})[key] = (value, relative)

    wanted: dict[tuple[str, str], dict[str, str]] = {}
    broken: set[tuple[str, str]] = set(not_tables)
    for (level, name), relative in not_tables.items():
        resolution.problems.append(problem(relative, f"models.{level}.{name}", "must be a table"))
    for (level, name), values in chosen.items():
        found: dict[str, str] = {}
        for key, (value, relative) in values.items():
            message = invalid_value(key, value)
            if message is not None:
                resolution.problems.append(problem(relative, f"models.{level}.{name}.{key}", message))
                broken.add((level, name))
            elif value:
                found[key] = str(value)
        wanted[(level, name)] = found

    preset_values = active_preset(preset, presets, resolution)
    for skill, values in preset_values.items():
        if values is None:
            broken.add(("preset", skill))
        else:
            wanted[("preset", skill)] = values

    named = {name for level, name in (*chosen, *not_tables) if level == "skills"}
    for skill in named | set(phase_of) | set(preset_values):
        levels = [("skills", skill)]
        if skill in phase_of:
            levels.append(("phases", phase_of[skill]))
        levels.append(("preset", skill))
        if any(level in broken for level in levels):
            resolution.invalid.add(skill)
            continue
        merged: dict[str, str] = {}
        for key in KEYS:
            for level in levels:
                if key in wanted.get(level, {}):
                    merged[key] = wanted[level][key]
                    break
        if merged:
            resolution.skills[skill] = merged
    return resolution


def active_preset(
    preset: tuple[object, str], presets: dict[str, dict[str, dict[str, str] | None]], resolution: Resolution
) -> dict[str, dict[str, str] | None]:
    """The skills of the preset `[models] preset` names; a name no module ships leaves every preset skill alone."""
    name, relative = preset
    if name == "":
        return {}
    if not isinstance(name, str) or name not in presets:
        shipped = ", ".join(sorted(presets)) or "none"
        resolution.problems.append(
            problem(relative, "models.preset", f"{name!r} is not a preset; installed: {shipped}")
        )
        return {skill: None for values in presets.values() for skill in values}
    return presets[name]


def module_settings(
    modules: tuple[Module, ...], resolution: Resolution
) -> tuple[dict[str, str], dict[str, dict[str, dict[str, str] | None]]]:
    """Each skill's phase, and each preset's values per skill, from the `models.toml` of the module listing it.

    A preset's value for a skill is None when it is unusable. Presets of the same name in several modules
    are one preset.
    """
    phase_of: dict[str, str] = {}
    presets: dict[str, dict[str, dict[str, str] | None]] = {}
    for module in modules:
        shown = f"{module.folder.name}/{MODULE_FILE}"
        try:
            data = load_toml(module.folder / MODULE_FILE)
        except ConfigError as error:
            resolution.problems.append(problem(shown, "", str(error)))
            continue
        phases = data.get("phases", {})
        if not isinstance(phases, dict):
            resolution.problems.append(problem(shown, "phases", "must be a table"))
            phases = {}
        for skill, phase in phases.items():
            if skill not in module.skills:
                resolution.problems.append(problem(shown, f"phases.{skill}", "names a skill its module does not list"))
            elif phase not in PHASES:
                resolution.problems.append(problem(shown, f"phases.{skill}", f"expected one of {', '.join(PHASES)}"))
            else:
                phase_of[skill] = phase
        for name, values in module_presets(data, module, shown, resolution).items():
            presets.setdefault(name, {}).update(values)
    return phase_of, presets


def module_presets(
    data: dict, module: Module, shown: str, resolution: Resolution
) -> dict[str, dict[str, dict[str, str] | None]]:
    """One module's presets with `extends` applied: a preset starts from the one it extends, then overrides by skill."""
    raw = data.get("presets", {})
    if not isinstance(raw, dict):
        resolution.problems.append(problem(shown, "presets", "must be a table"))
        return {}
    expanded: dict[str, dict[str, dict[str, str] | None]] = {}

    def expand(name: str, chain: tuple[str, ...]) -> dict[str, dict[str, str] | None]:
        if name in expanded:
            return expanded[name]
        table = raw[name]
        result: dict[str, dict[str, str] | None] = {}
        if not isinstance(table, dict):
            resolution.problems.append(problem(shown, f"presets.{name}", "must be a table"))
            expanded[name] = result
            return result
        parent = table.get("extends")
        if parent is not None:
            if not isinstance(parent, str) or parent not in raw or parent in chain:
                resolution.problems.append(
                    problem(
                        shown, f"presets.{name}.extends", "must name another preset of this module, without a cycle"
                    )
                )
            else:
                result.update(expand(parent, (*chain, parent)))
        for skill, values in table.items():
            if skill == "extends":
                continue
            key_path = f"presets.{name}.{skill}"
            if skill not in module.skills:
                resolution.problems.append(problem(shown, key_path, "names a skill its module does not list"))
                continue
            if not isinstance(values, dict):
                resolution.problems.append(problem(shown, key_path, "must be a table"))
                result[skill] = None
                continue
            messages = [(key, invalid_value(key, value)) for key, value in values.items()]
            for key, message in messages:
                if message is not None:
                    resolution.problems.append(problem(shown, f"{key_path}.{key}", message))
            usable = all(message is None for _, message in messages)
            result[skill] = {key: str(values[key]) for key in KEYS if values.get(key)} if usable else None
        expanded[name] = result
        return result

    return {name: expand(name, (name,)) for name in raw}


def models_table(layer: dict, relative: str, resolution: Resolution) -> dict:
    table = layer.get("models", {})
    if isinstance(table, dict):
        return table
    resolution.problems.append(problem(relative, "models", "must be a table"))
    return {}


def invalid_value(key: str, value: object) -> str | None:
    """Why a value cannot be written, or None. An empty string is a valid way to say unset."""
    if key not in KEYS:
        return f"unknown key; expected one of {', '.join(KEYS)}"
    if not isinstance(value, str):
        return "must be a string"
    if not value:
        return None
    if key == "effort" and value not in EFFORTS:
        return f"{value!r} is not an effort; expected one of {', '.join(EFFORTS)}"
    if key == "model" and value not in MODEL_ALIASES and not FULL_MODEL_ID.match(value):
        return f"{value!r} is not a Claude Code model alias or a full claude- model id"
    return None


def problem(relative: str, key: str, message: str) -> dict[str, object]:
    return {"kind": "models", "file": relative, "key": key, "message": f"{relative}: {key}: {message}"}


def frontmatter_bounds(lines: list[str]) -> tuple[int, int] | None:
    """The indexes of the opening and closing `---`, or None when the text does not open with a frontmatter block.

    A byte-order mark and blank lines may come first, as YAML loaders allow.
    """
    start = 0
    while start < len(lines) and not lines[start].lstrip("\ufeff").strip():
        start += 1
    if start == len(lines) or lines[start].lstrip("\ufeff").rstrip("\r\n") != "---":
        return None
    for index in range(start + 1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            return start, index
    return None


def current(text: str) -> dict[str, str]:
    """The `model` and `effort` values a SKILL.md carries now."""
    lines = text.splitlines(keepends=True)
    bounds = frontmatter_bounds(lines)
    found: dict[str, str] = {}
    if bounds is None:
        return found
    for line in lines[bounds[0] + 1 : bounds[1]]:
        match = TOP_LEVEL_KEY.match(line)
        if match and match.group(1) in KEYS:
            found[match.group(1)] = unquote(line[match.end() :].strip())
    return found


def reconcile(
    found: dict[str, str], wanted: dict[str, str], record: dict[str, dict[str, str | None]]
) -> tuple[dict[str, str | None], dict[str, dict[str, str | None]], list[str]]:
    """What to change in one skill, from its frontmatter now, its settings, and setup's record for it.

    The record keeps, per key, the value setup wrote and the value the skill shipped with (None for none). Setup
    writes over a value only when it is the shipped one or its own, and on removal restores the shipped one, so
    a skill's own `model` or `effort` survives and a hand edit is left alone. Returns the changes (None deletes
    a line), the record afterwards, and the keys left alone because someone edited them.
    """
    changes: dict[str, str | None] = {}
    after: dict[str, dict[str, str | None]] = {}
    edited: list[str] = []
    for key in KEYS:
        now, want, entry = found.get(key), wanted.get(key), record.get(key)
        shipped = entry["shipped"] if entry else now
        ours = entry["wrote"] if entry else now
        if want is not None:
            if now != want and now not in (shipped, ours):
                edited.append(key)
                if entry:
                    after[key] = entry
                continue
            if now != want:
                changes[key] = want
            after[key] = {"wrote": want, "shipped": shipped}
        elif entry and now == ours and now != shipped:
            changes[key] = shipped
    return changes, after, edited


def rewrite(text: str, changes: dict[str, str | None]) -> str:
    """The text with each changed key set in place, added after `description`, or deleted; other bytes unchanged."""
    lines = text.splitlines(keepends=True)
    bounds = frontmatter_bounds(lines)
    if bounds is None or not changes:
        return text
    start, end = bounds
    inner = lines[start + 1 : end]
    newline = "\r\n" if lines[start].endswith("\r\n") else "\n"
    pending = dict(changes)
    kept: list[str] = []
    for line in inner:
        match = TOP_LEVEL_KEY.match(line)
        key = match.group(1) if match else None
        if key in pending:
            value = pending.pop(key)
            if value is not None:
                kept.append(f"{key}: {quote(value)}{newline}")
            continue
        kept.append(line)
    added = [f"{key}: {quote(value)}{newline}" for key, value in pending.items() if value is not None]
    insert_at = len(kept)
    for index, line in enumerate(kept):
        if line.startswith("description:"):
            insert_at = index + 1
            while insert_at < len(kept) and not TOP_LEVEL_KEY.match(kept[insert_at]):
                insert_at += 1
            break
    if added and insert_at > 0 and not kept[insert_at - 1].endswith(("\n", "\r")):
        kept[insert_at - 1] += newline
    return "".join([*lines[: start + 1], *kept[:insert_at], *added, *kept[insert_at:], *lines[end:]])


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'" if NEEDS_QUOTES.search(value) else value


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner
    return value


def read_record(path: Path) -> dict[str, dict[str, dict[str, str | None]]]:
    """Setup's record of what it wrote, per skill and key; empty when absent or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_record(path: Path, record: dict[str, dict[str, dict[str, str | None]]]) -> None:
    if record:
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()
