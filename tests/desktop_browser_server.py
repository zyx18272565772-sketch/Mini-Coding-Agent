"""仅供界面验收：本机临时会话 + 模拟工作进程，不调用真实模型。"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import unquote

from desktop_app.service import DesktopService
from tests.test_desktop_service import simulated_worker


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
METHODS = ("bootstrap", "create_project", "set_theme", "set_font_size", "delete_session", "delete_project", "rename_project", "rename_session", "pick_attachments", "create_session", "get_session", "choose_workspace", "send_message", "retry_run", "poll", "resolve_permission", "stop")


def main():
    os.environ["MODEL_ID"] = "deepseek-test"
    with tempfile.TemporaryDirectory() as temporary:
        app = DesktopService(ROOT, Path(temporary) / "sessions", worker_target=simulated_worker)
        reference_file = Path(temporary) / "参考文件.txt"
        reference_file.write_text("仅供模拟文件引用验收。", encoding="utf-8")
        reference_folder = Path(temporary) / "参考目录"
        reference_folder.mkdir()

        class FolderPicker:
            def create_file_dialog(self, *args, **kwargs):
                if "allow_multiple" in kwargs:
                    return (str(reference_file if kwargs["allow_multiple"] else reference_folder),)
                return (str(Path(temporary)),)
        app._window = FolderPicker()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                route = unquote(self.path.split("?", 1)[0])
                path = (DIST / route.lstrip("/")).resolve() if route != "/" else DIST / "index.html"
                if not path.is_relative_to(DIST) or not path.is_file():
                    self.send_error(404)
                    return
                body = path.read_bytes()
                content_type = "text/javascript" if path.suffix == ".js" else "text/css" if path.suffix == ".css" else "text/html"
                if path.name == "index.html":
                    bridge = "<script>window.pywebview={api:Object.fromEntries(" + json.dumps(METHODS) + ".map(method=>[method,(...args)=>fetch('/__api',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({method,args})}).then(r=>r.json())]))};</script>"
                    body = body.replace(b"<head>", b"<head>" + bridge.encode())
                self.send_response(200)
                self.send_header("Content-Type", content_type + "; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path != "/__api" or self.headers.get("Content-Type") != "application/json":
                    self.send_error(400)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 1000000:
                        raise ValueError()
                    request = json.loads(self.rfile.read(size))
                    if request["method"] not in METHODS:
                        raise ValueError()
                    result = getattr(app, request["method"])(*request["args"])
                    body = json.dumps(result, ensure_ascii=False).encode()
                except (KeyError, TypeError, ValueError):
                    self.send_error(400)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 9011), Handler)
        print("界面验收服务：http://127.0.0.1:9011（仅模拟模型）", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            app.shutdown()


if __name__ == "__main__":
    main()
