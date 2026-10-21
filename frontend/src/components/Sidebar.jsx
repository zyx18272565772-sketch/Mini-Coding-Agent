import React, { useEffect, useState } from 'react';
import { ChevronRight, Code2, FolderClosed, FolderOpen, MoreHorizontal, PanelLeftClose, Pencil, Plus, Settings, SquarePen, Trash2 } from 'lucide-react';
import { folderName, statusLabels } from '../state.js';

export default function Sidebar({ sessions, projects, current, onSelect, onCreate, onCreateProject, onDeleteSession, onDeleteProject, onRename, onFolder, onSettings, onClose, busy, open }) {
  const [collapsed, setCollapsed] = useState({});
  const [menu, setMenu] = useState(null);
  useEffect(() => {
    if (current?.project_id) setCollapsed(previous => ({ ...previous, [current.project_id]: false }));
    setMenu(null);
  }, [current?.project_id, current?.id]);
  useEffect(() => {
    function outside(event) { if (!event.target.closest('.row-menu')) setMenu(null); }
    function key(event) { if (event.key === 'Escape') setMenu(null); }
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', key); };
  }, []);

  function rowMenu(kind, item, disabled) {
    const label = kind === 'project' ? '项目' : '会话';
    const expanded = menu === `${kind}:${item.id}`;
    return <div className="row-menu">
      <button className="icon-button row-options" aria-label={`${item.name || item.title} 的${label}选项`} aria-haspopup="menu" aria-expanded={expanded} onClick={() => setMenu(expanded ? null : `${kind}:${item.id}`)}><MoreHorizontal size={16} /></button>
      {expanded && <div className="row-menu-content" role="menu"><button className="rename-menu-item" role="menuitem" disabled={busy} onClick={() => { setMenu(null); onRename({ kind, id: item.id, name: item.name || item.title }); }}><Pencil size={14} />重命名{label}</button><button role="menuitem" disabled={disabled || busy} title={disabled ? '请先停止运行中的任务' : undefined} onClick={() => { setMenu(null); if (kind === 'project') onDeleteProject(item); else onDeleteSession(item); }}><Trash2 size={14} />删除{label}</button></div>}
    </div>;
  }

  return <aside className={`sidebar ${open ? 'is-open' : ''}`} aria-label="会话导航">
    <div className="brand-row">
      <span className="brand"><Code2 size={20} strokeWidth={1.8} /> Coding Agent</span>
      <button className="icon-button" onClick={onClose} aria-label="收起侧栏" title="收起侧栏"><PanelLeftClose size={17} /></button>
    </div>
    <button className="new-session" onClick={() => onCreate(current?.project_id)} disabled={busy}><SquarePen size={17} />新建会话<span className="shortcut-hint" aria-hidden="true">Ctrl N</span></button>
    <div className="sidebar-section-title"><span>项目</span><button className="icon-button" onClick={onCreateProject} disabled={busy} aria-label="创建项目" title="选择本地文件夹创建项目"><Plus size={16} /></button></div>
    <nav className="session-list project-list" aria-label="项目与会话">
      {!projects.length && <p className="project-empty">创建项目，开始新的任务。</p>}
      {projects.map(project => {
        const children = sessions.filter(item => item.project_id === project.id);
        const expanded = !collapsed[project.id];
        const FolderIcon = expanded ? FolderOpen : FolderClosed;
        return <section className="project-group" key={project.id} aria-label={`项目 ${project.name}`}>
          <div className={`project-row ${project.id === current?.project_id ? 'current-project' : ''}`}>
            <button className="project-toggle" onClick={() => setCollapsed(previous => ({ ...previous, [project.id]: !previous[project.id] }))}
              aria-expanded={expanded} aria-controls={`project-${project.id}`} title={project.workdir}>
              <ChevronRight size={12} className={expanded ? 'rotated' : ''} /><FolderIcon size={15} /><span>{project.name}</span>
            </button>
            <button className="icon-button project-new" onClick={() => onCreate(project.id)} disabled={busy} aria-label={`在 ${project.name} 中新建会话`} title="新建会话"><Plus size={15} /></button>
            {rowMenu('project', project, children.some(item => ['running', 'waiting', 'stopping'].includes(item.status)))}
          </div>
          {expanded && <div className="project-sessions" id={`project-${project.id}`}>
            {children.length ? children.map(item => <div key={item.id} className={`session-entry ${item.id === current?.id ? 'is-selected' : ''}`}><button className={`session-item ${item.id === current?.id ? 'selected' : ''}`}
              onClick={() => onSelect(item.id)} aria-current={item.id === current?.id ? 'page' : undefined} title={item.title}>
              <span>{item.title}</span>
              {['running', 'waiting', 'stopping'].includes(item.status) && <span className={`session-dot ${item.status}`} role="img" aria-label={statusLabels[item.status]} />}
            </button>{rowMenu('session', item, ['running', 'waiting', 'stopping'].includes(item.status))}</div>) : <p className="project-empty">暂无会话</p>}
          </div>}
        </section>;
      })}
    </nav>
    <div className="sidebar-bottom">
      <button className="workspace-button" onClick={onFolder} disabled={busy || !current || ['running', 'waiting', 'stopping'].includes(current.status)} title={current?.workdir}>
        <FolderOpen size={15} /><span>{projects.find(project => project.id === current?.project_id)?.name || folderName(current?.workdir) || '选择工作目录'}</span><span className="local-tag">本地</span>
      </button>
      <button className="settings-button" onClick={onSettings}><Settings size={16} /><span>设置</span></button>
    </div>
  </aside>;
}
