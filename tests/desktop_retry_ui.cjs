/* 临时项目、模拟 API：失败后手动继续，保留进度和累计用量。 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const out = path.resolve(__dirname, '../.agent_data/ui-checks');
  fs.mkdirSync(out, { recursive: true });
  const input = page.getByRole('textbox', { name: '任务内容' });
  const retry = page.getByRole('button', { name: '继续执行', exact: true });
  async function send(text) {
    await input.fill(text);
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
  }
  async function snapshot() {
    return page.evaluate(async () => {
      const nav = await window.pywebview.api.bootstrap();
      return (await window.pywebview.api.get_session(nav.data.sessions[0].id)).data;
    });
  }
  try {
    await page.goto('http://127.0.0.1:9011/');
    await page.getByRole('button', { name: '创建项目', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.new-session').disabled && !document.querySelector('.current-project .project-toggle').innerText.includes('my coding agent'));
    await send('失败后继续验收');
    await retry.waitFor();
    await page.locator('.usage-details summary').getByText('本次任务 2 次请求', { exact: true }).waitFor();
    assert.equal(await page.locator('.user-message').count(), 1);
    assert.equal(await page.locator('.execution-toggle').count(), 1);
    await page.screenshot({ path: path.join(out, 'retry-error.png') });
    const failed = await snapshot();
    assert.equal(failed.failure.retryable, true);
    await page.reload();
    await retry.waitFor();
    await input.fill('还没发送的草稿');
    await retry.click();
    await page.locator('.status-pill.completed').waitFor();
    await page.getByText('已从保存的进度继续完成。', { exact: true }).waitFor();
    assert.equal(await input.inputValue(), '还没发送的草稿');
    assert.equal(await retry.count(), 0);
    assert.equal(await page.locator('.user-message').count(), 1);
    assert.equal(await page.locator('.execution-toggle').count(), 1);
    const done = await snapshot();
    assert.equal(done.usage.run_id, failed.usage.run_id);
    assert.equal(done.usage.calls, 4);
    assert.equal(done.usage_total.calls, 4);
    assert.equal(done.usage.unknown_calls, 1);
    assert.equal('limits' in done.usage, false);
    assert.equal(done.events.filter(event => event.type === 'tool_start').length, 1);
    await page.screenshot({ path: path.join(out, 'retry-completed.png') });

    await send('失败后继续：断流');
    await retry.waitFor();
    await page.getByText('已生成的部分说明', { exact: true }).waitFor();
    await page.getByText('输出已中断', { exact: true }).waitFor();
    await retry.click();
    await page.locator('.status-pill.completed').waitFor();
    assert.equal(await page.getByText('已生成的部分说明', { exact: true }).count(), 1);
    assert.equal(await page.locator('.user-message').count(), 2);

    await send('失败后继续：认证');
    await page.locator('.status-pill.error').waitFor();
    assert.equal(await retry.count(), 0);
    await page.locator('.inline-error').last().filter({ hasText: 'HTTP 401' }).waitFor();

    await send('失败后继续：持续失败');
    await retry.waitFor();
    const firstFailure = await snapshot();
    await retry.click();
    await retry.waitFor();
    await page.locator('.usage-details summary').getByText('本次任务 2 次请求', { exact: true }).waitFor();
    const secondFailure = await snapshot();
    assert.equal(secondFailure.usage.run_id, firstFailure.usage.run_id);
    assert.equal(secondFailure.usage.calls, 2);
    assert.equal(await page.locator('.user-message').count(), 4);

    await page.getByRole('button', { name: '设置', exact: true }).click();
    await page.getByRole('dialog', { name: '设置', exact: true }).getByText('深色', { exact: true }).click();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    await page.getByRole('button', { name: '收起侧栏', exact: true }).click();
    await page.setViewportSize({ width: 320, height: 740 });
    assert.ok(await retry.isVisible());
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(out, 'retry-dark-mobile.png') });
    assert.deepEqual(errors, []);
    console.log('失败后继续验收通过：刷新恢复、同任务累计、保留草稿、工具不重放、断流文字保留、认证错误、重复失败、深色与窄窗口。');
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'retry-failure.png') });
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
