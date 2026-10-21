import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeEvents, buildTimeline, mergeSnapshot, taskStatus, groupExecutionRows } from './state.js';

test('每轮工具与子智能体过程合并，正文和新任务保留各自位置', () => {
  const rows = [
    { seq: 1, type: 'user', text: '任务一' },
    { seq: 2, type: 'tool', name: 'task' },
    { seq: 3, type: 'assistant', scope: 'subagent', text: '子任务过程' },
    { seq: 4, type: 'assistant', text: '主智能体说明' },
    { seq: 5, type: 'tool', name: 'read_file' },
    { seq: 6, type: 'user', text: '任务二' },
    { seq: 7, type: 'tool', name: 'bash' },
  ];
  const grouped = groupExecutionRows(rows);
  assert.deepEqual(grouped.map(row => row.type), ['user', 'execution', 'assistant', 'user', 'execution']);
  assert.deepEqual(grouped[1].items.map(row => row.seq), [2, 3, 5]);
  assert.equal(grouped[2].text, '主智能体说明');
  assert.deepEqual(grouped[4].items.map(row => row.seq), [7]);
  assert.equal(rows[1].type, 'tool');
});

test('兼容旧版停止事件：结束流式状态并保留工具结果', () => {
  const rows = buildTimeline([
    { seq: 1, type: 'assistant_start', message_id: 'a', scope: 'main' },
    { seq: 2, type: 'assistant_delta', message_id: 'a', scope: 'main', text: '部分内容' },
    { seq: 3, type: 'tool_start', call_id: 'b', name: 'bash' },
    { seq: 4, type: 'state', status: 'limited' },
  ]);
  assert.equal(rows[0].streaming, false);
  assert.equal(rows[0].text, '部分内容');
  assert.equal(rows[1].status, 'stopped');
});

test('任务状态区分待处理、等待依赖、进行中和已完成', () => {
  assert.equal(taskStatus({ status: 'pending', incompleteDependencies: [] }), '待处理');
  assert.equal(taskStatus({ status: 'pending', incompleteDependencies: ['task_a1111111'] }), '等待依赖');
  assert.equal(taskStatus({ status: 'in_progress', incompleteDependencies: [] }), '进行中');
  assert.equal(taskStatus({ status: 'completed', incompleteDependencies: [] }), '已完成');
});

test('聊天事件未增加时仍接收同项目其他会话更新的任务', () => {
  const current = { events: [], tasks: [{ id: 'task_a1111111', status: 'pending' }] };
  const changed = mergeSnapshot(current, { cursor: 0, events: [], tasks: [{ id: 'task_a1111111', status: 'completed' }] });
  assert.equal(changed.tasks[0].status, 'completed');
});

test('轮询与发送同时返回事件时不会重复消息', () => {
  const first = [{ seq: 1, type: 'user', text: '你好' }];
  assert.deepEqual(mergeEvents(first, [...first, { seq: 2, type: 'assistant', text: '你好' }]).map(x => x.seq), [1, 2]);
});

test('主智能体与子智能体同名调用分开显示，停止后不显示仍在运行', () => {
  const timeline = buildTimeline([
    { seq: 1, type: 'tool_start', scope: 'main', call_id: 'a', name: 'task', args: {} },
    { seq: 2, type: 'tool_start', scope: 'subagent', call_id: 'a', name: 'read_file', args: {} },
    { seq: 3, type: 'tool_result', scope: 'subagent', call_id: 'a', output: '内容', allowed: true },
    { seq: 4, type: 'state', status: 'stopped' },
  ]);
  assert.equal(timeline[0].status, 'stopped');
  assert.equal(timeline[1].status, 'completed');
  assert.equal(timeline[1].output, '内容');
});

test('权限拒绝不能显示为执行完成', () => {
  const timeline = buildTimeline([
    { seq: 1, type: 'tool_start', call_id: 'a', name: 'bash', args: {} },
    { seq: 2, type: 'tool_result', call_id: 'a', output: '权限被拒绝。', allowed: false },
  ]);
  assert.equal(timeline[0].status, 'denied');
});

test('旧轮询结果不能让已经完成的任务退回运行状态', () => {
  const current = { status: 'completed', pending_approval: null, events: [{ seq: 4, type: 'state', status: 'completed' }] };
  const stale = { status: 'running', cursor: 2, events: [] };
  assert.equal(mergeSnapshot(current, stale).status, 'completed');
});

test('失败后继续清除恢复提示，旧轮询不能让错误和旧用量重新出现', () => {
  const failed = { status: 'error', failure: { retryable: true }, usage: { calls: 2 }, events: [{ seq: 4, type: 'state', status: 'error' }] };
  const resumed = mergeSnapshot(failed, { status: 'running', failure: null, usage: { calls: 3 }, events: [{ seq: 5, type: 'state', status: 'running' }] });
  const merged = mergeSnapshot(resumed, { ...failed, cursor: 4 });
  assert.equal(merged.status, 'running');
  assert.equal(merged.failure, null);
  assert.equal(merged.usage.calls, 3);
});

test('记忆进度作为提示显示，不混入助手回复或工具调用', () => {
  const events = [
    { seq: 1, type: 'notice', scope: 'main', text: '[记忆] 已召回 1 条记忆。' },
    { seq: 2, type: 'assistant', text: '根据项目偏好完成。' },
  ];
  assert.deepEqual(buildTimeline(events), events);
});

test('流式片段逐段累积在同一条回复，最终正文替换片段且不重复', () => {
  const events = [
    { seq: 1, type: 'assistant_start', scope: 'main', message_id: 'reply' },
    { seq: 2, type: 'assistant_delta', scope: 'main', message_id: 'reply', text: '你' },
    { seq: 3, type: 'assistant_delta', scope: 'main', message_id: 'reply', text: '好' },
  ];
  const partial = buildTimeline(events);
  assert.equal(partial.length, 1);
  assert.equal(partial[0].text, '你好');
  assert.equal(partial[0].streaming, true);
  const complete = buildTimeline([...events, { seq: 4, type: 'assistant', scope: 'main', message_id: 'reply', text: '你好！' }]);
  assert.equal(complete.length, 1);
  assert.equal(complete[0].text, '你好！');
  assert.equal(complete[0].streaming, false);
});

test('主子 Agent 的流式回复独立，停止时保留片段并结束输出状态', () => {
  const rows = buildTimeline([
    { seq: 1, type: 'assistant_start', scope: 'main', message_id: 'same' },
    { seq: 2, type: 'assistant_start', scope: 'subagent', message_id: 'same' },
    { seq: 3, type: 'assistant_delta', scope: 'subagent', message_id: 'same', text: '子任务内容' },
    { seq: 4, type: 'assistant_delta', scope: 'main', message_id: 'same', text: '主任务内容' },
    { seq: 5, type: 'state', status: 'stopped' },
  ]);
  assert.equal(rows[0].text, '主任务内容');
  assert.equal(rows[1].text, '子任务内容');
  assert.equal(rows[0].streaming, false);
  assert.equal(rows[1].streaming, false);
});

test('流式连接中断时保留正文，错误事件和历史整段回复仍能展示', () => {
  const rows = buildTimeline([
    { seq: 1, type: 'assistant', text: '旧会话回复' },
    { seq: 2, type: 'assistant_start', scope: 'main', message_id: 'reply' },
    { seq: 3, type: 'assistant_delta', scope: 'main', message_id: 'reply', text: '部分正文' },
    { seq: 4, type: 'assistant_end', scope: 'main', message_id: 'reply', text: '部分正文', interrupted: true },
    { seq: 5, type: 'error', message: '连接中断' },
  ]);
  assert.equal(rows.length, 3);
  assert.equal(rows[1].text, '部分正文');
  assert.equal(rows[1].streaming, false);
  assert.equal(rows[1].interrupted, true);
});

test('后台启动确认保持运行，完成事件只更新原工具卡片并显示实际输出', () => {
  const started = [
    { seq: 1, type: 'tool_start', scope: 'main', call_id: 'bg', name: 'bash', args: { command: 'pytest', run_in_background: true } },
    { seq: 2, type: 'tool_result', scope: 'main', call_id: 'bg', task_id: 'bg_123', output: '已启动', allowed: true, background: true },
  ];
  assert.equal(buildTimeline(started)[0].status, 'background');
  const rows = buildTimeline([...started, { seq: 3, type: 'background_result', scope: 'main', call_id: 'bg', task_id: 'bg_123', status: 'completed', output: '测试通过', exit_code: 0 }]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, 'completed');
  assert.equal(rows[0].output, '测试通过');
  assert.equal(rows[0].task_id, 'bg_123');
});

test('后台失败不显示成功，停止时关闭主子智能体的后台运行状态', () => {
  const started = [
    { seq: 1, type: 'tool_start', scope: 'subagent', call_id: 'bg', name: 'bash', args: {} },
    { seq: 2, type: 'tool_result', scope: 'subagent', call_id: 'bg', task_id: 'bg_456', allowed: true, background: true },
  ];
  const failed = buildTimeline([...started, { seq: 3, type: 'background_result', scope: 'subagent', call_id: 'bg', task_id: 'bg_456', status: 'failed', output: '错误：退出码 2' }]);
  assert.equal(failed[0].status, 'failed');
  const stopped = buildTimeline([...started, { seq: 3, type: 'state', status: 'stopped' }]);
  assert.equal(stopped[0].status, 'stopped');
});
