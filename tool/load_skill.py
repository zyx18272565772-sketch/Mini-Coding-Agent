"""load_skill 工具：按名称返回技能的完整说明。"""

from skill.loader import SKILL_LOADER


def run_load_skill(name: str) -> str:
    return SKILL_LOADER.load(name)
