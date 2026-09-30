import test from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '../../src/web/static/client.js';
test('client encodes opaque identifiers, sends nonce and presents domain errors in Chinese', async () => {
  const prior = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: calls.length !== 3,
      status: 409,
      json: async () =>
        calls.length === 3
          ? { error: { code: 'already_exists', message: 'name occupied' } }
          : { document: { id: 'a' } },
    };
  };
  try {
    const client = new Client();
    client.nonce = 'local-nonce';
    await client.readDocument('opaque /?&中文');
    assert.equal(
      new URL(calls[0].url, 'http://localhost').searchParams.get('object_id'),
      'opaque /?&中文',
    );
    await client.saveDocument({
      object_id: 'a',
      content: 'draft',
      expected_revision_id: 'r1',
    });
    assert.equal(calls[1].options.headers['X-C156-Nonce'], 'local-nonce');
    assert.deepEqual(JSON.parse(calls[1].options.body), {
      object_id: 'a',
      content: 'draft',
      expected_revision_id: 'r1',
    });
    await assert.rejects(
      client.createFolder('a', 'b'),
      (e) => e.code === 'already_exists' && e.message.includes('同名'),
    );
  } finally {
    globalThis.fetch = prior;
  }
});
