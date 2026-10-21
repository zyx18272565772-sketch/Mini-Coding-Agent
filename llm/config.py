"""界面可公开的模型标识；不创建客户端，也不向界面返回密钥。"""

import os
from pathlib import Path
from dotenv import get_key


def configured_model():
    if os.getenv("MODEL_ID"):
        return os.environ["MODEL_ID"]
    path = Path(__file__).resolve().parents[1] / ".env"
    return (get_key(str(path), "MODEL_ID") if path.is_file() else None) or "未配置模型"
