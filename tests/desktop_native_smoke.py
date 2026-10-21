"""在真实 WebView2 中检查 Python 桥接，不使用真实模型或用户会话。"""

import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time

import webview

from desktop_app.service import DesktopService
from tests.test_desktop_service import simulated_worker


ROOT = Path(__file__).resolve().parents[1]


def main():
    os.environ["MODEL_ID"] = "test-model"
    result = {"ok": False}
    with tempfile.TemporaryDirectory() as temporary:
        app = DesktopService(Path(temporary), Path(temporary) / "sessions", worker_target=simulated_worker)
        window = webview.create_window("桌面界面验收", str(ROOT / "frontend/dist/index.html"),
                                       js_api=app, width=1100, height=760, hidden=True, text_select=True)
        app._window = window

        def wait_for(script):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                value = window.evaluate_js(script)
                if value:
                    return value
                time.sleep(0.1)
            raise AssertionError(f"界面状态未出现：{script}")

        def check():
            try:
                wait_for("document.querySelector('.welcome h1')?.textContent")
                result["stage"] = "页面已载入"
                methods = window.evaluate_js("Object.keys(window.pywebview.api)")
                result["methods"] = methods
                assert all(method in methods for method in ("send_message", "resolve_permission", "retry_run", "set_font_size", "delete_session", "delete_project", "rename_project", "rename_session", "pick_attachments"))
                assert "continue_run" not in methods
                deadline = time.monotonic() + 10
                while not app._sessions and time.monotonic() < deadline:
                    time.sleep(0.1)
                if not app._sessions:
                    raise AssertionError(window.evaluate_js("document.querySelector('.error-banner')?.textContent"))
                sid = next(iter(app._sessions))
                window.run_js(f"window.pywebview.api.send_message('{sid}', '等待审批').then(r => window.__bridge_result = r)")
                response = wait_for("window.__bridge_result")
                result["stage"] = "已提交任务"
                assert response["ok"], response
                wait_for("document.querySelector('#permission-title')?.textContent === '需要你的确认'")
                result["stage"] = "已显示审批"
                window.run_js(f"window.pywebview.api.resolve_permission('{sid}', 'approval_1', false).then(r => window.__approval_result = r)")
                response = wait_for("window.__approval_result")
                assert response["ok"], response
                wait_for("document.querySelector('.status-pill')?.textContent.includes('已完成')")
                result["stage"] = "任务已完成"
                assert window.evaluate_js("document.querySelector('.execution-toggle').getAttribute('aria-expanded') === 'false'")
                assert window.evaluate_js("document.querySelectorAll('.tool-card').length === 0")
                window.run_js("document.querySelector('.execution-toggle').click()")
                wait_for("document.querySelectorAll('.tool-card').length === 1")
                window.run_js(f"window.pywebview.api.send_message('{sid}', '用量验收')")
                wait_for("document.querySelector('.usage-details summary')?.textContent.includes('次请求') && Boolean(document.querySelector('.status-pill.completed'))")
                usage_calls = app.get_session(sid)["data"]["usage"]["calls"]
                assert usage_calls >= 67
                assert window.evaluate_js("!document.querySelector('.usage-warning, .usage-limit, select[aria-label=\"执行额度\"]')")
                assert window.evaluate_js("document.querySelector('.model-badge').textContent.includes('test-model')")
                window.run_js("""const p = document.querySelector('.assistant-message .markdown p');
                    const range = document.createRange(); range.selectNodeContents(p);
                    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
                    p.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, button: 0, clientX: 200, clientY: 200}));""")
                wait_for("Boolean(document.querySelector('.selection-reply button'))")
                window.run_js("document.querySelector('.selection-reply button').click()")
                wait_for("document.querySelector('.composer-quote blockquote')?.textContent.includes('处理完成')")
                window.run_js("document.querySelector('button[aria-label=\"移除引用\"]').click()")
                window.run_js("document.querySelector('button[aria-label=\"复制回复\"]').click()")
                wait_for("Boolean(document.querySelector('.message-actions .lucide-check'))")
                window.run_js("document.querySelector('.settings-button').click()")
                wait_for("Boolean(document.querySelector('.settings-dialog[open]'))")
                window.run_js("const size = document.querySelector('#font-size'); size.value = '19'; size.dispatchEvent(new Event('change', {bubbles: true}))")
                wait_for("getComputedStyle(document.querySelector('.markdown')).fontSize === '19px'")
                assert app.bootstrap()["data"]["settings"]["font_size"] == 19
                window.run_js("document.querySelector('input[name=theme][value=dark]').click()")
                wait_for("document.documentElement.dataset.theme === 'dark'")
                window.run_js("document.querySelector('input[name=theme][value=light]').click()")
                wait_for("document.documentElement.dataset.theme === 'light'")
                assert app.bootstrap()["data"]["settings"]["theme"] == "light"
                window.run_js("document.querySelector('button[aria-label=\"关闭设置\"]').click()")
                result["stage"] = "验证失败后继续"
                window.run_js(f"window.pywebview.api.send_message('{sid}', '失败后继续验收')")
                wait_for("Boolean(document.querySelector('.retry-panel button'))")
                failed = app.get_session(sid)["data"]
                assert failed["failure"]["retryable"] and failed["usage"]["calls"] == 2
                window.run_js("document.querySelector('.retry-panel button').click()")
                wait_for("document.querySelector('.status-pill')?.classList.contains('completed') && document.querySelector('.usage-details summary')?.textContent.includes('4 次请求')")
                done = app.get_session(sid)["data"]
                assert done["usage"]["run_id"] == failed["usage"]["run_id"]
                assert done["usage_total"]["calls"] == usage_calls + 4
                assert done["failure"] is None
                assert window.evaluate_js("document.querySelectorAll('.user-message').length === 3")
                result["stage"] = "验证文件引用和重命名"
                reference = Path(temporary) / "参考.txt"
                reference.write_text("模拟文件引用", encoding="utf-8")

                class NativePicker:
                    def create_file_dialog(self, *args, **kwargs):
                        return (str(reference if kwargs.get("allow_multiple") else Path(temporary)),)

                app._window = NativePicker()
                window.run_js("document.querySelector('.composer-add').click()")
                wait_for("document.querySelector('.attachment-menu')?.textContent.includes('添加文件夹')")
                window.run_js("document.querySelector('.attachment-menu button').click()")
                wait_for("document.querySelector('.composer .attachment-chip')?.textContent.includes('参考.txt')")
                app._window = window
                for kind, selector, title in (("project", ".current-project .row-options", "桌面验收项目"),
                                              ("session", ".session-entry.is-selected .row-options", "桌面验收会话")):
                    window.run_js(f"document.querySelector('{selector}').click()")
                    wait_for("Boolean(document.querySelector('.row-menu-content .rename-menu-item'))")
                    window.run_js("document.querySelector('.row-menu-content .rename-menu-item').click()")
                    wait_for("Boolean(document.querySelector('.rename-dialog[open]'))")
                    window.run_js(f"Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(document.querySelector('.rename-dialog[open] input'), '{title}'); document.querySelector('.rename-dialog[open] input').dispatchEvent(new Event('input', {{bubbles:true}}))")
                    window.run_js("document.querySelector('.rename-dialog[open] .dialog-save').click()")
                    wait_for("!document.querySelector('.rename-dialog[open]')")
                    wait_for(f"document.querySelector('{'.header-project' if kind == 'project' else '.conversation-title'}')?.textContent.includes('{title}')")
                result["banner"] = window.evaluate_js("document.querySelector('.error-banner')?.textContent")
                assert not window.evaluate_js("Boolean(document.querySelector('.error-banner'))")
                result.update(ok=True, message="真实 WebView2：纯用量统计、无额度和警告、失败继续、文件引用、重命名、字号、选中引用、复制、折叠记录、审批和主题均通过。")
            except Exception as error:
                result["error"] = repr(error)
                result["selection_state"] = window.evaluate_js("({visibility: document.visibilityState, focus: document.activeElement?.tagName, text: window.getSelection()?.toString(), popup: Boolean(document.querySelector('.selection-reply'))})")
            finally:
                app.shutdown()
                window.destroy()

        webview.start(check, gui="edgechromium", http_server=True)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
