"""模型可见的归档编号；不包含数据库连接信息，也不依赖数据库驱动。"""
import re

PREFIX = "artifact://"


def is_artifact_reference(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def artifact_id(reference):
    if not isinstance(reference, str) or not re.fullmatch(r"artifact://[0-9a-f]{32}", reference):
        raise ValueError("数据库归档编号无效，需使用完整的 artifact://编号")
    return reference.removeprefix(PREFIX)
