"""桌面程序入口：React 界面 + pywebview + 原有 Python Agent。"""

import argparse
import multiprocessing
from pathlib import Path
import sys

from desktop_app.service import DesktopService


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="编程智能体桌面版")
    parser.add_argument("--dev", action="store_true", help="连接本机 Vite 开发服务器")
    args = parser.parse_args()
    try:
        import webview
    except ImportError:
        print("缺少桌面依赖，请先安装 requirements-desktop.txt。", file=sys.stderr)
        return 1
    index = ROOT / "frontend" / "dist" / "index.html"
    if not args.dev and not index.is_file():
        print("前端尚未构建，请先在 frontend 目录执行 npm run build。", file=sys.stderr)
        return 1
    try:
        service = DesktopService(ROOT, ROOT / ".agent_data" / "sessions")
    except (ValueError, RuntimeError) as error:
        print(f"无法启动桌面存储：{error}", file=sys.stderr)
        return 1
    window = webview.create_window(
        "Coding Agent", url="http://127.0.0.1:5173" if args.dev else str(index),
        js_api=service, width=1280, height=860, min_size=(640, 520),
        background_color="#ffffff" if service._catalog.settings["theme"] == "light" else "#212121",
        text_select=True,
        localization={"global.quitConfirmation": "确定退出吗？", "global.ok": "确定",
                      "global.quit": "退出", "global.cancel": "取消", "global.saveFile": "保存文件",
                      "windows.fileFilter.allFiles": "所有文件", "windows.fileFilter.otherFiles": "其他文件类型",
                      "linux.openFolder": "选择文件夹"},
    )
    service._window = window
    window.events.closing += service.shutdown
    try:
        # Windows 指定现代引擎，避免回退到旧版 IE。
        webview.start(gui="edgechromium" if sys.platform == "win32" else None,
                      debug=args.dev, http_server=True)
    finally:
        service.shutdown()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
