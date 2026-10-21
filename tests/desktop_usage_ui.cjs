/* 真实桌面工作进程使用模拟 API，验证只统计用量和执行记录折叠。 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const out = path.resolve(__dirname, '../.agent_data/ui-checks');
  fs.mkdirSync(out, { recursive: true });
  const text = page.getByRole('textbox', { name: '任务内容' });
  async function send(task) {
    await text.fill(task);
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
  }
  try {
    await page.goto('http://127.0.0.1:9011/');
    await page.getByRole('button', { name: '创建项目', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.new-session').disabled && !document.querySelector('.current-project .project-toggle').innerText.includes('my coding agent'));
    assert.equal(await page.getByRole('combobox', { name: '执行额度', exact: true }).count(), 0);
    await send('用量验收');
    await page.locator('.status-pill.completed').waitFor();
    const usage = await page.evaluate(async () => {
      const nav = (await window.pywebview.api.bootstrap()).data;
      return (await window.pywebview.api.get_session(nav.sessions[0].id)).data.usage;
    });
    assert.ok(usage.calls >= 67, '应能超过原来的 60 次请求上限');
    assert.equal('limits' in usage, false);
    assert.equal('warning' in usage, false);
    assert.equal(await page.locator('.usage-warning, .usage-limit').count(), 0);
    assert.equal(await page.locator('.execution-toggle').count(), 1);
    assert.equal(await page.locator('.execution-toggle').getAttribute('aria-expanded'), 'false');
    assert.equal(await page.locator('.tool-card').count(), 0);
    await page.locator('.execution-toggle').getByText('65 次工具调用', { exact: true }).waitFor();
    await page.locator('.usage-details summary').getByText(`本次任务 ${usage.calls} 次请求`, { exact: true }).waitFor();
    await page.screenshot({ path: path.join(out, 'usage-folded.png') });

    await page.locator('.execution-toggle').click();
    assert.equal(await page.locator('.tool-card').count(), 65);
    assert.equal(await page.locator('.tool-details').count(), 0);
    await page.locator('.tool-toggle').first().click();
    await page.locator('.tool-details pre').first().filter({ hasText: 'sample.txt' }).waitFor();
    await page.locator('.usage-details summary').click();
    const main = page.locator('.usage-details tbody tr').filter({ hasText: '主 Agent' });
    assert.equal(await main.locator('td').first().innerText(), '66');
    await page.screenshot({ path: path.join(out, 'usage-expanded.png') });
    await page.locator('.usage-details summary').click();
    await page.getByText('本轮任务已结束', { exact: true }).waitFor();
    assert.equal(await page.locator('.user-message').count(), 1);
    await page.locator('.usage-session-total').filter({ hasText: `会话累计 ${usage.calls} 次` }).waitFor();
    await page.reload();
    await page.getByText('本轮任务已结束', { exact: true }).waitFor();
    await page.locator('.usage-details summary').getByText(`本次任务 ${usage.calls} 次请求`, { exact: true }).waitFor();
    assert.equal(await page.locator('.tool-card').count(), 0);
    assert.equal(await page.locator('.execution-toggle').getAttribute('aria-expanded'), 'false');

    await send('等待审批');
    await page.getByText('需要你的确认', { exact: true }).waitFor();
    assert.equal(await page.locator('.execution-toggle').count(), 2);
    assert.equal(await page.locator('.tool-card').count(), 0);
    await page.getByRole('button', { name: '拒绝', exact: true }).click();
    await page.getByText('本轮任务已结束', { exact: true }).waitFor();
    await page.locator('.execution-toggle').last().getByText('1 项异常或未执行', { exact: true }).waitFor();

    await page.getByRole('button', { name: '设置', exact: true }).click();
    await page.getByRole('dialog', { name: '设置', exact: true }).getByText('浅色', { exact: true }).click();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await page.getByRole('button', { name: '收起侧栏', exact: true }).click();
    for (const width of [320, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `窗口 ${width}px 不应横向溢出`);
      assert.ok(await text.isVisible());
    }
    await page.setViewportSize({ width: 320, height: 900 });
    await page.screenshot({ path: path.join(out, 'usage-light-mobile.png') });
    assert.deepEqual(errors, []);
    console.log('用量界面验收通过：超过 60 次请求、纯用量统计、无额度档位或警告、默认折叠、保存恢复、审批及窄窗口。');
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'usage-failure.png') });
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
