"""實測模板落地：派發假 turn/start -> 檢查 localStorage -> 熱換後還在嗎 -> 清乾淨。"""
import asyncio, json, sys, urllib.request
import aiohttp
PORT = 9333
STEP = sys.argv[1]

FAKE = r"""
(() => {
  const h = window.__codexRetryHook;
  if (!h) return 'NOHOOK';
  // 完全不送出任何東西，只是讓 hook 的 onOutbound 收到一則假的送出事件
  window.dispatchEvent(new CustomEvent('codex-message-from-view', {detail: {
    type: 'mcp-request', hostId: 'local',
    request: {id: 'fake-req', method: 'turn/start', params: {
      threadId: 'ZZZTEST-persist-check',
      input: [{type: 'text', text: '這不會被存進快取'}],
      cwd: 'C:/verify', runtimeWorkspaceRoots: ['C:/verify'],
      collaborationMode: {mode: 'default', settings: {model: 'test-model',
                          reasoning_effort: 'low', developer_instructions: null}},
      responsesapiClientMetadata: {workspace_kind: 'test'},
      multiAgentMode: 'explicitRequestOnly', summary: 'detailed',
      personality: 'friendly', serviceTier: 'default',
    }},
  }}));
  const t = h.templates()['ZZZTEST-persist-check'];
  let raw = null;
  try { raw = localStorage.getItem('codexRetryHook.templates.v1'); } catch (e) {}
  return JSON.stringify({
    inMemory: !!t,
    fields: t ? Object.keys(t).sort() : null,
    hasInput: t ? ('input' in t) : null,
    storedBytes: raw ? raw.length : 0,
    storedHasTest: raw ? raw.includes('ZZZTEST-persist-check') : false,
    storedHasSecret: raw ? raw.includes('這不會被存進快取') : null,
  });
})()
"""
CHECK = r"""
(() => {
  const h = window.__codexRetryHook;
  if (!h) return 'NOHOOK';
  const t = h.templates()['ZZZTEST-persist-check'];
  return JSON.stringify({survived: !!t, cwd: t ? t.cwd : null,
                         total: Object.keys(h.templates()).length});
})()
"""
CLEANUP = r"""
(() => {
  const h = window.__codexRetryHook;
  if (!h) return 'NOHOOK';
  const all = h.templates();
  delete all['ZZZTEST-persist-check'];
  h.clearTemplates();
  let raw = null;
  try { raw = localStorage.getItem('codexRetryHook.templates.v1'); } catch (e) {}
  return JSON.stringify({cleared: true, remaining: Object.keys(h.templates()).length,
                         storedHasTest: raw ? raw.includes('ZZZTEST') : false});
})()
"""
EXPR = {'fake': FAKE, 'check': CHECK, 'cleanup': CLEANUP}[STEP]

async def main():
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as r:
        tg = [t for t in json.loads(r.read())
              if t.get('type') == 'page' and t.get('url', '').endswith('/index.html')]
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(tg[0]['webSocketDebuggerUrl'], max_msg_size=0) as ws:
            await ws.send_json({'id': 1, 'method': 'Runtime.evaluate',
                                'params': {'expression': EXPR, 'returnByValue': True}})
            while True:
                m = json.loads(await ws.receive_str())
                if m.get('id') == 1:
                    break
    v = (m.get('result') or {}).get('result', {}).get('value')
    try: print(json.dumps(json.loads(v), ensure_ascii=False, indent=1))
    except Exception: print(v)
asyncio.run(main())
