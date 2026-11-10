---
title: 'Choose Models per Skill'
description: Run each BMad skill on the Claude model and effort its work needs, by skill, by phase, or with the shipped roles preset.
sidebar:
  order: 6
---

Use `bmad setup` to run each BMad skill on the Claude model and effort level its work needs. Planning and review do better on a strong model at high effort. Routine builds run well, and cost less, on a smaller one.

Claude Code reads `model` and `effort` from a skill's `SKILL.md`. Don't edit those files yourself, because `npx skills update` overwrites them. Put the settings in your BMad config instead. `bmad setup` writes them into the installed skills, and writes them back after each update.

## When to Use This

- Analysis, planning or code review should run on a stronger model than your session default.
- Builds and utility skills should run on a cheaper, faster model.
- A team wants the same models for everyone, committed with the project.
- You want to try Claude Fable on architecture work.

:::note[Prerequisites]
BMad set up in the project (`bmad setup`), and Claude Code as your coding tool. Other tools ignore these settings.
:::

## Steps

### 1. Pick where the setting lives

| File | Who it applies to |
| --- | --- |
| `_bmad/custom/config.toml` | The whole team; commit it |
| `_bmad/custom/config.user.toml` | Only you; Git ignores it |

The personal file wins over the team file, one key at a time.

### 2. Choose a preset, a phase default or a single skill

Switch on the shipped preset to give every role a suitable model:

```toml
[models]
preset = "roles"
```

`roles` runs analysis, planning and code review on Opus at high effort, architecture on Opus at `xhigh`, builds and tests on Sonnet at medium effort, and sharding on Haiku. `preset = "roles-fable"` is the same except that `bmad-agent-architect` and `bmad-architecture` run on Fable at high effort. Each module's `models.toml` lists the exact values.

Set a default for a whole phase. The phases are `analysis`, `planning`, `implementation`, `validation` and `utility`.

```toml
[models.phases.implementation]
model = "sonnet"
effort = "medium"
```

Set one skill:

```toml
[models.skills.bmad-architecture]
model = "fable"
effort = "high"
```

For each key, a skill's own setting wins over its phase default, and a phase default wins over the preset. An empty string turns a value off, for example `preset = ""` in your personal file.

`model` takes a Claude Code alias (`opus`, `sonnet`, `haiku`, `fable`, `best`, `opusplan`, `opus[1m]`, `sonnet[1m]`, `inherit`) or a full model id such as `claude-opus-5-5`. `effort` takes `low`, `medium`, `high`, `xhigh` or `max`.

### 3. Run setup

Ask the `bmad` skill to run `bmad setup`. It writes `model` and `effort` into each installed skill and lists them under `applied`. A key you remove from the config is removed from the skill on the next setup.

After `npx skills update`, `bmad status` lists any skill whose settings were overwritten. Run `bmad setup` again to restore them.

## What You Get

- Each configured skill runs on its model and effort whenever you start it in Claude Code. The setting lasts until your next message, then your session model resumes.
- A status check that catches settings an update overwrote.
- An error from setup, naming the file and key, for a value it does not recognize. Setup leaves that skill unchanged.

## Tips

:::caution[What setup does not change]
Setup only writes skills installed in this project. It lists settings for globally installed or plugin-managed skills under `not_applied` and leaves those files alone. Your personal settings change the installed skill files too, so if your team commits `.agents/skills`, your personal settings show up in that diff.
:::

- `bmad-review`, `bmad-facilitate`, `bmad-advanced-elicitation` and `bmad-party-mode` are usually started by another skill partway through a turn. The phase defaults and the preset leave them unset, so they keep the calling skill's model. Set one under `[models.skills]` only if you want it to change the model for the rest of that turn.
- Fable can cost extra on some plans, and some gateways do not serve it. Try `roles-fable` on one project before you adopt it.
