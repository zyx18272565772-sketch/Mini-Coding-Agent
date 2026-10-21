/* 项目树与主题切换的浏览器验收，后端使用临时数据和模拟模型。 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const out = path.resolve(__dirname, '../.agent_data/ui-checks');
  fs.mkdirSync(out, { recursive: true });
  try {
    await page.goto('http://127.0.0.1:9011/');
    await page.getByRole('button', { name: '创建项目', exact: true }).click();
    await page.getByRole('heading', { name: '今天想完成什么？' }).waitFor();
    await page.waitForFunction(() => !document.querySelector('.new-session').disabled);
    const project = page.locator('.project-group:has(.current-project)');
    const name = (await project.locator('.project-toggle').innerText()).trim();
    assert.ok(name);
    assert.notEqual(name, 'my coding agent', '创建项目后应切换到新选目录的项目');
    const count = await project.locator('.session-item').count();
    await project.getByRole('button', { name: `在 ${name} 中新建会话`, exact: true }).click();
    await page.waitForFunction(expected => document.querySelector('.project-group:has(.current-project)').querySelectorAll('.session-item').length === expected, count + 1);
    assert.equal(await page.locator('.user-message').count(), 0);
    const title = `项目会话 ${Date.now()}`;
    await page.getByRole('textbox', { name: '任务内容' }).fill(title);
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
    await page.getByText('本轮任务已结束', { exact: true }).waitFor();
    await page.getByRole('textbox', { name: '任务内容' }).fill('主题切换后仍然保留的草稿');

    await page.getByRole('button', { name: '设置', exact: true }).click();
    const settings = page.getByRole('dialog', { name: '设置', exact: true });
    await settings.waitFor();
    await settings.getByText('浅色', { exact: true }).click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    assert.equal(await page.locator('.composer').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 255, 255)');
    assert.equal(await page.locator('.sidebar').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(247, 247, 248)');
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.session-item.selected')).backgroundColor === 'rgb(233, 233, 235)');
    await page.screenshot({ path: path.join(out, 'settings-light.png') });
    await page.keyboard.press('Escape');
    await settings.waitFor({ state: 'hidden' });
    assert.equal(await page.getByRole('textbox', { name: '任务内容' }).inputValue(), '主题切换后仍然保留的草稿');
    await page.screenshot({ path: path.join(out, 'projects-light.png') });

    await page.reload();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await page.getByRole('button', { name: title, exact: true }).waitFor();
    await project.locator('.project-toggle').click();
    assert.equal(await project.locator('.project-toggle').getAttribute('aria-expanded'), 'false');
    assert.equal(await project.locator('.session-item').count(), 0);
    await project.locator('.project-toggle').click();
    await page.getByRole('button', { name: title, exact: true }).waitFor();
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await settings.getByText('深色', { exact: true }).click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    await page.screenshot({ path: path.join(out, 'settings-dark.png') });
    await page.getByRole('button', { name: '关闭设置', exact: true }).click();
    await page.screenshot({ path: path.join(out, 'projects-dark.png') });

    await page.setViewportSize({ width: 320, height: 800 });
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await settings.waitFor();
    const bounds = await settings.boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 320, '窄窗口中的设置面板不能超出屏幕');
    await page.screenshot({ path: path.join(out, 'settings-narrow.png') });
    assert.deepEqual(errors, []);
    console.log('项目与主题验收通过：创建项目、多会话、折叠展开、设置面板、深浅主题、刷新恢复、草稿保留和窄窗口。');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
