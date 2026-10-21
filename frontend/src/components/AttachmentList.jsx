import React from 'react';
import { FileText, FolderOpen, X } from 'lucide-react';

export default function AttachmentList({ items = [], onRemove }) {
  if (!items.length) return null;
  return <div className="attachment-list" aria-label="引用的文件和文件夹">{items.map(item => <div className="attachment-chip" key={`${item.kind}:${item.path}`} title={item.path}>
    {item.kind === 'folder' ? <FolderOpen size={15} /> : <FileText size={15} />}<span>{item.name}</span>
    {onRemove && <button type="button" className="icon-button" onClick={() => onRemove(item)} aria-label={`移除引用 ${item.name}`}><X size={13} /></button>}
  </div>)}</div>;
}
