"""模型客户端及模型配置。"""

import os

from anthropic import Anthropic
from dotenv import load_dotenv


load_dotenv()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]
