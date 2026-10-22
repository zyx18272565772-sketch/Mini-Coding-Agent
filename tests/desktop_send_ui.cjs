/* 固定长会话，在真实 Chrome 中测量输入、空轮询和发送确认；只使用模拟接口。 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const events = [];
    for (let i = 0; i < 24; i++) {
      events.push({ seq: events.length + 1, type: 'user', text: `历史任务 ${i}` });
      events.push({ seq: events.length + 1, type: 'assistant', scope: 'main', text:
        `## 项目检查 ${i}\n\n已经检查入口、权限和异常处理。\n\n|模块|状态|\n|---|---|\n|主循环|通过|\n\n\`\`\`python\n` +
        Array.from({ length: 18 }, (_, line) => `def function_${line}(value):\n    return value + ${line}\n`).join('\n') + '\n```' });
    }
    let session = { id: 'perf-session', title: '长会话验收', project_id: 'perf-project', workdir: 'D:/offline',
      status: 'completed', events, tasks: [], model: 'offline-model' };
    const project = { id: session.project_id, name: '性能测试', workdir: session.workdir };
    let failNext = false;
    const sends = [];
    const summary = () => [{ id: session.id, title: session.title, project_id: session.project_id, workdir: session.workdir, status: session.status }];
    await page.route('**/__api', async route => {
      const { method, args } = route.request().postDataJSON();
      let data;
      if (method === 'bootstrap') data = { sessions: summary(), projects: [project], settings: { theme: 'light', font_size: 15 }, model: 'offline-model', warnings: [] };
      else if (method === 'get_session') data = session;
      else if (method === 'poll') data = { ...session, events: session.events.filter(event => event.seq > (args[1] || 0)), cursor: session.events.at(-1).seq, sessions: summary(), projects: [project] };
      else if (method === 'send_message') {
        sends.push(args[1]);
        // 模拟桌面进程启动较慢，界面仍须及时显示正在发送的消息。
        await new Promise(resolve => setTimeout(resolve, 1800));
        if (failNext) {
          await route.fulfill({ json: { ok: false, error: '模拟发送失败，草稿已保留' } });
          return;
        }
        session = { ...session, status: 'completed', events: [...session.events, { seq: session.events.length + 1, type: 'user', text: args[1] }] };
        data = session;
      } else throw new Error(`Unexpected method: ${method}`);
      await route.fulfill({ json: { ok: true, data } });
    });
    await page.goto(process.env.DESKTOP_TEST_URL || 'http://127.0.0.1:9011/');
    await page.locator('.assistant-message').nth(23).waitFor();
    const input = page.getByRole('textbox', { name: '任务内容', exact: true });
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Performance.enable');
    async function scriptTime() {
      const { metrics } = await cdp.send('Performance.getMetrics');
      return metrics.find(metric => metric.name === 'ScriptDuration').value * 1000;
    }
    const idleStart = await scriptTime();
    await page.waitForTimeout(1600);
    const idleMs = await scriptTime() - idleStart;
    const typing = [];
    for (let sample = 0; sample < 3; sample++) {
      await input.fill('');
      const start = await scriptTime();
      await input.pressSequentially('performance', { delay: 0 });
      typing.push(await scriptTime() - start);
    }
    await input.fill('继续检查会话');
    const start = Date.now();
    await page.getByRole('button', { name: '发送任务', exact: true }).click();
    await page.locator('.user-message').filter({ hasText: '继续检查会话' }).waitFor();
    const echoMs = Date.now() - start;
    const result = { idle_script_ms: +idleMs.toFixed(1), typing_script_ms: typing.map(x => +x.toFixed(1)), send_echo_ms: echoMs };
    console.log(JSON.stringify(result));
    const out = path.resolve(__dirname, '../.agent_data/ui-checks');
    fs.mkdirSync(out, { recursive: true });
    fs.writeFileSync(path.join(out, process.env.PERF_BASELINE ? 'send-before.json' : 'send-after.json'), JSON.stringify(result, null, 2));
    if (!process.env.PERF_BASELINE) {
      assert.ok(echoMs < 600, `发送后显示消息过慢：${echoMs}ms`);
      assert.ok(Math.max(...typing) < 300, `输入导致历史 Markdown 重渲染：${typing}`);
      await input.press('Enter');
      await page.waitForTimeout(2200);
      assert.equal(await page.locator('.user-message').filter({ hasText: '继续检查会话' }).count(), 1, '确认后不能重复显示消息');
      assert.equal(await input.inputValue(), '');
      assert.equal(sends.length, 1, '等待提交时不能重复发送');
      failNext = true;
      await input.fill('发送失败应保留');
      await page.getByRole('button', { name: '发送任务', exact: true }).click();
      await page.locator('.user-message[data-pending]').filter({ hasText: '发送失败应保留' }).waitFor();
      await input.fill('发送期间编辑的新草稿');
      await page.getByRole('alert').filter({ hasText: '模拟发送失败' }).waitFor();
      assert.equal(await input.inputValue(), '发送期间编辑的新草稿');
      assert.equal(await page.locator('.user-message[data-pending]').count(), 0);
      assert.equal(await page.locator('.user-message').filter({ hasText: '发送失败应保留' }).count(), 0);
      assert.deepEqual(errors, []);
      await page.screenshot({ path: path.join(out, 'send-long-session.png') });
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
