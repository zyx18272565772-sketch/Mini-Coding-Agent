import React from 'react';
import { ChevronRight, Gauge } from 'lucide-react';

const labels = { main: '主 Agent', subagent: '子 Agent', memory: '记忆处理', compact: '上下文压缩' };
const number = value => (value || 0).toLocaleString('zh-CN');

export default function UsagePanel({ session, loading }) {
  if (loading) return null;
  const usage = session?.usage;
  const total = session?.usage_total;
  const uncertain = usage?.unknown_calls || usage?.pending_calls;
  return <section className="usage-panel" aria-label="模型用量">
    <div className="usage-toolbar">
      <span className="usage-label"><Gauge size={14} />模型用量</span>
      <span className="usage-session-total">会话累计 {number(total?.calls)} 次 · {total?.unknown_calls || total?.pending_calls ? '已知 ' : ''}{number(total?.total_tokens)} Token</span>
    </div>
    {usage && <details className="usage-details">
      <summary><ChevronRight size={14} /><span>本次任务 {number(usage.calls)} 次请求</span><span>{uncertain ? '已知 ' : ''}{number(usage.total_tokens)} Token</span></summary>
      <div className="usage-detail-body">
        <div className="usage-table-scroll"><table><thead><tr><th>用途</th><th>请求</th><th>输入</th><th>输出</th><th>缓存读 / 写</th></tr></thead><tbody>
          {Object.entries(labels).map(([key, label]) => {
            const bucket = usage.by_purpose[key] || {};
            return <tr key={key}><th scope="row">{label}</th><td>{number(bucket.calls)}</td><td>{number(bucket.input_tokens)}</td><td>{number(bucket.output_tokens)}</td><td>{number(bucket.cache_read_tokens)} / {number(bucket.cache_creation_tokens)}</td></tr>;
          })}
        </tbody></table></div>
        {!!usage.pending_calls && <p>有 {usage.pending_calls} 次请求正在等待完整用量。</p>}
        {!!usage.unknown_calls && <p>有 {usage.unknown_calls} 次请求用量不完整；已知数值是下限，不能视为完整账单。</p>}
        <p>累计统计每次请求的输入和输出；重复发送的历史也会再次计入。</p>
        <p>会话累计：输入 {number(total?.input_tokens)}，输出 {number(total?.output_tokens)}，缓存读 / 写 {number(total?.cache_read_tokens)} / {number(total?.cache_creation_tokens)}。统计启用前的历史未补算。</p>
      </div>
    </details>}
  </section>;
}
