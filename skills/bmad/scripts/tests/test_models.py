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
