"""验证技能扫描、目录生成和按名称读取完整说明。"""

from pathlib import Path
import tempfile
import unittest

from skill.loader import SkillLoader


class SkillLoaderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "skills"

    def write_skill(self, folder, content):
        manifest = self.root / folder / "SKILL.md"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(content, encoding="utf-8")
        return manifest

    def test_catalog_has_metadata_but_full_instructions_are_loaded_by_name(self):
        content = "---\nname: review\ndescription: 检查代码问题\n---\n# 审查步骤\n逐行核对异常处理。\n"
        self.write_skill("code-review", content)
        loader = SkillLoader(self.root)

        self.assertEqual(loader.catalog(), "- review: 检查代码问题")
        self.assertNotIn("逐行核对", loader.catalog())
        self.assertEqual(loader.load("review"), content)

    def test_yaml_description_supports_multiple_lines_and_colons(self):
        self.write_skill("review", '---\nname: "review"\ndescription: |\n  检查代码: 异常处理\n  核对测试\n---\n详细步骤\n')
        self.assertEqual(SkillLoader(self.root).catalog(), "- review: 检查代码: 异常处理 核对测试")

    def test_missing_metadata_uses_folder_name_and_first_body_line(self):
        self.write_skill("review", "# 中文审查说明\n详细步骤\n")
        self.assertEqual(SkillLoader(self.root).catalog(), "- review: 中文审查说明")

    def test_invalid_or_non_string_metadata_falls_back_to_body(self):
        for metadata in ("name: [", "- review", "name: 123\ndescription: false"):
            with self.subTest(metadata=metadata):
                self.write_skill("review", f"---\n{metadata}\n---\n# 后备简介\n详细步骤\n")
                self.assertEqual(SkillLoader(self.root).catalog(), "- review: 后备简介")

    def test_unclosed_frontmatter_is_preserved_as_plain_text(self):
        content = "---\nname: review\n缺少结束标记\n"
        self.assertEqual(SkillLoader.parse_frontmatter(content), ({}, content))

    def test_missing_or_empty_skills_directory_is_allowed(self):
        loader = SkillLoader(self.root)
        self.assertEqual(loader.catalog(), "（未找到技能）")
        self.assertIn("未找到技能", loader.load("missing"))
        self.root.mkdir()
        loader.scan()
        self.assertEqual(loader.catalog(), "（未找到技能）")

    def test_unknown_name_is_not_interpreted_as_a_file_path(self):
        self.write_skill("review", "# 审查说明\n")
        outside = self.root.parent / "outside.md"
        outside.write_text("不应读取的文件", encoding="utf-8")
        loader = SkillLoader(self.root)

        for name in ("missing", "../outside.md", str(outside)):
            with self.subTest(name=name):
                output = loader.load(name)
                self.assertIn("未知技能", output)
                self.assertIn("review", output)
                self.assertNotIn("不应读取的文件", output)

    def test_load_uses_startup_content_and_rescan_removes_deleted_skills(self):
        manifest = self.write_skill("review", "# 原始说明\n")
        loader = SkillLoader(self.root)
        manifest.write_text("# 更新说明\n", encoding="utf-8")
        self.assertEqual(loader.load("review"), "# 原始说明\n")
        loader.scan()
        self.assertEqual(loader.load("review"), "# 更新说明\n")
        manifest.unlink()
        loader.scan()
        self.assertEqual(loader.catalog(), "（未找到技能）")

    def test_only_direct_skill_manifests_are_included_in_sorted_order(self):
        self.write_skill("z-last", "# 最后\n")
        self.write_skill("a-first", "# 最前\n")
        self.write_skill("nested/deeper", "# 深层文件\n")
        (self.root / "directory" / "SKILL.md").mkdir(parents=True)
        self.assertEqual(SkillLoader(self.root).catalog(), "- a-first: 最前\n- z-last: 最后")


if __name__ == "__main__":
    unittest.main()
