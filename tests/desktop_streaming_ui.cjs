/* 模拟工作进程分段输出，在真实浏览器检查增量显示、停止和恢复。 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const out = path.resolve(__dirname, '../.agent_data/ui-checks');
  fs.mkdirSync(out, { recursive: true });
  try {
    await page.goto('http://127.0.0.1:9011/');
    await page.getByRole('button', { name: '新建会话', exact: true }).click();
    await page.getByRole('heading', { name: '今天想完成什么？' }).waitFor();
    await page.getByRole('textbox', { name: '任务内容' }).fill('流式输出验收');
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
    const active = page.locator('.assistant-message[data-streaming="true"]');
    await active.getByText('正在审查项目。', { exact: true }).waitFor();
    assert.equal(await active.count(), 1);
    assert.equal(await page.getByText('审查完成。', { exact: true }).count(), 0);
    await page.screenshot({ path: path.join(out, 'streaming-partial.png') });
    await page.getByText('本轮任务已结束', { exact: true }).waitFor();
    assert.equal(await page.locator('.assistant-message').count(), 1);
    assert.equal(await active.count(), 0);
    await page.getByText('审查完成。', { exact: true }).waitFor();
    assert.equal(await page.locator('.assistant-message .markdown li').count(), 2);
    await page.screenshot({ path: path.join(out, 'streaming-complete.png') });

    await page.reload();
    await page.getByText('审查完成。', { exact: true }).waitFor();
    assert.equal(await page.locator('.assistant-message').count(), 1);
    await page.getByRole('textbox', { name: '任务内容' }).fill('流式输出等待停止');
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
    await active.getByText('正在审查项目。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '停止任务', exact: true }).click();
    await page.getByText('任务已停止', { exact: true }).waitFor();
    assert.equal(await active.count(), 0);
    assert.equal(await page.locator('.assistant-message').count(), 2);
    await page.getByText('输出已中断', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(out, 'streaming-stopped.png') });
    await page.reload();
    await page.getByText('输出已中断', { exact: true }).waitFor();
    assert.equal(await active.count(), 0);
    assert.equal(await page.locator('.assistant-message').count(), 2);
    assert.deepEqual(errors, []);
    console.log('流式界面验收通过：逐段显示、单条回复、Markdown、恢复、停止保留部分正文、无控制台错误。');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
