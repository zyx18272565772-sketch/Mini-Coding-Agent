import React from 'react';
import { RefreshCw } from 'lucide-react';

export default function RetryPanel({ session, onRetry, busy, loading }) {
  if (loading || session?.status !== 'error' || !session.failure?.retryable) return null;
  return <section className="retry-panel" aria-label="失败后继续">
    <div><strong>{session.failure.message}</strong><p>进度已保存，继续会保留本次任务和累计用量。</p></div>
    <button onClick={onRetry} disabled={busy}><RefreshCw size={14} />继续执行</button>
  </section>;
}
