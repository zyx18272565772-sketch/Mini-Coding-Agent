import test from 'node:test';
import assert from 'node:assert/strict';
import { call } from './bridge.js';

test('WebView2 创建空 api 对象后，必须等到桥接方法注册完成才能调用', async () => {
  global.window = new EventTarget();
  window.pywebview = { api: {} };
  let settled = false;
  const pending = call('bootstrap').then(data => {
    settled = true;
    return { data };
  }, error => {
    settled = true;
    return { error: error.message };
  });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(settled, false, '不能把空 api 对象误判为已连接');
  window.pywebview.api.bootstrap = async () => ({ ok: true, data: { sessions: [] } });
  window.dispatchEvent(new Event('pywebviewready'));
  assert.deepEqual(await pending, { data: { sessions: [] } });
});
