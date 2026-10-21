import React, { useRef, useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { Check, Copy } from 'lucide-react';
import { copyText } from '../chat.js';

function SafeLink({ href, children }) {
  return /^https?:\/\//i.test(href || '') ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
}

export function CopyButton({ text, label = '复制回复' }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState('');
  return <span className="copy-control"><button className="icon-button copy-button" aria-label={label} title={copied ? '已复制' : label} onClick={async () => {
    try { await copyText(typeof text === 'function' ? text() : text); setCopied(true); setError(''); }
    catch (failure) { setError(failure.message); }
  }}>{copied ? <Check size={14} /> : <Copy size={14} />}</button>{error && <span role="status">{error}</span>}</span>;
}

function CodeBlock({ children }) {
  const content = useRef(null);
  const language = /language-([\w+-]+)/.exec(children?.props?.className || '')?.[1] || '代码';
  return <div className="code-block"><div className="code-header"><span>{language}</span><CopyButton text={() => content.current?.textContent || ''} label="复制代码" /></div><pre ref={content}>{children}</pre></div>;
}

export default function MessageContent({ text }) {
  return <div className="markdown"><Markdown skipHtml remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeHighlight, { detect: false }]]} components={{ a: SafeLink, pre: CodeBlock, table: ({ children }) => <div className="markdown-table"><table>{children}</table></div> }}>{text}</Markdown></div>;
}
