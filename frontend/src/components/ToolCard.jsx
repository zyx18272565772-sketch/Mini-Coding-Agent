import React, { memo, useState } from 'react';
import { AlertCircle, Check, ChevronRight, FilePenLine, FileText, LoaderCircle, Search, ShieldX, Square, Terminal, Workflow } from 'lucide-react';

const toolLabels = { bash: '执行命令', read_file: '读取文件', write_file: '写入文件', edit_file: '编辑文件', glob: '查找文件', todo_write: '更新计划（旧记录）', task: '委派子任务', load_skill: '加载技能', compact: '压缩上下文', create_task: '创建任务', update_task: '设置任务依赖', list_tasks: '查看任务列表', get_task: '读取任务详情', claim_task: '领取任务', complete_task: '完成任务' };
const icons = { bash: Terminal, read_file: FileText, write_file: FilePenLine, edit_file: FilePenLine, glob: Search, task: Workflow };

export default memo(function ToolCard({ row }) {
  const [expanded, setExpanded] = useState(false);
  const [fullOutput, setFullOutput] = useState(false);
  const Icon = icons[row.name] || Workflow;
  const active = ['running', 'background'].includes(row.status);
  const StatusIcon = active ? LoaderCircle : row.status === 'denied' ? ShieldX : row.status === 'stopped' ? Square : ['failed', 'error'].includes(row.status) ? AlertCircle : Check;
  const labels = { running: '执行中', background: '后台运行中', completed: '已返回', denied: '已拒绝', stopped: '已停止', failed: '执行失败', error: '已中断' };
  const preview = row.args?.path || row.args?.command || row.args?.pattern || row.args?.name || row.args?.subject || row.args?.task_id || '';
  const output = row.output || '';
  const bodyId = `tool-${row.seq}`;
  return <div className={`tool-card tool-${row.status}`}>
    <button className="tool-toggle" onClick={() => setExpanded(value => !value)} aria-expanded={expanded} aria-controls={bodyId}>
      <ChevronRight size={14} className={expanded ? 'rotated' : ''} /><Icon size={15} />
      <span className="tool-name">{toolLabels[row.name] || row.name}</span>
      {row.scope === 'subagent' && <span className="subagent-tag">子智能体</span>}
      <span className="tool-preview">{String(preview)}</span>
      <span className={`tool-status ${row.status}`}><StatusIcon size={13} className={active ? 'spin' : ''} /><span>{labels[row.status]}</span></span>
    </button>
    {expanded && <div className="tool-details" id={bodyId}>
      <div className="detail-label">{row.name} · 参数</div><pre>{JSON.stringify(row.args, null, 2)}</pre>
      {row.task_id && <div className="detail-label">后台任务：{row.task_id}{row.exit_code != null && ` · 退出码：${row.exit_code}`}</div>}
      <div className="detail-label">执行结果</div>
      {output ? <pre>{fullOutput ? output : output.slice(0, 12000)}</pre> : <p className="muted">{active ? '正在等待工具返回…' : '没有返回结果。'}</p>}
      {output.length > 12000 && <button className="text-button" onClick={() => setFullOutput(value => !value)}>{fullOutput ? '收起完整结果' : `显示完整结果（${output.length.toLocaleString()} 字符）`}</button>}
    </div>}
  </div>;
});
