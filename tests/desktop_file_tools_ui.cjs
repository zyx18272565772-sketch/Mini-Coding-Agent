/* 模拟搜索事件，使用真实浏览器验收中文工具卡片，不调用模型。 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const events = [
      { seq: 1, type: 'user', text: '查找登录函数' },
      { seq: 2, type: 'tool_start', scope: 'main', call_id: 'search_demo', name: 'search_files', args: { query: 'def login', file_glob: '**/*.py' } },
      { seq: 3, type: 'tool_result', scope: 'main', call_id: 'search_demo', name: 'search_files', output: 'app/auth.py:42: def login(username):', allowed: true },
      { seq: 4, type: 'assistant', scope: 'main', text: '已定位登录函数。' },
    ];
    await page.route('**/__api', async route => {
      const request = route.request().postDataJSON();
      const response = await route.fetch();
      const result = await response.json();
      if (result.ok && ['get_session', 'poll'].includes(request.method)) {
        result.data.status = 'completed';
        result.data.events = events.filter(event => request.method === 'get_session' || event.seq > (request.args[1] || 0));
        result.data.cursor = 4;
      }
      await route.fulfill({ response, json: result });
    });
    await page.goto('http://127.0.0.1:9011/');
    await page.locator('.execution-toggle').waitFor();
    assert.equal(await page.locator('.tool-card').count(), 0);
    await page.locator('.execution-toggle').click();
    const toggle = page.getByRole('button', { name: /搜索文件内容/ });
    await toggle.waitFor();
    assert.match(await toggle.innerText(), /def login/);
    assert.equal(await page.locator('.tool-details').count(), 0);
    await toggle.click();
    await page.locator('.tool-details').getByText('app/auth.py:42: def login(username):', { exact: true }).waitFor();
    assert.deepEqual(errors, []);
    console.log('搜索工具卡片验收通过：中文名称、关键词预览、默认折叠及展开结果。');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
