import React, { useEffect, useRef } from 'react';
import { Check, Minus, Moon, Plus, Settings, Sun, Type, X } from 'lucide-react';

export default function SettingsDialog({ open, onClose, theme, onTheme, fontSize, onFontSize, busy }) {
  const dialog = useRef(null);
  useEffect(() => {
    if (open && !dialog.current.open) dialog.current.showModal();
    if (!open && dialog.current.open) dialog.current.close();
  }, [open]);

  return <dialog ref={dialog} className="settings-dialog" aria-labelledby="settings-title" onCancel={onClose} onClose={onClose}>
    <header className="settings-header"><Settings size={19} /><h2 id="settings-title">设置</h2><button className="icon-button" onClick={onClose} aria-label="关闭设置" autoFocus><X size={18} /></button></header>
    <div className="settings-content">
      <h3>外观</h3><p className="settings-description">选择你习惯的桌面配色。</p>
      <fieldset className="theme-fieldset"><legend>主题</legend>
        <div className="theme-options">{[['light', '浅色', Sun], ['dark', '深色', Moon]].map(([value, label, Icon]) => <label className={`theme-option ${theme === value ? 'is-selected' : ''}`} key={value}>
          <input type="radio" name="theme" value={value} checked={theme === value} onChange={() => onTheme(value)} disabled={busy} />
          <span className={`theme-preview preview-${value}`} aria-hidden="true"><span className="preview-sidebar"><i /><i /><i /></span><span className="preview-main"><i /><i /><b /></span></span>
          <span className="theme-option-label"><Icon size={15} /><span>{label}</span>{theme === value && <Check size={15} />}</span>
        </label>)}</div>
      </fieldset>
      <div className="font-setting"><label htmlFor="font-size"><Type size={16} />字体大小</label><div className="font-controls">
        <button className="icon-button" onClick={() => onFontSize(fontSize - 1)} disabled={busy || fontSize <= 12} aria-label="缩小字体"><Minus size={15} /></button>
        <select id="font-size" aria-label="字体大小" value={fontSize} onChange={event => onFontSize(Number(event.target.value))} disabled={busy}>{Array.from({ length: 11 }, (_, index) => index + 12).map(size => <option key={size} value={size}>{size} px</option>)}</select>
        <button className="icon-button" onClick={() => onFontSize(fontSize + 1)} disabled={busy || fontSize >= 22} aria-label="放大字体"><Plus size={15} /></button>
      </div></div>
      <p className="font-preview">用合适的字号，专注阅读与编写代码。</p>
      <p className="settings-note" role="status">{busy ? '正在保存…' : '自动保存，重新打开应用后仍然生效。'}</p>
    </div>
  </dialog>;
}
