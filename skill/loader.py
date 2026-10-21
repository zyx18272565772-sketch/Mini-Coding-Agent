"""启动时保存技能文件，向模型先提供目录、按需提供全文。"""

from pathlib import Path

import yaml


class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills: dict[str, dict[str, str]] = {}
        self.scan()

    @staticmethod
    def parse_frontmatter(text: str) -> tuple[dict, str]:
        """读取文件开头两个 --- 之间的 YAML，以及后面的正文。"""
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].rstrip("\r\n") != "---":
            return {}, text
        closing_index = next(
            (index for index, line in enumerate(lines[1:], start=1)
             if line.rstrip("\r\n") == "---"),
            None,
        )
        if closing_index is None:
            return {}, text
        frontmatter = "".join(lines[1:closing_index])
        body = "".join(lines[closing_index + 1:]).strip()
        try:
            metadata = yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return metadata, body

    def scan(self):
        self.skills.clear()
        if not self.skills_dir.exists():
            return
        skills_root = self.skills_dir.resolve()
        for manifest in sorted(self.skills_dir.glob("*/SKILL.md")):
            if (not manifest.is_file()
                    or not manifest.resolve().is_relative_to(skills_root)):
                continue
            content = manifest.read_text(encoding="utf-8")
            metadata, body = self.parse_frontmatter(content)
            raw_name = metadata.get("name")
            name = raw_name.strip() if isinstance(raw_name, str) else ""
            name = name or manifest.parent.name
            raw_description = metadata.get("description")
            description = raw_description.strip() if isinstance(raw_description, str) else ""
            description = description or body.split("\n", 1)[0]
            description = " ".join(description.lstrip("# ").split())
            self.skills[name] = {
                "name": name,
                "description": description,
                "content": content,
            }

    def catalog(self) -> str:
        """只返回名称和简介，供系统提示词使用。"""
        if not self.skills:
            return "（未找到技能）"
        return "\n".join(
            f"- {skill['name']}: {skill['description']}"
            for skill in self.skills.values()
        )

    def load(self, name: str) -> str:
        """按注册的技能名称返回完整文件内容，名称不作为路径使用。"""
        skill = self.skills.get(name)
        if skill:
            return skill["content"]
        available = ", ".join(self.skills) or "（未找到技能）"
        return f"错误：未知技能 '{name}'。可用技能：{available}"


SKILLS_DIR = Path.cwd().resolve() / "skills"
SKILL_LOADER = SkillLoader(SKILLS_DIR)
