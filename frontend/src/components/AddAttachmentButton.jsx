import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { FilePlus2, FolderPlus, Plus } from 'lucide-react';

export default function AddAttachmentButton({ onPick, disabled, sessionId }) {
  const [position, setPosition] = useState(null);
  const button = useRef(null);
  const menu = useRef(null);
  useEffect(() => { setPosition(null); }, [sessionId, disabled]);
  useEffect(() => {
    if (!position) return;
    menu.current?.querySelector('button')?.focus();
    const outside = event => { if (!menu.current?.contains(event.target) && !button.current?.contains(event.target)) setPosition(null); };
    const key = event => {
      if (event.key === 'Escape') { event.preventDefault(); setPosition(null); button.current?.focus(); }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) && menu.current?.contains(document.activeElement)) {
        event.preventDefault();
        const items = Array.from(menu.current.querySelectorAll('button'));
        const index = items.indexOf(document.activeElement);
        items[event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
      }
      if (event.key === 'Tab') setPosition(null);
    };
    const close = () => setPosition(null);
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', key);
    window.addEventListener('resize', close);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', key); window.removeEventListener('resize', close); };
  }, [position]);
  function toggle() {
    const rect = button.current.getBoundingClientRect();
    setPosition(position ? null : { left: Math.max(8, Math.min(rect.left, window.innerWidth - 188)), bottom: window.innerHeight - rect.top + 8 });
  }
  function pick(kind) { setPosition(null); button.current?.focus(); onPick(kind); }
  return <>
    <button ref={button} type="button" className="composer-add" disabled={disabled} onClick={toggle} aria-label="添加文件或文件夹" title="添加文件或文件夹" aria-haspopup="menu" aria-expanded={!!position}><Plus size={19} /></button>
    {position && createPortal(<div ref={menu} className="attachment-menu" role="menu" aria-label="添加引用" style={position}>
      <button type="button" role="menuitem" onClick={() => pick('file')}><FilePlus2 size={16} />添加文件</button>
      <button type="button" role="menuitem" onClick={() => pick('folder')}><FolderPlus size={16} />添加文件夹</button>
    </div>, document.body)}
  </>;
}
