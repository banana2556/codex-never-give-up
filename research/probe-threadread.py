"""唯讀測 thread/read：看能不能直接拿到 cwd / collaborationMode 等模板欄位。"""
import asyncio, json, sys, urllib.request
import aiohttp
PORT = 9333
METHOD = sys.argv[1] if len(sys.argv) > 1 else 'thread/read'

SEND = """
(async () => {
  const skel = (v, d) => {
    d = d || 0;
    if (v === null) return 'null';
    if (Array.isArray(v)) return d > 4 ? `array(${v.length})`
      : [`array(${v.length})`, v.length ? skel(v[0], d+1) : null];
    const t = typeof v;
    if (t === 'string') return v.length > 40 ? `string(${v.length})` : v;   // 短字串直接顯示
    if (t === 'object') {
      if (d > 5) return 'object{…}';
      const o = {};
      for (const k of Object.keys(v).slice(0, 50)) o[k] = skel(v[k], d + 1);
      return o;
    }
    if (t === 'boolean') return `bool:${v}`;
    if (t === 'number') return 'number';
    return t;
  };
  // 拿一個現有 threadId：用 hook 快取的，沒有就用 thread/list 的第一個
  const h = window.__codexRetryHook;
  let tid = h && Object.keys(h.templates())[0];
  const got = [];
  const mk = (method, params) => {
    const id = crypto.randomUUID();
    const hh = (e) => {
      const d = e.data;
      if (!d || typeof d !== 'object') return;
      let s = ''; try { s = JSON.stringify(d); } catch (x) { return; }
      if (s.includes(id)) got.push({method, shape: skel(d.message ? d.message.result : d)});
    };
    window.addEventListener('message', hh, true);
    window.electronBridge.sendMessageFromView({
      type: 'mcp-request', hostId: 'local', priority: 'background',
      request: { id, method, params },
    }).catch(e => got.push({method, err: String(e).slice(0, 120)}));
    return () => window.removeEventListener('message', hh, true);
  };
  const offs = [];
  if (!tid) offs.push(mk('thread/list', {}));
  else offs.push(mk('%METHOD%', {threadId: tid}));
  await new Promise(r => setTimeout(r, 6000));
  offs.forEach(f => f());
  return JSON.stringify({threadId: tid ? tid.slice(0, 8) : null, got});
})()
""".replace('%METHOD%', METHOD)

async def main():
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as r:
        tg = [t for t in json.loads(r.read())
              if t.get('type') == 'page' and t.get('url', '').endswith('/index.html')]
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(tg[0]['webSocketDebuggerUrl'], max_msg_size=0) as ws:
            await ws.send_json({'id': 1, 'method': 'Runtime.evaluate',
                                'params': {'expression': SEND, 'awaitPromise': True,
                                           'returnByValue': True}})
            while True:
                m = json.loads(await ws.receive_str())
                if m.get('id') == 1:
                    break
    r = m.get('result') or {}
    if r.get('exceptionDetails'):
        print('ERR', json.dumps(r['exceptionDetails'])[:500]); return
    v = json.loads(r.get('result', {}).get('value'))
    print('threadId:', v['threadId'])
    for g in v['got']:
        print(f"--- {g['method']} ---")
        print(json.dumps(g.get('shape', g.get('err')), indent=1, ensure_ascii=False)[:2500])
asyncio.run(main())
