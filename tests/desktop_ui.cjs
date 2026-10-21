/* 在独立的无头浏览器中验收桌面页面，不连接用户现有的浏览器配置。 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 });
  const failures = [];
  page.on('pageerror', error => failures.push(error.message));
  page.on('console', message => { if (message.type() === 'error') failures.push(message.text()); });
  const out = path.resolve(__dirname, '../.agent_data/ui-checks');
  const taskTitle = `界面验收 ${Date.now()}`;
  fs.mkdirSync(out, { recursive: true });
  try {
    await page.goto('http://127.0.0.1:9011/');
    await page.getByRole('button', { name: '新建会话', exact: true }).click();
    await page.getByRole('heading', { name: '今天想完成什么？' }).waitFor();
    await page.screenshot({ path: path.join(out, 'empty.png') });
    await page.getByRole('button', { name: '了解这个项目' }).click();
    assert.match(await page.getByRole('textbox', { name: '任务内容' }).inputValue(), /目录结构/);
    await page.getByRole('textbox', { name: '任务内容' }).fill(taskTitle);
    await page.getByRole('textbox', { name: '任务内容' }).press('Shift+Enter');
    assert.equal(await page.locator('.user-message').count(), 0);
    await page.getByRole('textbox', { name: '任务内容' }).press('Enter');
    await page.getByText('本轮任务已结束', { exact: true }).waitFor();
    assert.equal(await page.locator('.user-message').count(), 1);
    assert.equal(await page.getByRole('textbox', { name: '任务内容' }).inputValue(), '');

    await page.getByRole('textbox', { name: '任务内容' }).fill('等待审批');
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
    await page.getByText('需要你的确认', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(out, 'permission.png') });
    await page.getByRole('button', { name: '拒绝', exact: true }).click();
    await page.getByText('本轮任务已结束', { exact: true }).waitFor();
    await page.locator('.execution-toggle').click();
    await page.getByRole('button', { name: /执行命令/ }).click();
    await page.getByText('权限被拒绝。', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(out, 'conversation.png') });

    await page.getByRole('button', { name: '新建会话', exact: true }).click();
    await page.getByRole('heading', { name: '今天想完成什么？' }).waitFor();
    assert.equal(await page.locator('.user-message').count(), 0);
    await page.getByRole('button', { name: taskTitle, exact: true }).click();
    await page.locator('.execution-toggle').click();
    await page.getByRole('button', { name: /执行命令/ }).click();
    await page.getByText('权限被拒绝。', { exact: true }).waitFor();
    await page.reload();
    await page.getByRole('button', { name: taskTitle, exact: true }).click();
    await page.locator('.execution-toggle').click();
    await page.getByRole('button', { name: /执行命令/ }).click();
    await page.getByText('权限被拒绝。', { exact: true }).waitFor();

    await page.getByRole('textbox', { name: '任务内容' }).fill('一直运行');
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
    await page.getByRole('button', { name: '停止任务' }).waitFor();
    await page.getByRole('button', { name: '停止任务' }).click();
    await page.getByText('任务已停止', { exact: true }).waitFor();
    await page.getByRole('button', { name: '发送任务', exact: true }).waitFor();
    await page.locator('.workspace-button').click();
    await page.waitForFunction(() => !document.querySelector('.workspace-button').title.includes('my coding agent'));

    for (const width of [320, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 800 });
      if (width === 320) {
        await page.getByRole('button', { name: '收起侧栏' }).click();
        await page.getByRole('button', { name: '展开侧栏' }).click();
        await page.getByRole('button', { name: '关闭会话导航' }).click();
      }
      const dimensions = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth }));
      assert.ok(dimensions.scroll <= dimensions.viewport, `窗口 ${width}px 不应横向溢出`);
      await page.screenshot({ path: path.join(out, `width-${width}.png`) });
    }
    assert.deepEqual(failures, [], '页面不能有 JavaScript 或控制台错误');
    console.log('界面验收通过：发送、换行、工具详情、审批拒绝、会话切换、刷新恢复、停止、工作目录、四种窗口尺寸。');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
