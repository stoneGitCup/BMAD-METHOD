import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import models  # noqa: E402

SKILL = "---\nname: demo\ndescription: Does a thing.\n  Over two lines.\nlicense: MIT\n---\n\n# Demo\n"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class ResolveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        write(self.root / "_bmad" / "config.toml", "[core]\n")

    def test_the_personal_layer_beats_the_team_layer_per_key(self):
        write(
            self.root / "_bmad" / "custom" / "config.toml",
            '[models.skills.demo]\nmodel = "opus"\neffort = "high"\n',
        )
        write(self.root / "_bmad" / "custom" / "config.user.toml", '[models.skills.demo]\nmodel = "sonnet"\n')
        resolution = models.resolve(self.root)
        self.assertEqual(resolution.skills, {"demo": {"model": "sonnet", "effort": "high"}})
        self.assertEqual(resolution.problems, [])

    def test_an_empty_string_unsets_a_value_from_a_lower_layer(self):
        write(self.root / "_bmad" / "custom" / "config.toml", '[models.skills.demo]\nmodel = "opus"\n')
        write(self.root / "_bmad" / "custom" / "config.user.toml", '[models.skills.demo]\nmodel = ""\n')
        self.assertEqual(models.resolve(self.root).skills, {})

    def test_a_bad_value_is_a_problem_naming_file_and_key_and_skips_the_skill(self):
        write(
            self.root / "_bmad" / "custom" / "config.toml",
            '[models.skills.demo]\nmodel = "opus"\neffort = "huge"\n[models.skills.other]\nmodel = "gpt-5"\n',
        )
        resolution = models.resolve(self.root)
        self.assertEqual(resolution.skills, {})
        self.assertEqual(resolution.invalid, {"demo", "other"})
        self.assertEqual(
            [(item["file"], item["key"]) for item in resolution.problems],
            [
                ("_bmad/custom/config.toml", "models.skills.demo.effort"),
                ("_bmad/custom/config.toml", "models.skills.other.model"),
            ],
        )

    def test_a_skill_entry_that_is_not_a_table_is_a_problem_and_skips_the_skill(self):
        write(self.root / "_bmad" / "custom" / "config.toml", '[models.skills]\ndemo = "opus"\n')
        resolution = models.resolve(self.root)
        self.assertEqual([item["key"] for item in resolution.problems], ["models.skills.demo"])
        self.assertEqual(resolution.invalid, {"demo"})
        self.assertNotIn("demo", resolution.skills)

    def test_a_personal_table_repairs_a_team_entry_that_is_not_a_table(self):
        write(self.root / "_bmad" / "custom" / "config.toml", '[models.skills]\ndemo = "opus"\n')
        write(self.root / "_bmad" / "custom" / "config.user.toml", '[models.skills.demo]\nmodel = "opus"\n')
        resolution = models.resolve(self.root)
        self.assertEqual((resolution.problems, resolution.skills), ([], {"demo": {"model": "opus"}}))

    def test_a_key_named_table_is_an_unknown_key(self):
        write(self.root / "_bmad" / "custom" / "config.toml", '[models.skills.demo]\ntable = "x"\n')
        resolution = models.resolve(self.root)
        self.assertEqual([item["key"] for item in resolution.problems], ["models.skills.demo.table"])

    def test_an_unreadable_layer_is_a_problem(self):
        write(self.root / "_bmad" / "custom" / "config.toml", "[models\n")
        resolution = models.resolve(self.root)
        self.assertTrue(resolution.unreadable)
        self.assertEqual([item["file"] for item in resolution.problems], ["_bmad/custom/config.toml"])

    def test_aliases_and_full_ids_are_accepted(self):
        for value in (
            "fable",
            "opus[1m]",
            "inherit",
            "claude-opus-5-5",
            "us.anthropic.claude-sonnet-5-5",
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "claude-sonnet-4-5@20250929",
        ):
            self.assertIsNone(models.invalid_value("model", value), value)
        for value in ("gpt-5", "default", "Opus", "claude-", "claude-opus-", "claude-sonnet@", "anthropic.claude-x:"):
            self.assertIsNotNone(models.invalid_value("model", value), value)


class PhaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        write(self.root / "_bmad" / "config.toml", "[core]\n")
        self.record = self.root / "skills" / "bmod-demo"
        write(self.record / "models.toml", '[phases]\nplanner = "planning"\nbuilder = "implementation"\n')
        self.modules = (models.Module(self.record, ("planner", "builder", "helper")),)

    def resolve(self, team: str) -> models.Resolution:
        write(self.root / "_bmad" / "custom" / "config.toml", team)
        return models.resolve(self.root, self.modules)

    def test_a_phase_default_reaches_only_the_skills_of_that_phase(self):
        resolution = self.resolve('[models.phases.planning]\nmodel = "opus"\neffort = "high"\n')
        self.assertEqual(resolution.skills, {"planner": {"model": "opus", "effort": "high"}})

    def test_a_skill_setting_beats_the_phase_default_per_key(self):
        resolution = self.resolve(
            '[models.phases.planning]\nmodel = "opus"\neffort = "high"\n[models.skills.planner]\neffort = "low"\n'
        )
        self.assertEqual(resolution.skills["planner"], {"model": "opus", "effort": "low"})

    def test_a_skill_in_no_phase_takes_no_phase_default(self):
        resolution = self.resolve("".join(f'[models.phases.{phase}]\nmodel = "haiku"\n' for phase in models.PHASES))
        self.assertNotIn("helper", resolution.skills)

    def test_an_unknown_phase_and_a_bad_phase_value_are_problems(self):
        resolution = self.resolve('[models.phases.design]\nmodel = "opus"\n[models.phases.planning]\neffort = "x"\n')
        self.assertEqual(
            [item["key"] for item in resolution.problems],
            ["models.phases.design", "models.phases.planning.effort"],
        )
        self.assertEqual(resolution.invalid, {"planner"})

    def test_a_module_cannot_phase_another_modules_skill(self):
        write(self.record / "models.toml", '[phases]\nstranger = "planning"\nplanner = "testing"\n')
        resolution = models.resolve(self.root, self.modules)
        self.assertEqual(
            [(item["file"], item["key"]) for item in resolution.problems],
            [("bmod-demo/models.toml", "phases.stranger"), ("bmod-demo/models.toml", "phases.planner")],
        )


class PresetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        write(self.root / "_bmad" / "config.toml", "[core]\n")
        self.record = self.root / "skills" / "bmod-demo"
        write(
            self.record / "models.toml",
            '[phases]\nplanner = "planning"\n'
            '[presets.base]\nplanner = { model = "opus", effort = "high" }\nbuilder = { model = "sonnet" }\n'
            '[presets.big]\nextends = "base"\nplanner = { model = "fable", effort = "high" }\n',
        )
        self.modules = (models.Module(self.record, ("planner", "builder")),)

    def resolve(self, team: str) -> models.Resolution:
        write(self.root / "_bmad" / "custom" / "config.toml", team)
        return models.resolve(self.root, self.modules)

    def test_no_preset_unless_one_is_named(self):
        self.assertEqual(self.resolve("").skills, {})

    def test_a_preset_and_its_extension(self):
        base = self.resolve('[models]\npreset = "base"\n').skills
        self.assertEqual(base, {"planner": {"model": "opus", "effort": "high"}, "builder": {"model": "sonnet"}})
        big = self.resolve('[models]\npreset = "big"\n').skills
        self.assertEqual(big, {"planner": {"model": "fable", "effort": "high"}, "builder": {"model": "sonnet"}})

    def test_phase_and_skill_settings_beat_the_preset_per_key(self):
        skills = self.resolve(
            '[models]\npreset = "base"\n[models.phases.planning]\neffort = "low"\n'
            '[models.skills.builder]\nmodel = "haiku"\n'
        ).skills
        self.assertEqual(skills, {"planner": {"model": "opus", "effort": "low"}, "builder": {"model": "haiku"}})

    def test_the_personal_layer_can_turn_the_preset_off(self):
        write(self.root / "_bmad" / "custom" / "config.user.toml", '[models]\npreset = ""\n')
        self.assertEqual(self.resolve('[models]\npreset = "base"\n').skills, {})

    def test_an_unknown_preset_is_a_problem_and_its_skills_are_left_alone(self):
        resolution = self.resolve('[models]\npreset = "nope"\n')
        self.assertEqual([item["key"] for item in resolution.problems], ["models.preset"])
        self.assertEqual(resolution.skills, {})
        self.assertEqual(resolution.invalid, {"planner", "builder"})

    def test_a_bad_value_in_a_preset_is_a_problem_and_skips_the_skill(self):
        write(
            self.record / "models.toml",
            '[presets.base]\nplanner = { model = "gpt-5" }\nbuilder = "opus"\n',
        )
        write(self.root / "_bmad" / "custom" / "config.toml", '[models]\npreset = "base"\n')
        resolution = models.resolve(self.root, self.modules)
        self.assertEqual(
            [item["key"] for item in resolution.problems], ["presets.base.planner.model", "presets.base.builder"]
        )
        self.assertEqual((resolution.invalid, resolution.skills), ({"planner", "builder"}, {}))

    def test_an_extends_cycle_is_reported_once_where_it_closes(self):
        write(self.record / "models.toml", '[presets.a]\nextends = "b"\n[presets.b]\nextends = "a"\n')
        resolution = models.resolve(self.root, self.modules)
        self.assertEqual([item["key"] for item in resolution.problems], ["presets.b.extends"])


class ShippedModelsTests(unittest.TestCase):
    """The module records shipped in this repository phase only their own skills."""

    def test_every_shipped_models_toml_resolves_without_problems(self):
        skills_root = Path(__file__).resolve().parents[3]
        records = sorted(skills_root.glob("bmod-*/models.toml"))
        self.assertTrue(records)
        modules = tuple(
            models.Module(path.parent, tuple(models.load_toml(path.parent / "bmod.toml")["bmod"]["skills"]))
            for path in records
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write(root / "_bmad" / "config.toml", "[core]\n")
            self.assertEqual(models.resolve(root, modules).problems, [])


class ReconcileTests(unittest.TestCase):
    def test_a_first_write_records_the_shipped_value(self):
        changes, after, edited = models.reconcile({}, {"model": "opus"}, {})
        self.assertEqual(
            (changes, after, edited), ({"model": "opus"}, {"model": {"wrote": "opus", "shipped": None}}, [])
        )

    def test_a_skills_own_value_is_replaced_and_restored_on_removal(self):
        changes, after, _ = models.reconcile({"model": "haiku"}, {"model": "opus"}, {})
        self.assertEqual(changes, {"model": "opus"})
        changes, after, _ = models.reconcile({"model": "opus"}, {}, after)
        self.assertEqual((changes, after), ({"model": "haiku"}, {}))

    def test_nothing_set_and_nothing_recorded_changes_nothing(self):
        self.assertEqual(models.reconcile({"model": "haiku", "effort": "low"}, {}, {}), ({}, {}, []))

    def test_an_update_that_restored_the_shipped_value_is_written_again(self):
        record = {"model": {"wrote": "opus", "shipped": None}}
        self.assertEqual(models.reconcile({}, {"model": "opus"}, record)[0], {"model": "opus"})

    def test_a_hand_edit_is_left_alone_and_reported(self):
        record = {"model": {"wrote": "opus", "shipped": None}}
        changes, after, edited = models.reconcile({"model": "sonnet"}, {"model": "opus"}, record)
        self.assertEqual((changes, after, edited), ({}, record, ["model"]))
        self.assertEqual(models.reconcile({"model": "sonnet"}, {}, record)[:2], ({}, {}))


class RecordTests(unittest.TestCase):
    def test_absent_is_empty_and_a_malformed_record_is_unusable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "models-applied.json"
            self.assertEqual(models.read_record(path), {})
            for content in ("{", '{"demo": "x"}', '{"demo": {"model": {"wrote": "opus"}}}', '{"demo": {"tone": {}}}'):
                path.write_text(content, encoding="utf-8")
                self.assertIsNone(models.read_record(path), content)
            path.write_text('{"demo": {"model": {"wrote": "opus", "shipped": null}}}', encoding="utf-8")
            self.assertEqual(models.read_record(path), {"demo": {"model": {"wrote": "opus", "shipped": None}}})


class RewriteTests(unittest.TestCase):
    def test_keys_go_after_the_description_and_nothing_else_changes(self):
        updated = models.rewrite(SKILL, {"model": "opus", "effort": "high"})
        self.assertEqual(
            updated,
            "---\nname: demo\ndescription: Does a thing.\n  Over two lines.\nmodel: opus\neffort: high\n"
            "license: MIT\n---\n\n# Demo\n",
        )
        self.assertEqual(models.current(updated), {"model": "opus", "effort": "high"})

    def test_rewriting_with_the_same_values_is_a_no_op(self):
        once = models.rewrite(SKILL, {"model": "opus"})
        self.assertEqual(models.rewrite(once, {"model": "opus"}), once)

    def test_no_wanted_keys_restores_the_shipped_text(self):
        stamped = models.rewrite(SKILL, {"model": "opus", "effort": "high"})
        self.assertEqual(models.rewrite(stamped, {"model": None, "effort": None}), SKILL)
        self.assertIs(models.rewrite(SKILL, {}), SKILL)

    def test_values_with_brackets_are_quoted_and_read_back(self):
        updated = models.rewrite(SKILL, {"model": "opus[1m]"})
        self.assertIn("model: 'opus[1m]'\n", updated)
        self.assertEqual(models.current(updated), {"model": "opus[1m]"})

    def test_crlf_files_keep_their_line_endings(self):
        crlf = SKILL.replace("\n", "\r\n")
        updated = models.rewrite(crlf, {"effort": "low"})
        self.assertIn("effort: low\r\n", updated)
        self.assertEqual(models.rewrite(updated, {"effort": None}), crlf)

    def test_an_existing_line_is_replaced_in_place(self):
        shipped = SKILL.replace("license: MIT\n", "license: MIT\nmodel: haiku\n")
        self.assertEqual(models.rewrite(shipped, {"model": "opus"}), shipped.replace("model: haiku", "model: opus"))

    def test_a_byte_order_mark_is_allowed_but_not_leading_blank_lines(self):
        updated = models.rewrite("\ufeff" + SKILL, {"model": "opus"})
        self.assertTrue(updated.startswith("\ufeff---\n"))
        self.assertEqual(models.current(updated), {"model": "opus"})
        self.assertIsNone(models.frontmatter_bounds(("\n" + SKILL).splitlines(keepends=True)))

    def test_a_duplicated_key_is_collapsed_to_the_new_line(self):
        doubled = SKILL.replace("license: MIT\n", "model: haiku\nlicense: MIT\nmodel: sonnet\n")
        updated = models.rewrite(doubled, {"model": "opus"})
        self.assertEqual(updated.count("model:"), 1)
        self.assertEqual(models.current(updated), {"model": "opus"})

    def test_text_without_frontmatter_is_left_alone(self):
        self.assertEqual(models.rewrite("# No frontmatter\n", {"model": "opus"}), "# No frontmatter\n")


if __name__ == "__main__":
    unittest.main()
