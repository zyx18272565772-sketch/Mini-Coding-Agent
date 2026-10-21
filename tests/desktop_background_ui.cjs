/* 真实桌面工作进程 + 模拟模型，验证后台等待、自动继续、失败和停止。 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const out = path.resolve(__dirname, '../.agent_data/ui-checks');
  fs.mkdirSync(out, { recursive: true });
  async function send(text) {
    await page.getByRole('textbox', { name: '任务内容' }).fill(text);
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
  }
  async function newSession() {
    await page.locator('.new-session').click();
    await page.getByRole('heading', { name: '今天想完成什么？' }).waitFor();
  }
  try {
    await page.goto('http://127.0.0.1:9011/');
    await page.getByRole('button', { name: '创建项目', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.new-session').disabled && !document.querySelector('.current-project .project-toggle').innerText.includes('my coding agent'));
    await send('后台执行验收');
    await page.locator('.execution-toggle').click();
    await page.locator('.tool-status.background').getByText('后台运行中', { exact: true }).waitFor();
    await page.locator('.thinking').getByText('正在等待后台任务完成…', { exact: true }).waitFor();
    assert.equal(await page.getByText('本轮任务已结束', { exact: true }).count(), 0);
    await page.getByRole('button', { name: '停止任务', exact: true }).waitFor();
    await page.screenshot({ path: path.join(out, 'background-waiting.png') });
    await page.getByText('本轮任务已结束', { exact: true }).waitFor();
    assert.equal(await page.locator('.tool-card').count(), 1);
    assert.equal(await page.locator('.tool-status.background').count(), 0);
    assert.equal(await page.locator('.user-message').count(), 1);
    await page.getByRole('button', { name: /执行命令/ }).click();
    await page.getByText(/后台任务：bg_.*退出码：0/).waitFor();
    await page.locator('.tool-details pre').last().filter({ hasText: 'background-ok' }).waitFor();
    await page.screenshot({ path: path.join(out, 'background-completed.png') });
    await page.reload();
    await page.getByText('本轮任务已结束', { exact: true }).waitFor();
    await page.locator('.execution-toggle').click();
    assert.equal(await page.locator('.tool-card').count(), 1);
    await page.getByRole('button', { name: /执行命令/ }).click();
    await page.locator('.tool-details pre').last().filter({ hasText: 'background-ok' }).waitFor();

    await newSession();
    await send('后台执行失败验收');
    await page.getByText('本轮任务已结束', { exact: true }).waitFor();
    await page.locator('.execution-toggle').click();
    await page.locator('.tool-status.failed').getByText('执行失败', { exact: true }).waitFor();
    await page.getByRole('button', { name: /执行命令/ }).click();
    await page.getByText(/退出码：3/).waitFor();
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await page.getByRole('dialog', { name: '设置', exact: true }).getByText('浅色', { exact: true }).click();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await page.screenshot({ path: path.join(out, 'background-failed-light.png') });

    await newSession();
    await send('后台执行等待停止');
    await page.locator('.execution-toggle').click();
    await page.locator('.thinking').getByText('正在等待后台任务完成…', { exact: true }).waitFor();
    await page.getByRole('button', { name: '停止任务', exact: true }).click();
    await page.getByText('任务已停止', { exact: true }).waitFor();
    assert.equal(await page.locator('.tool-status.background').count(), 0);
    await page.locator('.tool-status.stopped').getByText('已停止', { exact: true }).waitFor();
    await page.getByRole('button', { name: '收起侧栏', exact: true }).click();
    for (const width of [320, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `宽度 ${width} 不应横向溢出`);
    }
    await page.setViewportSize({ width: 320, height: 900 });
    await page.screenshot({ path: path.join(out, 'background-stopped-mobile.png') });
    assert.deepEqual(errors, []);
    console.log('后台界面验收通过：等待期间保留停止按钮、完成自动继续、单条工具卡片更新、刷新恢复、失败状态、主题与窄窗口。');
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'background-failure.png') });
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
