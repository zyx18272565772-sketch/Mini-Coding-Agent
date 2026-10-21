import React, { useState } from 'react';
import MessageContent from './MessageContent.jsx';
import { ChevronRight, ListChecks, LoaderCircle } from 'lucide-react';
import ToolCard from './ToolCard.jsx';

export default function ExecutionGroup({ row }) {
  const [open, setOpen] = useState(false);
  const tools = row.items.filter(item => item.type === 'tool');
  const active = row.items.some(item => ['running', 'background'].includes(item.status) || item.streaming);
  const failed = tools.filter(item => ['failed', 'error', 'denied', 'stopped'].includes(item.status)).length;
  const bodyId = `execution-${row.seq}`;
  return <section className="execution-group">
    <button className="execution-toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen(!open)}>
      <ChevronRight size={15} className={open ? 'rotated' : ''} />
      {active ? <LoaderCircle size={15} className="spin" /> : <ListChecks size={15} />}
      <span>执行记录</span><span className="execution-count">{tools.length} 次工具调用</span>
      <span className={`execution-summary ${failed ? 'has-errors' : ''}`}>{active ? '正在执行…' : failed ? `${failed} 项异常或未执行` : '已结束'}</span>
    </button>
    {open && <div className="execution-body" id={bodyId}>
      {row.items.map(item => item.type === 'tool' ? <ToolCard key={item.seq} row={item} /> : <div className="execution-child" key={item.seq}>
        <div className="assistant-label">子智能体{item.streaming && <span className="streaming-indicator">正在输出…</span>}</div>
        <MessageContent text={item.text || item.message} />
        {item.interrupted && <div className="stream-interrupted">输出已中断</div>}
      </div>)}
    </div>}
  </section>;
}
