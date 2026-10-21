let ready;

function desktopReady() {
  if (typeof window.pywebview?.api?.bootstrap === 'function') return Promise.resolve();
  if (!ready) {
    ready = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        ready = undefined;
        reject(new Error('尚未连接桌面程序，请通过 desktop.py 打开窗口。'));
      }, 10000);
      window.addEventListener('pywebviewready', () => {
        clearTimeout(timeout);
        resolve();
      }, { once: true });
    });
  }
  return ready;
}

export async function call(method, ...args) {
  await desktopReady();
  const response = await window.pywebview.api[method](...args);
  if (!response?.ok) throw new Error(response?.error || '操作失败，请重试。');
  return response.data;
}
