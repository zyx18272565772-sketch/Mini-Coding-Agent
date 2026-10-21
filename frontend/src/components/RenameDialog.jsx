import React, { useEffect, useRef, useState } from 'react';
import { Pencil, X } from 'lucide-react';

export default function RenameDialog({ target, onClose, onConfirm, busy }) {
  const dialog = useRef(null);
  const input = useRef(null);
  const [name, setName] = useState('');
  useEffect(() => {
    let timer;
    if (target) {
      setName(target.name);
      if (!dialog.current.open) dialog.current.showModal();
      timer = setTimeout(() => { input.current?.focus(); input.current?.select(); }, 0);
    }
    else if (dialog.current.open) dialog.current.close();
    return () => clearTimeout(timer);
  }, [target]);
  const label = target?.kind === 'project' ? '项目' : '会话';
  const invalid = !name.trim() || name.trim().length > 80 || /[\u0000-\u001f\u007f]/.test(name);
  return <dialog ref={dialog} className="rename-dialog" aria-labelledby="rename-title" onCancel={event => { if (busy) event.preventDefault(); else onClose(); }} onClose={onClose}>
    <form onSubmit={event => { event.preventDefault(); if (!invalid && !busy) onConfirm(name.trim()); }}>
      <header className="settings-header"><Pencil size={18} /><h2 id="rename-title">重命名{label}</h2><button type="button" className="icon-button" onClick={onClose} disabled={busy} aria-label="关闭重命名"><X size={18} /></button></header>
      <div className="rename-content"><label htmlFor="display-name">{label}名称</label><input ref={input} id="display-name" value={name} maxLength={80} onChange={event => setName(event.target.value)} disabled={busy} autoComplete="off" aria-describedby="rename-hint" /><p id="rename-hint" className="muted">最多 80 个字符。仅修改显示名称。</p></div>
      <footer className="dialog-actions"><button type="button" className="dialog-cancel" onClick={onClose} disabled={busy}>取消</button><button type="submit" className="dialog-save" disabled={busy || invalid}>{busy ? '保存中…' : '保存'}</button></footer>
    </form>
  </dialog>;
}
