import React, { useEffect, useRef } from 'react';
import { Trash2, X } from 'lucide-react';

export default function DeleteDialog({ target, onClose, onConfirm, busy }) {
  const dialog = useRef(null);
  useEffect(() => {
    if (target && !dialog.current.open) dialog.current.showModal();
    if (!target && dialog.current.open) dialog.current.close();
  }, [target]);
  const project = target?.kind === 'project';
  return <dialog ref={dialog} className="delete-dialog" aria-labelledby="delete-title" onCancel={event => { if (busy) event.preventDefault(); else onClose(); }} onClose={onClose}>
    <header className="settings-header"><Trash2 size={18} /><h2 id="delete-title">删除{project ? '项目' : '会话'}</h2><button className="icon-button" onClick={onClose} disabled={busy} aria-label="关闭删除确认"><X size={18} /></button></header>
    <div className="delete-content"><p>确定删除“<strong>{target?.name}</strong>”吗？</p>
      <p className="muted">{project ? `这会删除项目及其 ${target.count} 个会话的聊天记录。项目文件夹、源代码、任务与记忆文件会保留。` : '这会删除该会话的聊天记录和保存的对话上下文，项目文件会保留。'}聊天记录删除后无法恢复。</p>
    </div>
    <footer className="dialog-actions"><button className="dialog-cancel" onClick={onClose} disabled={busy} autoFocus>取消</button><button className="danger-button" onClick={onConfirm} disabled={busy}>删除{project ? '项目' : '会话'}</button></footer>
  </dialog>;
}
