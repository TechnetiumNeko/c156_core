import test from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '../../src/web/static/client.js';
test('client encodes opaque identifiers, sends CSRF and presents domain errors in Chinese', async () => {
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
    client.csrf = 'session-csrf';
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
    assert.equal(calls[1].options.headers['X-C156-CSRF'], 'session-csrf');
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
test('auth nonce, typed management bodies and stale response isolation',async()=>{
  const previous=globalThis.fetch;const calls=[];let resolve;
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,status:200,json:async()=>url.includes('bootstrap')?{user:{id:'a'},csrf:'csrf'}:{ok:true}};};
  try{
    const c=new Client();c.nonce='nonce';await c.bootstrap();assert.equal(c.nonce,'nonce');
    await c.login('alice','  unchanged password  ');assert.equal(calls[1].options.headers['X-C156-Nonce'],'nonce');assert.equal(JSON.parse(calls[1].options.body).password,'  unchanged password  ');
    await c.siteAdmin('a',false,3);assert.deepEqual(JSON.parse(calls[2].options.body),{user_id:'a',enabled:false,expected_version:3});assert.equal(calls[2].options.headers['X-C156-CSRF'],'csrf');
    await c.deleteRule({object_id:'d',subject_type:'role',subject_key:'editor',action:'edit',effect:'deny',expected_version:4});assert.equal('effect' in JSON.parse(calls[3].options.body),false);
    globalThis.fetch=()=>new Promise(r=>{resolve=r;});const pending=c.bootstrap();c.invalidate();resolve({ok:true,json:async()=>({nonce:'old'})});await assert.rejects(pending,e=>e.code==='stale');assert.equal(c.nonce,'nonce');assert.equal('session_token' in c,false);
  }finally{globalThis.fetch=previous;}
});
