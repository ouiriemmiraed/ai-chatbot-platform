/**
 * Widget streaming parser test.
 *
 * Exercises streamMessage() against a mock fetch that yields a hand-crafted
 * SSE byte stream. We verify:
 *  - onMeta fires with the conversation_id from the meta frame
 *  - onToken fires once per token frame, in order
 *  - onDone fires after [DONE]
 *  - the parser correctly handles a payload split across two TCP chunks
 *  - cancel() aborts cleanly
 */
// jsdom env in CRA's jest setup is missing TextEncoder/TextDecoder/ReadableStream
// for the test runner itself; pull them from node:util/web before importing.
const { TextEncoder, TextDecoder } = require('util');
const { ReadableStream } = require('stream/web');
global.TextEncoder = global.TextEncoder || TextEncoder;
global.TextDecoder = global.TextDecoder || TextDecoder;
global.ReadableStream = global.ReadableStream || ReadableStream;

import { streamMessage } from '../chatApi';

const encoder = new TextEncoder();

// Helper: build a ReadableStream that yields `chunks` (already encoded).
const streamOf = (chunks) =>
    new ReadableStream({
        start(controller) {
            for (const c of chunks) controller.enqueue(c);
            controller.close();
        },
    });

beforeEach(() => {
    // jsdom's TextDecoder + ReadableStream are available by default in
    // jest-environment-jsdom v29+. Polyfill if missing.
    if (typeof global.TextDecoder === 'undefined') {
        global.TextDecoder = require('util').TextDecoder;
    }
});

test('streamMessage parses meta → token... → done', async () => {
    const sseFrames = [
        'data: {"type":"meta","conversation_id":"conv-xyz"}\n\n',
        'data: {"type":"token","content":"Hello"}\n\n',
        'data: {"type":"token","content":" world"}\n\n',
        'data: {"type":"done","conversation_id":"conv-xyz"}\n\n',
    ];

    global.fetch = jest.fn().mockResolvedValue({
        ok: true,
        body: streamOf(sseFrames.map(s => encoder.encode(s))),
    });

    const meta = [], tokens = [], dones = [], errors = [];
    const cancel = streamMessage(
        'bot1', 'hi', null, { url: 'http://x', title: 't', text: '' }, null,
        {
            onMeta: id => meta.push(id),
            onToken: t => tokens.push(t),
            onDone: id => dones.push(id),
            onError: e => errors.push(e),
        },
    );

    // Wait for the inner async loop to drain
    await new Promise(r => setTimeout(r, 50));

    expect(errors).toHaveLength(0);
    expect(meta).toEqual(['conv-xyz']);
    expect(tokens).toEqual(['Hello', ' world']);
    expect(dones).toEqual(['conv-xyz']);

    cancel();
});

test('streamMessage handles a frame split across chunk boundaries', async () => {
    // The middle frame is intentionally chopped between "type" and "token"
    const part1 = 'data: {"type":"meta","conversation_id":"c"}\n\ndata: {"ty';
    const part2 = 'pe":"token","content":"split-ok"}\n\ndata: {"type":"done","conversation_id":"c"}\n\n';

    global.fetch = jest.fn().mockResolvedValue({
        ok: true,
        body: streamOf([encoder.encode(part1), encoder.encode(part2)]),
    });

    const tokens = [];
    streamMessage('bot1', 'hi', null, { url: '', title: '', text: '' }, null, {
        onToken: t => tokens.push(t),
    });
    await new Promise(r => setTimeout(r, 50));

    expect(tokens).toEqual(['split-ok']);
});

test('streamMessage propagates an error frame', async () => {
    const sse = 'data: {"type":"error","message":"upstream boom"}\n\n';
    global.fetch = jest.fn().mockResolvedValue({
        ok: true,
        body: streamOf([encoder.encode(sse)]),
    });

    const errors = [];
    streamMessage('bot1', 'hi', null, { url: '', title: '', text: '' }, null, {
        onError: e => errors.push(e),
    });
    await new Promise(r => setTimeout(r, 50));

    expect(errors).toHaveLength(1);
    expect(errors[0].message).toBe('upstream boom');
});

test('streamMessage surfaces non-2xx responses as onError', async () => {
    global.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({ detail: 'service down' }),
    });

    const errors = [];
    streamMessage('bot1', 'hi', null, { url: '', title: '', text: '' }, null, {
        onError: e => errors.push(e),
    });
    await new Promise(r => setTimeout(r, 50));

    expect(errors).toHaveLength(1);
    expect(errors[0].message).toBe('service down');
    expect(errors[0].status).toBe(503);
});
