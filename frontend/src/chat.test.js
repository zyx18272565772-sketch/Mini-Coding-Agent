import test from 'node:test';
import assert from 'node:assert/strict';
import { buildRequest, splitRequest } from './chat.js';

test('引用作为上下文发送，换行、引号和代码保留，当前请求单独标注', () => {
  const quote = { text: '第一行\n"引用" 和 `code` 😀' };
  const sent = buildRequest('  解释这段内容  ', quote);
  assert.match(sent, /仅供参考/);
  assert.deepEqual(splitRequest(sent), { quote: quote.text, text: '解释这段内容' });
  assert.equal(buildRequest('正常请求', null), '正常请求');
  assert.deepEqual(splitRequest('正常请求'), { quote: null, text: '正常请求' });
});

test('不是应用生成的引用格式时按用户原文显示', () => {
  const text = '引用的聊天内容（仅供参考）：\n坏 JSON\n\n当前请求：\n说明';
  assert.deepEqual(splitRequest(text), { quote: null, text });
});

test('文件引用只携带路径，与聊天引用一起往返，正文标题仍是当前请求', () => {
  const attachments = [{ kind: 'file', name: '中文.txt', path: 'D:\\示例\\中文.txt' }, { kind: 'folder', name: '代码', path: 'D:\\代码' }];
  const sent = buildRequest('检查这些内容', { text: '旧回复' }, attachments);
  assert.deepEqual(splitRequest(sent), { quote: '旧回复', text: '检查这些内容', attachments });
  assert.match(sent, /按需使用工具读取/);
  assert.equal(buildRequest('问题', null, []), '问题');
});
