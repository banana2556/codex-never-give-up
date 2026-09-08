"""只錄 mcp-request 的結構（不記內容）：信封欄位 + method 分佈 + turn/start 的 params 骨架。"""
import asyncio, json, urllib.request
import aiohttp
PORT = 9333
SETUP = r"""
(() => {
  if (window.__rq) return 'already';
  const R = window.__rq = {n:0, methods:{}, envelopeKeys:{}, skeleton:null, skelMethod:null};
  // 把值換成型別/長度，不留原文
  function skel(v, d) {
    d = d || 0;
    if (v === null) return 'null';
    if (Array.isArray(v)) return d > 3 ? `array(${v.length})`
      : [`array(${v.length})`, v.length ? skel(v[0], d+1) : null];
    const t = typeof v;
    if (t === 'string') return `string(${v.length})`;
    if (t === 'object') {
      if (d > 4) return 'object{…}';
      const o = {};
      for (const k of Object.keys(v).slice(0, 40)) o[k] = skel(v[k], d + 1);
      return o;
    }
    if (t === 'boolean') return `bool:${v}`;
    if (t === 'number') return 'number';
    return t;
  }
  R.h = (e) => {
    const d = e.detail;
    if (!d || typeof d !== 'object' || d.type !== 'mcp-request') return;
    R.n += 1;
    for (const k of Object.keys(d)) R.envelopeKeys[k] = (R.envelopeKeys[k] || 0) + 1;
    const meth = d.method || (d.payload && d.payload.method) ||
                 (d.request && d.request.method) || '(?)';
    R.methods[meth] = (R.methods[meth] || 0) + 1;
    if (!R.skeleton && /turn\/start|thread\/resume/.test(meth)) {
      R.skelMethod = meth;
      try { R.skeleton = JSON.stringify(skel(d)); } catch (x) { R.skeleton = 'err ' + x; }
    }
    if (!R.firstAny) { try { R.firstAny = JSON.stringify(skel(d)); } catch(x){} }
  };
  window.addEventListener('codex-message-from-view', R.h, true);
  document.addEventListener('codex-message-from-view', R.h, true);
  return 'ok';
})()
"""
READ = ("(()=>{const r=window.__rq||{};return {n:r.n,methods:r.methods,"
        "envelopeKeys:r.envelopeKeys,skelMethod:r.skelMethod,skeleton:r.skeleton,"
        "firstAny:r.firstAny};})()")
CLEAN = ("(()=>{const r=window.__rq;if(!r)return 'none';"
         "window.removeEventListener('codex-message-from-view',r.h,true);"
         "document.removeEventListener('codex-message-from-view',r.h,true);"
         "delete window.__rq;return 'cleaned';})()")

async def ev(ws, i, expr):
    await ws.send_json({'id': i, 'method': 'Runtime.evaluate',
                        'params': {'expression': expr, 'returnByValue': True}})
    while True:
        m = json.loads(await ws.receive_str())
        if m.get('id') == i:
            return (m.get('result') or {})

async def main():
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as r:
        tg = [t for t in json.loads(r.read())
              if t.get('type') == 'page' and t.get('url', '').endswith('/index.html')]
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(tg[0]['webSocketDebuggerUrl'], max_msg_size=0) as ws:
            print('setup:', (await ev(ws, 1, SETUP)).get('result', {}).get('value'), flush=True)
            await asyncio.sleep(170)
            v = (await ev(ws, 2, READ)).get('result', {}).get('value') or {}
            print(f'mcp-request 數: {v.get("n")}')
            print('信封欄位      :', v.get('envelopeKeys'))
            print('--- method 分佈 ---')
            for k, n in sorted((v.get('methods') or {}).items(), key=lambda x: -x[1]):
                print(f'   {n:4d}  {k}')
            print(f'\n--- 任一則的骨架 ---\n{v.get("firstAny")}')
            print(f'\n--- {v.get("skelMethod")} 的骨架 ---')
            sk = v.get('skeleton')
            if sk:
                try: print(json.dumps(json.loads(sk), indent=1, ensure_ascii=False)[:2600])
                except Exception: print(sk[:2600])
            else:
                print('這段時間沒有 turn/start（沒觸發重試也沒送新訊息）')
            print('clean:', (await ev(ws, 3, CLEAN)).get('result', {}).get('value'))
asyncio.run(main())
