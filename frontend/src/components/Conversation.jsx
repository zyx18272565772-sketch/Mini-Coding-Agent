import React, { useEffect, useMemo, useRef } from 'react';
import { AlertCircle, ArrowDown, Check, ChevronRight, Code2, FileSearch, FlaskConical, LoaderCircle } from 'lucide-react';
import { buildTimeline, folderName, groupExecutionRows } from '../state.js';
import ExecutionGroup from './ExecutionGroup.jsx';
import MessageContent, { CopyButton } from './MessageContent.jsx';
import ChatSelection from './ChatSelection.jsx';
import { splitRequest } from '../chat.js';
import AttachmentList from './AttachmentList.jsx';

const suggestions = [
  [FileSearch, '了解这个项目', '请先阅读这个项目，说明它的目录结构、入口和主要功能。'],
  [Code2, '检查代码问题', '请审查这个项目的主要代码，找出有实际影响的问题，并说明文件位置和原因。'],
  [FlaskConical, '运行项目测试', '请查找这个项目使用的测试框架，运行现有测试并报告结果；如果没有测试，请先说明。'],
];

export default function Conversation({ session, projectName, loading, pendingMessage, onSuggestion, onQuote }) {
  const rows = useMemo(() => buildTimeline(session?.events || []), [session?.events]);
  const visibleRows = useMemo(() => groupExecutionRows(rows), [rows]);
  const waitingBackground = rows.some(row => row.status === 'background') && rows.at(-1)?.type === 'notice' && rows.at(-1).text?.includes('等待后台');
  const scroller = useRef(null);
  const follow = useRef(true);
  const lastSession = useRef(null);
  const [showJump, setShowJump] = React.useState(false);
  useEffect(() => {
    if (lastSession.current !== session?.id) {
      lastSession.current = session?.id;
      follow.current = true;
    }
    if (follow.current && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [rows, session?.id, session?.pending_approval, pendingMessage]);

  function onScroll() {
    const element = scroller.current;
    follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100;
    setShowJump(!follow.current);
  }

  return <div className="conversation-shell">
    <div className="conversation-scroll" ref={scroller} onScroll={onScroll} role="log" aria-live="polite" aria-relevant="additions" aria-label="聊天记录" tabIndex={0}>
      {loading ? <div className="loading-state" role="status"><LoaderCircle size={20} className="spin" />正在载入会话…</div> : !rows.length && !pendingMessage ? <section className="welcome">
        <div className="welcome-project"><span className="project-mark"><Code2 size={20} /></span>{projectName || folderName(session?.workdir) || '你的工作区'}</div>
        <h1>今天想完成什么？</h1>
        <p>从一个想法开始，一起阅读、编写和改进代码。</p>
        <div className="suggestions">{suggestions.map(([Icon, title, prompt]) => <button key={title} onClick={() => onSuggestion(prompt)}><Icon size={17} /><span>{title}</span><ChevronRight size={15} /></button>)}</div>
      </section> : <div className="message-list">
        {visibleRows.map(row => row.type === 'execution' ? <ExecutionGroup key={`${session?.id}:${row.seq}`} row={row} /> : row.type === 'user' ? <UserMessage key={row.seq} text={row.text} /> : row.type === 'assistant' ? <article className={`assistant-message ${row.scope === 'subagent' ? 'child-message' : ''}`} key={row.seq} data-streaming={row.streaming || undefined} aria-busy={row.streaming || undefined}>
          <MessageContent text={row.text} />
          {row.interrupted && <div className="stream-interrupted">输出已中断</div>}
          {!row.streaming && row.text && <div className="message-actions"><CopyButton text={row.text} /></div>}
        </article> : row.type === 'error' ? <div className="inline-error" role="alert" key={row.seq}><AlertCircle size={16} /><span>{row.message}</span></div> : <div className="timeline-notice" key={row.seq}><SquareNotice />{row.text}</div>)}
        {pendingMessage && <UserMessage text={pendingMessage.text} pending />}
        {session?.status === 'running' && <div className="thinking" role="status"><span className="thinking-dot" />{waitingBackground ? '正在等待后台任务完成…' : 'Agent 正在处理任务…'}</div>}
        {session?.status === 'completed' && !pendingMessage && <div className="completion-note"><Check size={13} />本轮任务已结束</div>}
      </div>}
    </div>
    <ChatSelection container={scroller} sessionId={session?.id} onReply={onQuote} />
    {showJump && <button className="jump-button icon-button" aria-label="回到最新消息" onClick={() => { follow.current = true; scroller.current.scrollTop = scroller.current.scrollHeight; setShowJump(false); }}><ArrowDown size={17} /></button>}
  </div>;
}

function SquareNotice() { return <span className="stop-notice-icon" />; }

function UserMessage({ text, pending = false }) {
  const message = splitRequest(text);
  return <div className="user-message" data-pending={pending || undefined}><div>{message.quote && <blockquote className="user-quote">{message.quote}</blockquote>}<AttachmentList items={message.attachments} /><div>{message.text}</div>{pending && <small role="status">正在发送…</small>}</div></div>;
}
