import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Copy, CornerUpLeft, TextSelect } from 'lucide-react';
import { copyText } from '../chat.js';

export default function ChatSelection({ container, sessionId, onReply }) {
  const [popup, setPopup] = useState(null);
  const [notice, setNotice] = useState('');
  const menu = useRef(null);
  const timer = useRef(null);
  useEffect(() => {
    setPopup(null);
    const element = container.current;
    if (!element) return;
    let selectionTimer;
    function selectedText() {
      const selection = window.getSelection();
      const list = element.querySelector('.message-list');
      return selection && list?.contains(selection.anchorNode) && list.contains(selection.focusNode) ? selection.toString().trim() : '';
    }
    function show(kind, x, y, text) {
      setPopup({ kind, text, x: Math.max(8, Math.min(x, innerWidth - 184)), y: Math.max(8, Math.min(y + 8, innerHeight - (kind === 'menu' ? 94 : 48))) });
    }
    function mouseUp(event) {
      if (event.button !== 0) return;
      clearTimeout(selectionTimer);
      const text = selectedText();
      // 浏览器可能在松开鼠标后补发选区自动滚动，等待布局完成再显示。
      if (text) selectionTimer = setTimeout(() => {
        if (selectedText() === text) show('reply', event.clientX, event.clientY, text);
      }, 50);
      else setPopup(null);
    }
    function contextMenu(event) {
      if (!event.target.closest('.message-list')) return;
      event.preventDefault();
      clearTimeout(selectionTimer);
      show('menu', event.clientX, event.clientY, selectedText());
    }
    function clear() { setPopup(null); }
    function outside(event) { if (!event.target.closest('.chat-selection-popup')) { clearTimeout(selectionTimer); clear(); } }
    function selectionChanged() { if (window.getSelection()?.isCollapsed) { clearTimeout(selectionTimer); clear(); } }
    function keyDown(event) { if (event.key === 'Escape') { clearTimeout(selectionTimer); clear(); } }
    function resize() { clearTimeout(selectionTimer); clear(); }
    element.addEventListener('mouseup', mouseUp);
    element.addEventListener('contextmenu', contextMenu);
    element.addEventListener('scroll', clear, true);
    document.addEventListener('pointerdown', outside);
    document.addEventListener('selectionchange', selectionChanged);
    document.addEventListener('keydown', keyDown);
    window.addEventListener('resize', resize);
    return () => {
      element.removeEventListener('mouseup', mouseUp);
      element.removeEventListener('contextmenu', contextMenu);
      element.removeEventListener('scroll', clear, true);
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('selectionchange', selectionChanged);
      document.removeEventListener('keydown', keyDown);
      window.removeEventListener('resize', resize);
      clearTimeout(selectionTimer);
    };
  }, [container, sessionId]);
  useEffect(() => { if (popup?.kind === 'menu') menu.current?.querySelector('button:not(:disabled)')?.focus({ preventScroll: true }); }, [popup?.kind]);
  useLayoutEffect(() => {
    if (!popup || !menu.current) return;
    const bounds = menu.current.getBoundingClientRect();
    const x = Math.max(8, Math.min(popup.x, innerWidth - bounds.width - 8));
    const y = Math.max(8, Math.min(popup.y, innerHeight - bounds.height - 8));
    if (x !== popup.x || y !== popup.y) setPopup({ ...popup, x, y });
  }, [popup]);
  useEffect(() => () => clearTimeout(timer.current), []);

  async function copy() {
    try { await copyText(popup.text); setNotice('已复制'); }
    catch (error) { setNotice(error.message); }
    setPopup(null);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setNotice(''), 2000);
  }
  function selectAll() {
    const list = container.current?.querySelector('.message-list');
    if (list) {
      const range = document.createRange();
      range.selectNodeContents(list);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
    setPopup(null);
  }
  return createPortal(<>
    {popup && <div ref={menu} className={`chat-selection-popup selection-${popup.kind}`} style={{ left: popup.x, top: popup.y }} role={popup.kind === 'menu' ? 'menu' : undefined}
      onMouseDown={event => event.preventDefault()} onKeyDown={event => {
        if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
        event.preventDefault();
        const buttons = [...menu.current.querySelectorAll('button:not(:disabled)')];
        const current = buttons.indexOf(document.activeElement);
        buttons[(current + (event.key === 'ArrowDown' ? 1 : buttons.length - 1)) % buttons.length]?.focus();
      }}>
      {popup.kind === 'reply' ? <button onClick={() => { onReply({ text: popup.text }); window.getSelection()?.removeAllRanges(); setPopup(null); }}><CornerUpLeft size={15} />回复</button> : <>
        <button role="menuitem" onClick={copy} disabled={!popup.text}><Copy size={14} /><span>Copy</span><kbd>Ctrl+C</kbd></button>
        <button role="menuitem" onClick={selectAll}><TextSelect size={14} /><span>Select all</span><kbd>Ctrl+A</kbd></button>
      </>}
    </div>}
    {notice && <div className="clipboard-status" role="status">{notice}</div>}
  </>, document.body);
}
