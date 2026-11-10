#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""The model and effort each installed skill runs on: resolved from the central config, written into SKILL.md.

Claude Code reads `model` and `effort` from a skill's frontmatter. The skills CLI overwrites installed copies
on every update, so `bmad setup` owns those two keys on installed BMad skills and writes them back from
`[models]` in the config layers. Shipped skills never carry them, so a key setup has no value for is removed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.dont_write_bytecode = True

from config_utils import ConfigError, load_toml  # noqa: E402

KEYS = ("model", "effort")
CONFIG_LAYERS = ("_bmad/config.toml", "_bmad/custom/config.toml", "_bmad/custom/config.user.toml")
MODEL_ALIASES = frozenset({"best", "fable", "opus", "sonnet", "haiku", "opus[1m]", "sonnet[1m]", "opusplan", "inherit"})
# A full id, optionally behind a provider prefix such as `anthropic.` or `us.anthropic.`.
FULL_MODEL_ID = re.compile(r"(?:[a-z0-9-]+\.)*claude-[a-z0-9.-]*[a-z0-9](?:\[1m\])?\Z")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TOP_LEVEL_KEY = re.compile(r"([A-Za-z0-9_-]+):")
NEEDS_QUOTES = re.compile(r"[\[\]:#]")


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


def resolve(project_root: Path, modules: tuple[Module, ...] = ()) -> Resolution:
    """Each skill's model and effort: its own setting, else its phase's default, per key.

    Each setting is taken after the team and personal layers, from the last layer that sets it. A skill's
    phase comes from the `models.toml` of the module listing it.
    """
    resolution = Resolution()
    phase_of = module_phases(modules, resolution)
    chosen: dict[tuple[str, str], dict[str, tuple[object, str]]] = {}
    for relative in CONFIG_LAYERS:
        table = models_table(load_toml(project_root / relative), relative, resolution)
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
                    resolution.problems.append(problem(relative, key_path, "must be a table"))
                    chosen.setdefault((level, name), {})["table"] = (None, relative)
                else:
                    for key, value in values.items():
                        chosen.setdefault((level, name), {})[key] = (value, relative)

    wanted: dict[tuple[str, str], dict[str, str]] = {}
    broken: set[tuple[str, str]] = set()
    for (level, name), values in chosen.items():
        found: dict[str, str] = {}
        for key, (value, relative) in values.items():
            message = None if key == "table" else invalid_value(key, value)
            if key == "table" or message is not None:
                if message is not None:
                    resolution.problems.append(problem(relative, f"models.{level}.{name}.{key}", message))
                broken.add((level, name))
            elif value:
                found[key] = str(value)
        wanted[(level, name)] = found

    for skill in {name for level, name in chosen if level == "skills"} | set(phase_of):
        levels = [("skills", skill)]
        if skill in phase_of:
            levels.append(("phases", phase_of[skill]))
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


def module_phases(modules: tuple[Module, ...], resolution: Resolution) -> dict[str, str]:
    """The phase each skill belongs to, from the `[phases]` of the module listing it."""
    phase_of: dict[str, str] = {}
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
            continue
        for skill, phase in phases.items():
            if skill not in module.skills:
                resolution.problems.append(problem(shown, f"phases.{skill}", "names a skill its module does not list"))
            elif phase not in PHASES:
                resolution.problems.append(problem(shown, f"phases.{skill}", f"expected one of {', '.join(PHASES)}"))
            else:
                phase_of[skill] = phase
    return phase_of


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


def frontmatter_bounds(lines: list[str]) -> int | None:
    """The index of the closing `---`, or None when the text does not open with a frontmatter block."""
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            return index
    return None


def current(text: str) -> dict[str, str]:
    """The `model` and `effort` values a SKILL.md carries now."""
    lines = text.splitlines(keepends=True)
    end = frontmatter_bounds(lines)
    found: dict[str, str] = {}
    if end is None:
        return found
    for line in lines[1:end]:
        match = TOP_LEVEL_KEY.match(line)
        if match and match.group(1) in KEYS:
            found[match.group(1)] = unquote(line[match.end() :].strip())
    return found


def rewrite(text: str, wanted: dict[str, str]) -> str:
    """The text with exactly the wanted `model` and `effort` lines; every other byte unchanged."""
    lines = text.splitlines(keepends=True)
    end = frontmatter_bounds(lines)
    if end is None:
        return text
    inner = [line for line in lines[1:end] if not owned(line)]
    if len(inner) == end - 1 and not wanted:
        return text
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    insert_at = len(inner)
    for index, line in enumerate(inner):
        if line.startswith("description:"):
            insert_at = index + 1
            while insert_at < len(inner) and not TOP_LEVEL_KEY.match(inner[insert_at]):
                insert_at += 1
            break
    if insert_at > 0 and not inner[insert_at - 1].endswith(("\n", "\r")):
        inner[insert_at - 1] += newline
    added = [f"{key}: {quote(wanted[key])}{newline}" for key in KEYS if key in wanted]
    return "".join([lines[0], *inner[:insert_at], *added, *inner[insert_at:], *lines[end:]])


def owned(line: str) -> bool:
    match = TOP_LEVEL_KEY.match(line)
    return bool(match) and match.group(1) in KEYS


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'" if NEEDS_QUOTES.search(value) else value


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner
    return value


def apply(skill_md: Path, wanted: dict[str, str]) -> bool:
    """Write the wanted keys into the file a SKILL.md path points at; True when it changed."""
    target = skill_md.resolve()
    text = target.read_bytes().decode("utf-8")
    updated = rewrite(text, wanted)
    if updated == text:
        return False
    target.write_bytes(updated.encode("utf-8"))
    return True
