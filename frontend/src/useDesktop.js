import { useEffect, useRef, useState } from 'react';
import { call } from './bridge.js';
import { mergeSnapshot } from './state.js';


export function useDesktop() {
  const [sessions, setSessions] = useState([]);
  const [projects, setProjects] = useState([]);
  const [settings, setSettings] = useState({ theme: 'light', font_size: 15 });
  const [model, setModel] = useState('未配置模型');
  const [current, setCurrent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const selected = useRef(null);
  const snapshot = useRef(current);
  snapshot.current = current;

  async function select(id) {
    selected.current = id;
    setLoading(true);
    try {
      const session = await call('get_session', id);
      if (selected.current === id) setCurrent(previous => previous?.id === id ? mergeSnapshot(previous, session) : session);
    } catch (err) {
      if (selected.current === id) setError(err.message);
    } finally {
      if (selected.current === id) setLoading(false);
    }
  }

  async function initialize() {
    setLoading(true);
    setError('');
    try {
      const data = await call('bootstrap');
      setSessions(data.sessions);
      setProjects(data.projects);
      setSettings(data.settings);
      setModel(data.model);
      if (data.warnings.length) setError(data.warnings.join('\n'));
      if (data.sessions.length) await select(data.sessions[0].id);
      else if (data.projects.length) {
        const session = await call('create_session', data.projects[0].id);
        selected.current = session.id;
        setCurrent(session);
        setSessions([session]);
      } else {
        selected.current = null;
        setCurrent(null);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { initialize(); }, []);

  useEffect(() => {
    if (!current?.id) return;
    let cancelled = false;
    let timer;
    const id = current.id;
    async function poll() {
      try {
        const after = snapshot.current?.id === id ? snapshot.current.events.at(-1)?.seq || 0 : 0;
        const data = await call('poll', id, after);
        if (!cancelled && selected.current === id) {
          setSessions(data.sessions);
          setProjects(data.projects);
          setCurrent(previous => previous?.id === id ? mergeSnapshot(previous, {
            events: data.events, cursor: data.cursor, status: data.status, pending_approval: data.pending_approval,
            tasks: data.tasks, task_error: data.task_error, usage: data.usage, usage_total: data.usage_total,
            failure: data.failure,
          }) : previous);
        }
      } catch (err) {
        if (!cancelled && selected.current === id) setError(err.message);
      } finally {
        if (!cancelled) timer = setTimeout(poll, snapshot.current?.status === 'running' ? 150 : 500);
      }
    }
    timer = setTimeout(poll, 300);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [current?.id]);

  async function perform(method, ...args) {
    setBusy(true);
    setError('');
    try {
      let data = await call(method, ...args);
      if (method === 'pick_attachments') return data;
      if (method === 'create_project') {
        if (!data) return false;
        data = await call('create_session', data.id);
      }
      if (method === 'create_session' || method === 'create_project') {
        selected.current = data.id;
        setCurrent(data);
        setSessions(previous => [data, ...previous]);
      } else if (method === 'set_theme' || method === 'set_font_size') {
        setSettings(data);
      } else if (method === 'rename_project') {
        setProjects(previous => previous.map(project => project.id === data.id ? data : project));
      } else if (method === 'delete_session' || method === 'delete_project') {
        const navigation = await call('bootstrap');
        setProjects(navigation.projects);
        setSessions(navigation.sessions);
        if (data.deleted_session_ids.includes(selected.current)) {
          selected.current = null;
          setCurrent(null);
          if (navigation.sessions.length) await select(navigation.sessions[0].id);
        }
      } else if (selected.current === args[0]) {
        setCurrent(previous => mergeSnapshot(previous, data));
      }
      if (['create_session', 'create_project', 'choose_workspace', 'rename_project', 'rename_session'].includes(method)) {
        const navigation = await call('bootstrap');
        setProjects(navigation.projects);
        setSessions(navigation.sessions);
      }
      return true;
    } catch (err) {
      setError(err.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  return { sessions, projects, settings, model, current, loading, busy, error, select, perform, initialize, dismissError: () => setError('') };
}
