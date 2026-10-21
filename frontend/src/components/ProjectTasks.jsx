import React from 'react';
import { Check, Circle, ListChecks, LoaderCircle, LockKeyhole } from 'lucide-react';
import { taskStatus } from '../state.js';

export default function ProjectTasks({ tasks = [], error, sessionId }) {
  if (!tasks.length && !error) return null;
  const completed = tasks.filter(task => task.status === 'completed').length;
  const names = new Map(tasks.map(task => [task.id, task.subject]));
  return <details className="project-tasks">
    <summary><ListChecks size={16} /><span>项目任务</span><span className="task-count">{error ? '读取失败' : `${completed}/${tasks.length} 已完成`}</span><span className="task-shared">同项目会话共享</span></summary>
    {error ? <p className="task-error" role="alert">{error}</p> : <ul aria-label="项目任务清单">
      {tasks.map(task => {
        const status = taskStatus(task);
        const Icon = task.status === 'completed' ? Check : task.status === 'in_progress' ? LoaderCircle : status === '等待依赖' ? LockKeyhole : Circle;
        const waiting = task.incompleteDependencies || [];
        const owner = task.owner === sessionId ? '当前会话' : task.owner === 'agent' ? '命令行' : '其他会话';
        return <li key={task.id} data-task-id={task.id}>
          <Icon size={15} className="task-icon" aria-hidden="true" />
          <div className="task-info"><div className="task-heading"><span>{task.subject}</span><span className="task-state">{status}</span></div>
            <div className="task-meta"><code>{task.id}</code>{task.owner && <span title={task.owner}>领取者：{owner}</span>}</div>
            {task.description && <p>{task.description}</p>}
            {waiting.length > 0 ? <p className="task-dependencies">等待：{waiting.map(id => names.get(id) || id).join('、')}</p> : task.blockedBy.length > 0 && <p className="task-dependencies">前置任务已完成</p>}
          </div>
        </li>;
      })}
    </ul>}
  </details>;
}
