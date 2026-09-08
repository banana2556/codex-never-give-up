// buildRetryRequest 的檢查：送出去的每個欄位都必須是實錄過的形狀
const assert = require('node:assert');
const h = require('./codex-retry-hook.js');

let n = 0;
const uuid = () => 'uuid-' + (++n);
const NOW = 1700000000000;
const now = () => NOW;

const tpl = {
  hostId: 'local',
  cwd: 'C:\\work\\myproject',
  runtimeWorkspaceRoots: ['C:\\work\\myproject', 'C:\\Users\\me\\.codex/visualizations/a'],
  collaborationMode: { mode: 'default', settings: { model: 'gpt-5.6-sol', reasoning_effort: 'xhigh', developer_instructions: null } },
  responsesapiClientMetadata: { workspace_kind: 'desktop' },
  multiAgentMode: 'explicitRequestOnly',
  summary: 'detailed',
  personality: 'friendly',
  serviceTier: 'default',
  // 模板裡有這個，但重送絕對不該把它帶出去（實錄的重試是送 null）
  permissions: ':danger-full-access',
};

const m = h.buildRetryRequest('TH-1', tpl, uuid, now);

// 信封
assert.strictEqual(m.type, 'mcp-request');
assert.strictEqual(m.hostId, 'local');
assert.strictEqual(m.priority, 'critical');
assert.strictEqual(m.source, 'turn');
assert.strictEqual(m.retainResponse, true);
assert.strictEqual(m.timeoutMs, 30000);
assert.strictEqual(m.expiresAtMs, NOW + 30000);
assert.strictEqual(m.request.method, 'turn/start');

const p = m.request.params;
assert.strictEqual(p.threadId, 'TH-1');
assert.strictEqual(p.turnTrigger, 'capacity_retry_manual');
assert.strictEqual(p.turnTrigger, h.RETRY_TRIGGER);

// 最重要的兩條：不重送使用者內容、不外帶 policy
assert.deepStrictEqual(p.input, [], 'input 必須是空陣列');
for (const k of h.NULL_FIELDS) {
  assert.strictEqual(p[k], null, `${k} 必須是 null（實錄的重試就是送 null）`);
}
assert.strictEqual(p.permissions, null, ':danger-full-access 不可被帶出去');

// per-thread 欄位要原封照抄
assert.strictEqual(p.cwd, tpl.cwd);
assert.deepStrictEqual(p.runtimeWorkspaceRoots, tpl.runtimeWorkspaceRoots);
assert.deepStrictEqual(p.collaborationMode, tpl.collaborationMode);
assert.deepStrictEqual(p.responsesapiClientMetadata, tpl.responsesapiClientMetadata);
assert.strictEqual(p.serviceTier, 'default');

// 兩個 id 必須是不同的新 uuid
assert.notStrictEqual(p.clientUserMessageId, m.request.id);
assert.match(p.clientUserMessageId, /^uuid-/);
assert.match(m.request.id, /^uuid-/);

// 缺料就拒絕，不要瞎送
assert.throws(() => h.buildRetryRequest('TH-2', null, uuid, now), /no template/);
assert.throws(() => h.buildRetryRequest(null, tpl, uuid, now), /no threadId/);

// 模板沒有某欄位時就不要無中生有
const thin = { hostId: 'local', cwd: 'C:\\p' };
const m2 = h.buildRetryRequest('TH-3', thin, uuid, now);
assert.ok(!('collaborationMode' in m2.request.params), '模板沒有的欄位不該憑空補上');
assert.strictEqual(m2.request.params.cwd, 'C:\\p');

// 背景重送的上限：serverOverloaded 無限
assert.strictEqual(h.CFG['ipc:serverOverloaded'].max, Infinity);
assert.ok(!h.CFG['ipc:usageLimitExceeded'], '配額類不該有背景重送設定');

console.log('ok  buildRetryRequest：信封 / 觸發器 / 空 input / policy 全 null / 模板照抄 / id 唯一 / 缺料拒絕');

// --- 最小參數退路：只能有讀到的欄位，不可編造 ---
{
  const m3 = h.buildMinimalRetryRequest('TH-9', {cwd: 'C:\proj', model: 'gpt-6-astra',
                                                 reasoningEffort: 'medium'}, uuid, now);
  const q = m3.request.params;
  assert.strictEqual(q.threadId, 'TH-9');
  assert.strictEqual(q.turnTrigger, h.RETRY_TRIGGER);
  assert.deepStrictEqual(q.input, []);
  assert.strictEqual(q.cwd, 'C:\proj');
  for (const k of h.NULL_FIELDS) assert.strictEqual(q[k], null, k);
  // 這些讀不到，就絕對不能出現
  for (const k of ['collaborationMode', 'runtimeWorkspaceRoots', 'multiAgentMode',
                   'summary', 'personality', 'responsesapiClientMetadata']) {
    assert.ok(!(k in q), `${k} 讀不到就不該被編造出來`);
  }
  // model/effort 讀得到也不該塞進去（實錄的重試是送 null，由 server 解）
  assert.strictEqual(q.model, null);
  assert.strictEqual(q.effort, null);
  assert.strictEqual(m3._minimal, true);
  assert.throws(() => h.buildMinimalRetryRequest('T', null, uuid, now), /no threadInfo/);
  console.log('ok  buildMinimalRetryRequest：只填讀到的欄位，不編造 collaborationMode');
}

// --- 模板落地的修剪邏輯：熱換/重開不能把模板弄丟，但也不能無限長大 ---
{
  const NOWMS = 1700000000000;
  const day = 86400000;
  const e = (id, ageDays) => [id, {at: NOWMS - ageDays * day, cwd: "/p/" + id}];
  // 過期的要丟
  let out = h.pruneTemplates([e('a', 1), e('b', 40), e('c', 2)], NOWMS, 50, h.TPL_TTL_MS);
  assert.deepStrictEqual(out.map(x => x[0]), ['a', 'c'], '40 天前的該被丟掉');
  // 新的排前面
  out = h.pruneTemplates([e('old', 5), e('new', 0)], NOWMS, 50, h.TPL_TTL_MS);
  assert.strictEqual(out[0][0], 'new');
  // 超量砍最舊
  const many = Array.from({length: 80}, (_, i) => e('t' + i, i * 0.1));
  out = h.pruneTemplates(many, NOWMS, h.TPL_MAX, h.TPL_TTL_MS);
  assert.strictEqual(out.length, h.TPL_MAX);
  assert.strictEqual(out[0][0], 't0', '最新的要留著');
  // 壞資料不能炸
  assert.deepStrictEqual(h.pruneTemplates([['x', null], ['y', {}], e('z', 0)],
                                          NOWMS, 50, h.TPL_TTL_MS).map(x => x[0]), ['z']);
  assert.deepStrictEqual(h.pruneTemplates([], NOWMS, 50, h.TPL_TTL_MS), []);
  console.log(`ok  pruneTemplates：TTL ${h.TPL_TTL_MS / day} 天 / 上限 ${h.TPL_MAX} 筆 / 壞資料不炸`);
}
