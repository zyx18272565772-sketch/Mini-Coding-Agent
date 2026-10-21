import React, { useEffect, useState } from 'react';
import { AlertCircle, ChevronRight, FolderOpen, PanelLeftOpen, RefreshCw, X } from 'lucide-react';
import { useDesktop } from './useDesktop.js';
import { folderName, statusLabels } from './state.js';
import Sidebar from './components/Sidebar.jsx';
import Conversation from './components/Conversation.jsx';
import Composer from './components/Composer.jsx';
import SettingsDialog from './components/SettingsDialog.jsx';
import ProjectTasks from './components/ProjectTasks.jsx';
import UsagePanel from './components/UsagePanel.jsx';
import RetryPanel from './components/RetryPanel.jsx';
import DeleteDialog from './components/DeleteDialog.jsx';
import RenameDialog from './components/RenameDialog.jsx';
import { buildRequest } from './chat.js';

export default function App() {
  const { sessions, projects, settings, model, current, loading, busy, error, select, perform, initialize, dismissError } = useDesktop();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [deletion, setDeletion] = useState(null);
  const [renaming, setRenaming] = useState(null);
  useEffect(() => { document.documentElement.dataset.theme = settings.theme; }, [settings.theme]);
  useEffect(() => { document.documentElement.style.setProperty('--font-size', `${settings.font_size || 15}px`); }, [settings.font_size]);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 760);
  const [compact, setCompact] = useState(() => window.innerWidth < 760);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 759px)');
    const update = () => setCompact(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  const [drafts, setDrafts] = useState({});
  const [quotes, setQuotes] = useState({});
  const [references, setReferences] = useState({});
  const id = current?.id;
  const projectName = projects.find(project => project.id === current?.project_id)?.name;
  const draft = drafts[id] || '';
  const quote = quotes[id];
  const attachments = references[id] || [];
  const setDraft = text => setDrafts(previous => ({ ...previous, [id]: text }));
  const chooseFolder = () => id && perform('choose_workspace', id);
  async function addAttachment(kind) {
    const targetId = id;
    const chosen = await perform('pick_attachments', targetId, kind);
    if (Array.isArray(chosen) && chosen.length) setReferences(previous => {
      const items = previous[targetId] || [];
      return { ...previous, [targetId]: [...items, ...chosen.filter(item => !items.some(old => old.kind === item.kind && old.path === item.path))] };
    });
  }
  async function send() {
    const sentId = id;
    const sentDraft = draft;
    const sentQuote = quote;
    const sentAttachments = attachments;
    if (await perform('send_message', sentId, buildRequest(sentDraft, sentQuote, sentAttachments))) {
      setDrafts(previous => previous[sentId] === sentDraft ? { ...previous, [sentId]: '' } : previous);
      setQuotes(previous => previous[sentId] === sentQuote ? { ...previous, [sentId]: null } : previous);
      setReferences(previous => ({ ...previous, [sentId]: (previous[sentId] || []).filter(item => !sentAttachments.includes(item)) }));
    }
  }
  function selectSession(sessionId) {
    select(sessionId);
    if (window.innerWidth < 760) setSidebarOpen(false);
  }
  useEffect(() => {
    function shortcut(event) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'n' && !settingsOpen && !deletion && !renaming) {
        event.preventDefault();
        if (!busy && !loading) perform('create_session', current?.project_id || null);
      }
    }
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [current?.project_id, busy, loading, settingsOpen, deletion, renaming]);
  async function confirmDelete() {
    if (await perform(deletion.kind === 'project' ? 'delete_project' : 'delete_session', deletion.id)) setDeletion(null);
  }
  async function confirmRename(name) {
    if (await perform(renaming.kind === 'project' ? 'rename_project' : 'rename_session', renaming.id, name)) setRenaming(null);
  }
  return <div className={`app ${sidebarOpen ? 'sidebar-visible' : ''}`}>
    {sidebarOpen && <button className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} aria-label="关闭会话导航" />}
    <Sidebar sessions={sessions} projects={projects} current={current} onSelect={selectSession} onCreate={projectId => perform('create_session', projectId || current?.project_id || null)} onCreateProject={() => perform('create_project')} onDeleteSession={item => setDeletion({ kind: 'session', id: item.id, name: item.title })} onDeleteProject={item => setDeletion({ kind: 'project', id: item.id, name: item.name, count: sessions.filter(session => session.project_id === item.id).length })} onRename={setRenaming} onFolder={chooseFolder} onSettings={() => setSettingsOpen(true)} onClose={() => setSidebarOpen(false)} busy={busy || loading} open={sidebarOpen} />
    <main className="main-panel" inert={compact && sidebarOpen}>
      <header className="topbar">
        {!sidebarOpen && <button className="icon-button" onClick={() => setSidebarOpen(true)} aria-label="展开侧栏" title="展开侧栏"><PanelLeftOpen size={18} /></button>}
        <div className="header-title"><span className="header-project" title={current?.workdir}><FolderOpen size={14} />{projectName || folderName(current?.workdir) || '工作区'}</span><ChevronRight className="breadcrumb-slash" size={13} /><span className="conversation-title">{current?.title || '新会话'}</span></div>
        <div className={`status-pill ${current?.status || 'idle'}`} role="status"><span />{loading ? '载入中' : statusLabels[current?.status] || '未连接'}</div>
      </header>
      {error && <div className="error-banner" role="alert"><AlertCircle size={16} /><span>{error}</span>{!current && <button className="text-button" onClick={initialize}><RefreshCw size={14} />重试连接</button>}<button className="icon-button" onClick={dismissError} aria-label="关闭错误提示"><X size={15} /></button></div>}
      <Conversation key={current?.id || 'empty'} session={current} projectName={projectName} loading={loading} onSuggestion={setDraft} onQuote={value => setQuotes(previous => ({ ...previous, [id]: value }))} />
      <div className="conversation-controls">
      {!loading && <ProjectTasks key={current?.id} tasks={current?.tasks} error={current?.task_error} sessionId={current?.id} />}
      <UsagePanel session={current} loading={loading} />
      <RetryPanel session={current} onRetry={() => perform('retry_run', id)} busy={busy} loading={loading} />
      <Composer session={current} draft={draft} quote={quote} onRemoveQuote={() => setQuotes(previous => ({ ...previous, [id]: null }))} attachments={attachments} onAddAttachment={addAttachment} onRemoveAttachment={item => setReferences(previous => ({ ...previous, [id]: (previous[id] || []).filter(old => old !== item) }))} projectName={projectName} model={current?.model || model} onDraft={setDraft} onSend={send} onStop={() => perform('stop', id)} onPermission={(requestId, allow) => perform('resolve_permission', id, requestId, allow)} busy={busy} loading={loading} />
      </div>
    </main>
    <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} theme={settings.theme} onTheme={theme => perform('set_theme', theme)} fontSize={settings.font_size || 15} onFontSize={size => perform('set_font_size', size)} busy={busy} />
    <DeleteDialog target={deletion} onClose={() => setDeletion(null)} onConfirm={confirmDelete} busy={busy} />
    <RenameDialog target={renaming} onClose={() => setRenaming(null)} onConfirm={confirmRename} busy={busy} />
  </div>;
}
