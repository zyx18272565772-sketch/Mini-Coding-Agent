/* Codex 风格外观、选中回复和右键菜单；只操作模拟服务的临时记录。 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'] });
  const page = await context.newPage();
  const errors = [];
  const sends = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('request', request => { if (request.url().endsWith('/__api') && request.method() === 'POST') { const data = request.postDataJSON(); if (data.method === 'send_message') sends.push(data.args); } });
  const out = path.resolve(__dirname, '../.agent_data/ui-checks');
  fs.mkdirSync(out, { recursive: true });
  const input = page.getByRole('textbox', { name: '任务内容' });
  const taskTitle = `外观验收 ${Date.now()}`;
  const settings = page.getByRole('dialog', { name: '设置', exact: true });
  async function send(text) { await input.fill(text); await page.getByRole('button', { name: '发送任务', exact: true }).click(); }
  async function selectParagraph() {
    const paragraph = page.locator('.assistant-message .markdown > p').first();
    await paragraph.click({ clickCount: 3 });
    await page.getByRole('button', { name: '回复', exact: true }).waitFor();
    return (await page.evaluate(() => window.getSelection().toString())).trim();
  }
  try {
    await page.goto('http://127.0.0.1:9011/');
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await settings.getByText('浅色', { exact: true }).click();
    await settings.getByRole('combobox', { name: '字体大小', exact: true }).selectOption('15');
    await page.getByRole('button', { name: '关闭设置', exact: true }).click();
    await settings.waitFor({ state: 'hidden' });
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light' && !document.querySelector('.new-session').disabled);
    await page.getByRole('button', { name: '创建项目', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.new-session').disabled && !document.querySelector('.current-project .project-toggle').innerText.includes('my coding agent'));
    const project = page.locator('.project-group:has(.current-project)');
    const projectName = (await project.locator('.project-toggle').innerText()).trim();
    await send(taskTitle);
    await page.locator('.status-pill.completed').waitFor();
    assert.equal(await page.locator('.main-panel').evaluate(el => getComputedStyle(el).backgroundColor), 'rgba(0, 0, 0, 0)');
    assert.equal(await page.locator('body').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 255, 255)');
    await page.locator('.model-badge').getByText('deepseek-test', { exact: true }).waitFor();
    assert.equal(await page.locator('.markdown table tbody tr').count(), 2);
    assert.ok(await page.locator('.hljs-keyword').count() > 0);
    assert.equal(await page.locator('.tool-card').count(), 0);
    await page.getByRole('button', { name: '复制代码', exact: true }).click();
    assert.match(await page.evaluate(() => navigator.clipboard.readText()), /def main\(\):/);
    await page.screenshot({ path: path.join(out, 'codex-light.png') });

    const selected = await selectParagraph();
    await page.locator('.assistant-message .markdown > p').first().click({ button: 'right' });
    await page.getByRole('menuitem', { name: /Copy/ }).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), selected);
    await page.locator('.assistant-message .markdown > p').first().click({ button: 'right' });
    await page.getByRole('menuitem', { name: /Select all/ }).click();
    assert.match(await page.evaluate(() => window.getSelection().toString()), /外观验收/);
    assert.match(await page.evaluate(() => window.getSelection().toString()), /修改说明/);
    await page.evaluate(() => window.getSelection().removeAllRanges());
    const quote = await selectParagraph();
    await page.getByRole('button', { name: '回复', exact: true }).click();
    assert.equal(await page.locator('.composer-quote blockquote').innerText(), quote);
    await page.getByRole('button', { name: '移除引用', exact: true }).click();
    assert.equal(await page.locator('.composer-quote').count(), 0);
    await selectParagraph();
    await page.getByRole('button', { name: '回复', exact: true }).click();
    await input.fill('详细解释这段内容');
    await page.getByRole('button', { name: '新建会话', exact: true }).click();
    await page.getByRole('heading', { name: '今天想完成什么？' }).waitFor();
    assert.equal(await page.locator('.composer-quote').count(), 0);
    await page.getByRole('button', { name: taskTitle, exact: true }).click();
    await page.locator('.composer-quote').waitFor();
    assert.equal(await input.inputValue(), '详细解释这段内容');
    await page.screenshot({ path: path.join(out, 'codex-quote.png') });
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.user-message').length === 2 && Boolean(document.querySelector('.status-pill.completed')));
    assert.match(sends.at(-1)[1], /引用的聊天内容（仅供参考）/);
    assert.ok(sends.at(-1)[1].includes(quote));
    assert.match(sends.at(-1)[1], /当前请求：\n详细解释这段内容$/);
    assert.equal(await page.locator('.user-quote').last().innerText(), quote);
    assert.equal(await page.locator('.composer-quote').count(), 0);

    await page.getByRole('button', { name: '设置', exact: true }).click();
    await settings.getByRole('combobox', { name: '字体大小', exact: true }).selectOption('19');
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.markdown')).fontSize === '19px');
    await settings.getByText('深色', { exact: true }).click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    await page.getByRole('button', { name: '关闭设置', exact: true }).click();
    await settings.waitFor({ state: 'hidden' });
    await page.screenshot({ path: path.join(out, 'codex-dark.png'), animations: 'disabled' });
    await page.reload();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark' && document.querySelector('.markdown') && getComputedStyle(document.querySelector('.markdown')).fontSize === '19px');
    assert.equal(await page.locator('.user-quote').last().innerText(), quote);
    assert.equal(await page.locator('.tool-card').count(), 0);

    await page.getByRole('button', { name: '设置', exact: true }).click();
    await settings.getByRole('combobox', { name: '字体大小', exact: true }).selectOption('22');
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.markdown')).fontSize === '22px');
    await page.getByRole('button', { name: '关闭设置', exact: true }).click();
    await settings.waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: '收起侧栏', exact: true }).click();
    await page.setViewportSize({ width: 320, height: 900 });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await selectParagraph();
    await page.locator('.assistant-message .markdown > p').first().click({ button: 'right' });
    const menuBounds = await page.getByRole('menu').boundingBox();
    assert.ok(menuBounds.x >= 0 && menuBounds.x + menuBounds.width <= 320);
    await page.keyboard.press('Escape');
    await page.evaluate(() => window.getSelection().removeAllRanges());
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('button', { name: '展开侧栏', exact: true }).click();

    await project.locator('.project-new').click();
    await page.getByRole('heading', { name: '今天想完成什么？' }).waitFor();
    await page.locator('.session-entry.is-selected .row-options').click();
    await page.getByRole('menuitem', { name: '删除会话', exact: true }).click();
    const deletion = page.getByRole('dialog', { name: '删除会话', exact: true });
    await deletion.getByRole('button', { name: '取消', exact: true }).click();
    await page.locator('.session-entry.is-selected .row-options').click();
    await page.getByRole('menuitem', { name: '删除会话', exact: true }).click();
    await deletion.getByRole('button', { name: '删除会话', exact: true }).click();
    await page.getByRole('button', { name: taskTitle, exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('.new-session').disabled);
    await project.locator('.project-row .row-options').click();
    await page.getByRole('menuitem', { name: '删除项目', exact: true }).click();
    const deleteProject = page.getByRole('dialog', { name: '删除项目', exact: true });
    await deleteProject.getByText(/源代码/).waitFor();
    await deleteProject.getByRole('button', { name: '删除项目', exact: true }).click();
    await page.waitForFunction(name => ![...document.querySelectorAll('.project-toggle')].some(el => el.innerText.trim() === name), projectName);
    await page.reload();
    await page.getByRole('heading', { name: '今天想完成什么？' }).waitFor();
    assert.equal(await page.getByRole('button', { name: taskTitle, exact: true }).count(), 0);

    await page.getByRole('button', { name: '设置', exact: true }).click();
    await settings.getByText('浅色', { exact: true }).click();
    await settings.getByRole('combobox', { name: '字体大小', exact: true }).selectOption('22');
    await page.getByRole('button', { name: '关闭设置', exact: true }).click();
    await settings.waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: '收起侧栏', exact: true }).click();
    for (const width of [320, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `字号 22px，宽度 ${width}px 不应溢出`);
      assert.ok(await input.isVisible());
    }
    await page.setViewportSize({ width: 320, height: 900 });
    await page.screenshot({ path: path.join(out, 'codex-mobile.png') });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('button', { name: '展开侧栏', exact: true }).click();
    await page.locator('.project-row .row-options').click();
    await page.getByRole('menuitem', { name: '删除项目', exact: true }).click();
    await page.getByRole('dialog', { name: '删除项目', exact: true }).getByRole('button', { name: '删除项目', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.project-group').length === 0);
    assert.equal(await input.isDisabled(), true);
    await page.reload();
    await page.getByRole('heading', { name: '今天想完成什么？' }).waitFor();
    assert.equal(await page.locator('.project-group').count(), 0);
    await page.getByRole('button', { name: '新建会话', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.project-group').length === 1);
    assert.equal(await input.isEnabled(), true);
    assert.deepEqual(errors, []);
    console.log('外观与交互验收通过：浅深主题、字号保存、模型、Markdown、代码复制、选中回复、右键复制/全选、引用发送、删除确认与恢复、窄窗口。');
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'codex-appearance-failure.png') });
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
