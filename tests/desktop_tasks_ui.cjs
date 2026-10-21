/* 项目任务清单：实际文件状态 + 模拟执行，验证依赖、共享、恢复和主题。 */
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
  try {
    await page.goto('http://127.0.0.1:9011/');
    await page.getByRole('button', { name: '创建项目', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.new-session').disabled && !document.querySelector('.current-project .project-toggle').innerText.includes('my coding agent'));
    await page.getByRole('heading', { name: '今天想完成什么？' }).waitFor();
    await page.getByRole('textbox', { name: '任务内容' }).fill('任务系统验收');
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
    const panel = page.locator('.project-tasks');
    await panel.locator('summary').click();
    const list = panel.getByRole('list', { name: '项目任务清单' });
    await list.getByText('等待依赖', { exact: true }).waitFor();
    await list.getByText('进行中', { exact: true }).waitFor();
    assert.equal(await list.locator('li').count(), 2);
    await page.screenshot({ path: path.join(out, 'tasks-blocked.png') });
    await page.getByText('本轮任务已结束', { exact: true }).waitFor();
    await panel.getByText('2/2 已完成', { exact: true }).waitFor();
    assert.equal(await list.getByText('已完成', { exact: true }).count(), 2);
    await page.getByRole('button', { name: /设置任务依赖/ }).click();
    await page.getByText('update_task · 参数', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(out, 'tasks-completed.png') });
    await page.getByRole('button', { name: '新建会话', exact: false }).filter({ hasText: '新建会话' }).first().click();
    await page.getByRole('heading', { name: '今天想完成什么？' }).waitFor();
    await panel.getByText('2/2 已完成', { exact: true }).waitFor();
    await panel.locator('summary').focus();
    await page.keyboard.press('Enter');
    await list.waitFor();
    assert.equal(await list.locator('li').count(), 2);
    await page.reload();
    await panel.getByText('2/2 已完成', { exact: true }).waitFor();
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await page.getByRole('dialog', { name: '设置', exact: true }).getByText('浅色', { exact: true }).click();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await panel.locator('summary').click();
    await page.screenshot({ path: path.join(out, 'tasks-light.png') });
    await page.getByRole('button', { name: '收起侧栏', exact: true }).click();
    for (const width of [320, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `宽度 ${width} 不能横向溢出`);
    }
    await page.setViewportSize({ width: 320, height: 900 });
    await page.screenshot({ path: path.join(out, 'tasks-mobile.png') });
    assert.deepEqual(errors, []);
    console.log('任务界面验收通过：依赖状态、完成更新、工具详情、同项目新会话共享、刷新恢复、主题、键盘和窄窗口。');
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'tasks-failure.png') });
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
