import React, { useEffect, useRef } from 'react';
import { ArrowUp, CornerUpLeft, Cpu, FolderOpen, LoaderCircle, ShieldCheck, Square, X } from 'lucide-react';
import { folderName } from '../state.js';
import AddAttachmentButton from './AddAttachmentButton.jsx';
import AttachmentList from './AttachmentList.jsx';

export default function Composer({ session, draft, quote, onRemoveQuote, attachments, onAddAttachment, onRemoveAttachment, projectName, model, onDraft, onSend, onStop, onPermission, busy, loading }) {
  const pending = session?.pending_approval;
  const running = ['running', 'waiting', 'stopping'].includes(session?.status);
  const denyButton = useRef(null);
  const textarea = useRef(null);
  const composing = useRef(false);
  useEffect(() => { if (pending) denyButton.current?.focus(); }, [pending?.request_id]);
  useEffect(() => { if (!running && !loading) textarea.current?.focus(); }, [session?.id, running, loading]);
  useEffect(() => { if (quote) textarea.current?.focus(); }, [quote]);
  return <div className="composer-area">
    {pending && <section className="permission-panel" role="region" aria-labelledby="permission-title">
      <div className="permission-heading"><ShieldCheck size={18} /><strong id="permission-title">需要你的确认</strong><span>任务已暂停</span></div>
      <p>{pending.reason}</p><pre>{JSON.stringify({ tool: pending.tool, ...pending.args }, null, 2)}</pre>
      <div className="permission-actions"><span>仅授权这一次操作</span><button ref={denyButton} className="secondary-button" onClick={() => onPermission(pending.request_id, false)} disabled={busy}>拒绝</button><button className="primary-button" onClick={() => onPermission(pending.request_id, true)} disabled={busy}>允许执行</button></div>
    </section>}
    <form className={`composer ${running ? 'is-running' : ''}`} onSubmit={event => { event.preventDefault(); if (!running && !busy && draft.trim()) onSend(); }}>
      {quote && <div className="composer-quote"><CornerUpLeft size={15} /><div><span>引用内容</span><blockquote>{quote.text}</blockquote></div><button type="button" className="icon-button" aria-label="移除引用" onClick={onRemoveQuote}><X size={15} /></button></div>}
      <AttachmentList items={attachments} onRemove={onRemoveAttachment} />
      <textarea ref={textarea} value={draft} aria-label="任务内容" placeholder={running ? '任务正在执行，可以先写下接下来的想法…' : '描述任务，或询问这个项目的代码…'}
        disabled={!session || loading} onChange={event => onDraft(event.target.value)}
        onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
        onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && !composing.current && event.keyCode !== 229) {
            event.preventDefault();
            if (!running && !busy && draft.trim()) onSend();
          }
        }} />
      <div className="composer-bottom"><AddAttachmentButton sessionId={session?.id} onPick={onAddAttachment} disabled={busy || loading || !session} />
        <span className="model-badge" title={`当前模型：${model}`}><Cpu size={14} /><span>{model}</span></span>
        <span className="composer-mode">本地</span>
        {running ? <button type="button" className="send-button stop-button" onClick={onStop} disabled={busy || session.status === 'stopping'} aria-label="停止任务" title="停止任务">{session.status === 'stopping' ? <LoaderCircle size={16} className="spin" /> : <Square size={14} fill="currentColor" />}</button> : <button type="submit" className="send-button" disabled={!draft.trim() || busy || loading || !session} aria-label="发送任务" title="发送任务"><ArrowUp size={19} /></button>}
      </div>
    </form>
    <div className="composer-hint"><span title={session?.workdir}><FolderOpen size={12} />{projectName || folderName(session?.workdir) || '选择项目后开始'}</span><span><kbd>Enter</kbd> 发送 · <kbd>Shift + Enter</kbd> 换行</span></div>
  </div>;
}
