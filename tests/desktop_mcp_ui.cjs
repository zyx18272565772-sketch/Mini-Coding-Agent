/* 真实浏览器 + 模拟 MCP 事件，验证中文卡片、折叠和远程错误显示。 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const events = [
      { seq: 1, type: 'user', text: '查询 GitHub Issue 和 PR' },
      { seq: 2, type: 'tool_start', scope: 'main', call_id: 'issues', name: 'mcp__github__list_issues', args: { owner: 'someone', repo: 'demo' } },
      { seq: 3, type: 'tool_result', scope: 'main', call_id: 'issues', name: 'mcp__github__list_issues', output: 'Issue #1: 示例问题', allowed: true, is_error: false },
      { seq: 4, type: 'tool_start', scope: 'subagent', call_id: 'pr', name: 'mcp__github__pull_request_read', args: { owner: 'someone', repo: 'demo', pullNumber: 1 } },
      { seq: 5, type: 'tool_result', scope: 'subagent', call_id: 'pr', name: 'mcp__github__pull_request_read', output: '错误：远程权限不足', allowed: true, is_error: true },
      { seq: 6, type: 'assistant', scope: 'main', text: '已读取 Issue，PR 查询失败。' },
    ];
    await page.route('**/__api', async route => {
      const request = route.request().postDataJSON();
      const response = await route.fetch();
      const result = await response.json();
      if (result.ok && ['get_session', 'poll'].includes(request.method)) {
        result.data.status = 'completed';
        result.data.events = events.filter(event => request.method === 'get_session' || event.seq > (request.args[1] || 0));
        result.data.cursor = 6;
      }
      await route.fulfill({ response, json: result });
    });
    await page.goto('http://127.0.0.1:9011/');
    await page.locator('.execution-toggle').waitFor();
    assert.equal(await page.locator('.tool-card').count(), 0);
    await page.locator('.execution-toggle').click();
    const issues = page.getByRole('button', { name: /GitHub MCP · 列出 Issue/ });
    await issues.waitFor();
    assert.match(await issues.innerText(), /someone\/demo/);
    assert.equal(await page.locator('.tool-details').count(), 0);
    await issues.click();
    await page.getByText('Issue #1: 示例问题', { exact: true }).waitFor();
    const failed = page.locator('.tool-failed');
    assert.match(await failed.innerText(), /GitHub MCP · 读取 PR/);
    assert.match(await failed.innerText(), /执行失败/);
    assert.match(await failed.innerText(), /子智能体/);
    await failed.locator('.tool-toggle').click();
    await page.getByText('错误：远程权限不足', { exact: true }).waitFor();
    assert.deepEqual(errors, []);
    console.log('GitHub MCP 界面验收通过：中文名称、仓库、折叠展开、子 Agent 与执行失败状态。');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
