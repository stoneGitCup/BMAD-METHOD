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

    def test_aliases_and_full_ids_are_accepted(self):
        for value in ("fable", "opus[1m]", "inherit", "claude-opus-5-5", "us.anthropic.claude-sonnet-5-5"):
            self.assertIsNone(models.invalid_value("model", value), value)
        for value in ("gpt-5", "default", "Opus", "claude-"):
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

    def test_skills_started_mid_turn_are_in_no_phase(self):
        skills_root = Path(__file__).resolve().parents[3]
        phased = set()
        for path in skills_root.glob("bmod-*/models.toml"):
            phased |= set(models.load_toml(path).get("phases", {}))
        self.assertFalse(phased & {"bmad-review", "bmad-facilitate", "bmad-advanced-elicitation", "bmad-party-mode"})


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
        self.assertEqual(models.rewrite(stamped, {}), SKILL)
        self.assertIs(models.rewrite(SKILL, {}), SKILL)

    def test_values_with_brackets_are_quoted_and_read_back(self):
        updated = models.rewrite(SKILL, {"model": "opus[1m]"})
        self.assertIn("model: 'opus[1m]'\n", updated)
        self.assertEqual(models.current(updated), {"model": "opus[1m]"})

    def test_crlf_files_keep_their_line_endings(self):
        crlf = SKILL.replace("\n", "\r\n")
        updated = models.rewrite(crlf, {"effort": "low"})
        self.assertIn("effort: low\r\n", updated)
        self.assertEqual(models.rewrite(updated, {}), crlf)

    def test_text_without_frontmatter_is_left_alone(self):
        self.assertEqual(models.rewrite("# No frontmatter\n", {"model": "opus"}), "# No frontmatter\n")


if __name__ == "__main__":
    unittest.main()
