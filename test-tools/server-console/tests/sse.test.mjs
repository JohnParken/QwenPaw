import {test} from 'node:test';
import assert from 'node:assert/strict';
import {SseParser} from '../web/sse.js';

test('SSE CRLF split at every boundary preserves multiline data and cursor', () => {
  const wire = ': ping\r\nid: 5\r\nevent: terminal\r\ndata: {"text":\r\ndata: "hello"}\r\n\r\n';
  for (let cut = 0; cut <= wire.length; cut++) {
    const parser = new SseParser();
    assert.deepEqual([...parser.push(wire.slice(0, cut)), ...parser.push(wire.slice(cut))], [{id:'5', event:'terminal', data:'{"text":\n"hello"}'}]);
  }
});
test('SSE limits unmatched frames and handles coalesced events', () => {
  assert.throws(() => new SseParser(8).push('data: too long'), /limit/);
  assert.equal(new SseParser().push('data: 1\n\ndata: 2\n\n').length, 2);
});
