const quotePrefix = '引用的聊天内容（仅供参考）：\n';
const requestSeparator = '\n\n当前请求：\n';
const attachmentSeparator = '\n\n引用的本地文件和文件夹（仅供参考，按需使用工具读取）：\n';

export function buildRequest(draft, quote, attachments = []) {
  const text = draft.trim();
  const request = quote?.text ? `${quotePrefix}${JSON.stringify(quote.text)}${requestSeparator}${text}` : text;
  return attachments.length ? `${request}${attachmentSeparator}${JSON.stringify(attachments)}` : request;
}

export function splitRequest(text) {
  const attachmentBoundary = text.lastIndexOf(attachmentSeparator);
  if (attachmentBoundary !== -1) {
    try {
      const attachments = JSON.parse(text.slice(attachmentBoundary + attachmentSeparator.length));
      if (Array.isArray(attachments) && attachments.length && attachments.every(item => item && ['file', 'folder'].includes(item.kind) && typeof item.name === 'string' && typeof item.path === 'string')) {
        return { ...splitRequest(text.slice(0, attachmentBoundary)), attachments };
      }
    } catch { /* 普通消息保留原文，不把无效引用解析为附件。 */ }
  }
  if (text.startsWith(quotePrefix)) {
    const boundary = text.indexOf(requestSeparator, quotePrefix.length);
    if (boundary !== -1) {
      try {
        const quote = JSON.parse(text.slice(quotePrefix.length, boundary));
        if (typeof quote === 'string') return { quote, text: text.slice(boundary + requestSeparator.length) };
      } catch { /* 普通用户原文不按引用格式拆分。 */ }
    }
  }
  return { quote: null, text };
}

export async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch { /* 部分 WebView2 环境没有异步剪贴板权限，使用复制命令。 */ }
  const saved = [];
  const selection = window.getSelection();
  for (let index = 0; selection && index < selection.rangeCount; index++) saved.push(selection.getRangeAt(index).cloneRange());
  const active = document.activeElement;
  const input = document.createElement('textarea');
  input.value = text;
  input.className = 'clipboard-input';
  document.body.append(input);
  try {
    input.select();
    if (!document.execCommand('copy')) throw new Error('复制失败，请使用 Ctrl+C。');
  } finally {
    input.remove();
    active?.focus({ preventScroll: true });
    selection?.removeAllRanges();
    for (const range of saved) selection?.addRange(range);
  }
}
