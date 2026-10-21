/* 临时项目：文件/文件夹引用、跨会话草稿与重命名的真实浏览器验收。 */
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
  const add = page.getByRole('button', { name: '添加文件或文件夹', exact: true });
  const input = page.getByRole('textbox', { name: '任务内容', exact: true });
  const sessionName = `文件审查 ${Date.now().toString().slice(-6)}`;
  async function attach(name) {
    await add.click();
    await page.getByRole('menuitem', { name, exact: true }).click();
    await page.locator('.composer .attachment-chip').filter({ hasText: name === '添加文件' ? '参考文件.txt' : '参考目录' }).waitFor();
  }
  async function rename(kind, value) {
    const selector = kind === '项目' ? '.current-project .row-options' : '.session-entry.is-selected .row-options';
    await page.locator(selector).click();
    await page.getByRole('menuitem', { name: `重命名${kind}`, exact: true }).click();
    const dialog = page.getByRole('dialog', { name: `重命名${kind}`, exact: true });
    await dialog.getByRole('textbox', { name: `${kind}名称`, exact: true }).fill(value);
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
  }
  try {
    await page.goto('http://127.0.0.1:9011/');
    await page.getByRole('button', { name: '创建项目', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.new-session').disabled && !document.querySelector('.current-project .project-toggle').innerText.includes('my coding agent'));
    const workdir = await page.locator('.workspace-button').getAttribute('title');
    await rename('项目', '我的开发项目');
    await page.locator('.header-project').filter({ hasText: '我的开发项目' }).waitFor();
    await page.locator('.welcome-project').filter({ hasText: '我的开发项目' }).waitFor();
    await page.locator('.workspace-button').filter({ hasText: '我的开发项目' }).waitFor();
    assert.equal(await page.locator('.workspace-button').getAttribute('title'), workdir);
    await rename('会话', sessionName);
    await page.locator('.conversation-title').filter({ hasText: sessionName }).waitFor();

    await page.locator('.session-entry.is-selected .row-options').click();
    await page.getByRole('menuitem', { name: '重命名会话', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '重命名会话', exact: true });
    await dialog.getByRole('textbox', { name: '会话名称', exact: true }).fill('   ');
    assert.ok(await dialog.getByRole('button', { name: '保存', exact: true }).isDisabled());
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });

    await input.fill('检查这些引用');
    await attach('添加文件');
    await attach('添加文件');
    assert.equal(await page.locator('.composer .attachment-chip').count(), 1);
    await attach('添加文件夹');
    assert.equal(await page.locator('.composer .attachment-chip').count(), 2);
    assert.equal(await input.inputValue(), '检查这些引用');
    assert.equal(await page.locator('.workspace-button').getAttribute('title'), workdir);
    await add.click();
    await page.screenshot({ path: path.join(out, 'composer-add-menu.png') });
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.evaluate(() => document.activeElement.textContent), '添加文件夹');
    await page.keyboard.press('Escape');
    assert.ok(await add.evaluate(element => element === document.activeElement));

    await page.getByRole('button', { name: '新建会话', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.conversation-title')?.textContent === '新会话');
    assert.equal(await page.locator('.composer .attachment-chip').count(), 0);
    await page.getByRole('button', { name: sessionName, exact: true }).click();
    await page.locator('.conversation-title').filter({ hasText: sessionName }).waitFor();
    assert.equal(await page.locator('.composer .attachment-chip').count(), 2);
    assert.equal(await input.inputValue(), '检查这些引用');
    await page.getByRole('button', { name: '移除引用 参考目录', exact: true }).click();
    assert.equal(await page.locator('.composer .attachment-chip').count(), 1);
    await attach('添加文件夹');
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
    await page.locator('.status-pill.completed').waitFor();
    assert.equal(await page.locator('.composer .attachment-chip').count(), 0);
    assert.equal(await page.locator('.user-message .attachment-chip').count(), 2);
    assert.equal(await page.locator('.conversation-title').innerText(), sessionName);
    const saved = await page.evaluate(async title => {
      const nav = (await window.pywebview.api.bootstrap()).data;
      return (await window.pywebview.api.get_session(nav.sessions.find(item => item.title === title).id)).data;
    }, sessionName);
    assert.ok(saved.events.find(item => item.type === 'user').text.includes('按需使用工具读取'));
    assert.ok(saved.events.find(item => item.type === 'user').text.includes('参考文件.txt'));
    await page.reload();
    await page.locator('.conversation-title').filter({ hasText: sessionName }).waitFor();
    assert.equal(await page.locator('.user-message .attachment-chip').count(), 2);
    await page.screenshot({ path: path.join(out, 'composer-renamed-history.png') });

    await attach('添加文件');
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await page.getByRole('dialog', { name: '设置', exact: true }).getByText('深色', { exact: true }).click();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    await page.getByRole('button', { name: '收起侧栏', exact: true }).click();
    for (const width of [320, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 850 });
      await add.click();
      assert.ok(await page.getByRole('menuitem', { name: '添加文件夹', exact: true }).isVisible());
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.keyboard.press('Escape');
    }
    await page.setViewportSize({ width: 320, height: 850 });
    await add.click();
    await page.screenshot({ path: path.join(out, 'composer-dark-mobile.png') });
    assert.deepEqual(errors, []);
    console.log('文件引用与重命名验收通过：加号菜单、文件/目录、去重移除、会话隔离、发送恢复、手动名称、键盘、主题及窄窗口。');
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'composer-failure.png') });
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
