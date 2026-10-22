export const statusLabels = {
  idle: '就绪', running: '运行中', waiting: '等待确认', stopping: '正在停止',
  completed: '已完成', stopped: '已停止', error: '出错', limited: '已停止',
};

export function taskStatus(task) {
  if (task.status === 'completed') return '已完成';
  if (task.status === 'in_progress') return '进行中';
  return task.incompleteDependencies?.length ? '等待依赖' : '待处理';
}

export function mergeEvents(previous, incoming) {
  if (!incoming.length) return previous;
  return [...new Map([...previous, ...incoming].map(event => [event.seq, event])).values()]
    .sort((a, b) => a.seq - b.seq);
}

export function buildTimeline(events) {
  const rows = [];
  const tools = new Map();
  const replies = new Map();
  for (const event of events) {
    const key = `${event.scope || 'main'}:${event.call_id}`;
    const replyKey = `${event.scope || 'main'}:${event.message_id}`;
    if (event.type === 'assistant_start') {
      const row = { ...event, type: 'assistant', text: '', streaming: true };
      rows.push(row);
      replies.set(replyKey, row);
    } else if (event.type === 'assistant_delta') {
      const row = replies.get(replyKey);
      if (row) row.text += event.text;
    } else if (event.type === 'assistant_end') {
      const row = replies.get(replyKey);
      if (row) Object.assign(row, { text: event.text ?? row.text, streaming: false, interrupted: event.interrupted });
    } else if (event.type === 'assistant' && event.message_id && replies.has(replyKey)) {
      Object.assign(replies.get(replyKey), { text: event.text, streaming: false });
    } else if (event.type === 'tool_start') {
      const row = { ...event, type: 'tool', status: 'running' };
      rows.push(row);
      tools.set(key, row);
    } else if (event.type === 'tool_result') {
      const row = tools.get(key);
      if (row) Object.assign(row, { output: event.output, task_id: event.task_id,
        status: event.allowed ? (event.is_error ? 'failed' : event.background ? 'background' : 'completed') : 'denied' });
    } else if (event.type === 'background_result') {
      const row = tools.get(key);
      if (row && row.task_id === event.task_id) Object.assign(row, { output: event.output, exit_code: event.exit_code,
        status: event.status === 'completed' ? 'completed' : event.status === 'cancelled' ? 'stopped' : 'failed' });
    } else if (['user', 'assistant', 'error', 'notice'].includes(event.type)) {
      rows.push(event);
    } else if (event.type === 'state' && ['stopped', 'error', 'completed', 'limited'].includes(event.status)) {
      for (const row of replies.values()) {
        if (row.streaming) Object.assign(row, { streaming: false, interrupted: true });
      }
      for (const row of tools.values()) {
        if (['running', 'background'].includes(row.status)) row.status = event.status === 'completed' ? 'error' : event.status === 'limited' ? 'stopped' : event.status;
      }
      if (event.status === 'stopped') rows.push({ ...event, type: 'notice', text: '任务已停止' });
    }
  }
  return rows;
}

export function groupExecutionRows(rows) {
  const grouped = [];
  let execution = null;
  for (const row of rows) {
    if (row.type === 'user') execution = null;
    if (row.type === 'tool' || row.scope === 'subagent') {
      if (!execution) {
        execution = { type: 'execution', seq: row.seq, items: [] };
        grouped.push(execution);
      }
      execution.items.push(row);
    } else grouped.push(row);
  }
  return grouped;
}

export function mergeSnapshot(current, snapshot) {
  if (!current) return snapshot;
  const incomingCursor = snapshot.cursor ?? snapshot.events?.at(-1)?.seq ?? 0;
  const currentCursor = current.events.at(-1)?.seq ?? 0;
  const events = mergeEvents(current.events, snapshot.events || []);
  return incomingCursor < currentCursor ? { ...current, events } : { ...current, ...snapshot, events };
}

export function folderName(path = '') {
  return path.split(/[\\/]/).filter(Boolean).at(-1) || path;
}
