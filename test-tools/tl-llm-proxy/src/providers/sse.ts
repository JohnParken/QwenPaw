import { ProxyError } from '../errors.js';

export interface SseRecord {
  /** The complete event as received, including its terminating blank line. */
  raw: string;
  /** The optional event name. */
  event: string | undefined;
  /** The joined data fields, or null for a comment/empty event. */
  data: string | null;
}

interface EventState {
  raw: string;
  event: string | undefined;
  data: string[];
  bytes: number;
}

/**
 * Incremental Server-Sent Events parser.
 *
 * It deliberately keeps only the current event and the current unterminated
 * line in memory.  The parser accepts all line endings allowed by the SSE
 * format, including CRLF split between two input chunks.
 */
export class SseParser {
  private buffer = '';
  private state: EventState = { raw: '', event: undefined, data: [], bytes: 0 };

  public constructor(private readonly maxEventBytes: number) {
    if (!Number.isSafeInteger(maxEventBytes) || maxEventBytes <= 0) {
      throw new ProxyError(500, 'Invalid SSE event limit');
    }
  }

  /** Feed decoded UTF-8 text and return every complete event found in it. */
  public feed(text: string): SseRecord[] {
    if (text.length === 0) return [];
    this.buffer += text;

    const records: SseRecord[] = [];
    let lineStart = 0;
    for (let index = 0; index < this.buffer.length; index += 1) {
      const character = this.buffer[index];
      if (character === '\n') {
        const line = this.buffer.slice(lineStart, index);
        records.push(...this.consumeLine(line, '\n'));
        lineStart = index + 1;
        continue;
      }
      if (character !== '\r') continue;

      // A CR at the end of this feed may be the first half of a CRLF split
      // across network reads.  Retain it until the next feed.
      if (index + 1 >= this.buffer.length) break;
      const hasLf = this.buffer[index + 1] === '\n';
      const line = this.buffer.slice(lineStart, index);
      records.push(...this.consumeLine(line, hasLf ? '\r\n' : '\r'));
      if (hasLf) index += 1;
      lineStart = index + 1;
    }

    this.buffer = this.buffer.slice(lineStart);
    this.assertPendingLimit();
    return records;
  }

  /**
   * Finish parsing at EOF.  SSE events must have a blank-line terminator;
   * returning a partial record would make an incomplete upstream response
   * look successful to the caller.
   */
  public finish(): SseRecord[] {
    const records: SseRecord[] = [];
    // A trailing CR is itself a valid SSE line ending. During feed() it is
    // retained because another chunk may add LF; at EOF that ambiguity ends.
    if (this.buffer.endsWith('\r')) {
      const line = this.buffer.slice(0, -1);
      this.buffer = '';
      records.push(...this.consumeLine(line, '\r'));
    }
    if (this.buffer.length !== 0 || this.state.raw.length !== 0) {
      throw new ProxyError(502, 'Upstream SSE ended with an incomplete event');
    }
    return records;
  }

  private consumeLine(line: string, ending: string): SseRecord[] {
    this.addBytes(line, ending);
    this.state.raw += line + ending;

    if (line.length === 0) {
      const record: SseRecord = {
        raw: this.state.raw,
        event: this.state.event,
        data: this.state.data.length === 0 ? null : this.state.data.join('\n'),
      };
      this.state = { raw: '', event: undefined, data: [], bytes: 0 };
      return [record];
    }

    // A line beginning with ':' is an SSE comment and has no field value.
    if (line.startsWith(':')) return [];
    const separator = line.indexOf(':');
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? '' : line.slice(separator + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'data') this.state.data.push(value);
    else if (field === 'event') this.state.event = value;
    return [];
  }

  private addBytes(line: string, ending: string): void {
    this.state.bytes += utf8ByteLength(line) + utf8ByteLength(ending);
    this.assertEventLimit();
  }

  private assertPendingLimit(): void {
    if (this.state.bytes + utf8ByteLength(this.buffer) > this.maxEventBytes) {
      throw new ProxyError(502, 'Upstream SSE event exceeds the configured limit');
    }
  }

  private assertEventLimit(): void {
    if (this.state.bytes > this.maxEventBytes) {
      throw new ProxyError(502, 'Upstream SSE event exceeds the configured limit');
    }
  }
}

export function utf8ByteLength(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}
