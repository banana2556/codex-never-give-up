"""只錄 input 為空的 turn/start（= 重試），拿它的 turnTrigger 字面值。"""
import asyncio, json, sys, urllib.request
import aiohttp
PORT = 9333
SETUP = r"""
(() => {
  const old = window.__rtCap;
  if (old && old.h) {
    window.removeEventListener('codex-message-from-view', old.h, true);
    document.removeEventListener('codex-message-from-view', old.h, true);
  }
  // 也把上一版收乾淨
  const lit = window.__litCap;
  if (lit && lit.h) {
    window.removeEventListener('codex-message-from-view', lit.h, true);
    document.removeEventListener('codex-message-from-view', lit.h, true);
    delete window.__litCap;
  }
  const SAFE = ['turnTrigger','approvalPolicy','approvalsReviewer','permissions',
                'serviceTier','multiAgentMode','summary','personality',
                'sandboxPolicy','model','effort'];
  const C = window.__rtCap = {armedAt: Date.now(), retries: [], userTurns: 0};
  C.h = (e) => {
    const d = e.detail;
    if (!d || d.type !== 'mcp-request') return;
    const rq = d.request;
    if (!rq || rq.method !== 'turn/start') return;
    const p = rq.params || {};
    const n = Array.isArray(p.input) ? p.input.length : -1;
    if (n !== 0) { C.userTurns += 1; return; }      // 有 input 的是使用者 turn，跳過
    const o = {_inputLen: n, _at: new Date().toLocaleTimeString()};
    for (const k of SAFE) if (k in p) o[k] = p[k];
    o._paramKeys = Object.keys(p);
    o._envelope = {priority: d.priority, source: d.source,
                   retainResponse: d.retainResponse, timeoutMs: d.timeoutMs};
    if (C.retries.length < 20) C.retries.push(o);
  };
  window.addEventListener('codex-message-from-view', C.h, true);
  document.addEventListener('codex-message-from-view', C.h, true);
  return 'armed (只收 input 為空的 turn/start)';
})()
"""
READ = ("(()=>{const c=window.__rtCap;return c?JSON.stringify("
        "{armedSecAgo:Math.round((Date.now()-c.armedAt)/1000),"
        "userTurnsSkipped:c.userTurns,retries:c.retries}):'NOT ARMED';})()")

async def run(expr, all_pages=False):
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as r:
        tg = [t for t in json.loads(r.read())
              if t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
    if not all_pages:
        tg = [t for t in tg if t.get('url', '').endswith('/index.html')] or tg[:1]
    out = []
    async with aiohttp.ClientSession() as s:
        for t in tg:
            try:
                async with s.ws_connect(t['webSocketDebuggerUrl'], max_msg_size=0) as ws:
                    await ws.send_json({'id': 1, 'method': 'Runtime.evaluate',
                                        'params': {'expression': expr, 'returnByValue': True}})
                    while True:
                        m = json.loads(await ws.receive_str())
                        if m.get('id') == 1:
                            break
                out.append((t.get('url', '?')[-32:],
                            (m.get('result') or {}).get('result', {}).get('value')))
            except Exception as e:
                out.append((t.get('url', '?')[-32:], f'ERR {e}'))
    return out

if '--read' in sys.argv:
    for u, v in asyncio.run(run(READ)):
        print(v)
else:
    for u, v in asyncio.run(run(SETUP, all_pages=True)):
        print(f'  {u:34s} {v}')
