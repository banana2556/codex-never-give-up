"""抓一個真實過載通知，看 app-server 到底送什麼、帶不帶 threadId。"""
import asyncio, json, urllib.request
import aiohttp
PORT = 9333
SETUP = r"""
(() => {
  if (window.__ov) return 'already';
  const O = window.__ov = {hits: [], turnCompleted: [], allMethods: {}, n: 0};
  O.h = (e) => {
    const d = e.data;
    if (!d || typeof d !== 'object') return;
    O.n += 1;
    if (d.method) O.allMethods[d.method] = (O.allMethods[d.method] || 0) + 1;
    let s = '';
    try { s = JSON.stringify(d); } catch (x) { return; }
    if (/overload|rate_?limit|retry|429|too many|unavailable|failed|"error"/i.test(s)
        && O.hits.length < 6) {
      O.hits.push(s.slice(0, 900));
    }
    if (d.method === 'turn/completed' && O.turnCompleted.length < 4) {
      O.turnCompleted.push(s.slice(0, 700));
    }
  };
  window.addEventListener('message', O.h, true);
  return 'ok';
})()
"""
READ = "(()=>{const o=window.__ov||{};return {n:o.n,hits:o.hits,turnCompleted:o.turnCompleted,allMethods:o.allMethods};})()"
CLEAN = "(()=>{const o=window.__ov;if(!o)return 'none';window.removeEventListener('message',o.h,true);delete window.__ov;return 'cleaned';})()"

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
            await asyncio.sleep(150)
            v = (await ev(ws, 2, READ)).get('result', {}).get('value') or {}
            print(f'總訊息數: {v.get("n")}')
            print('--- method 分佈 ---')
            for k, n in sorted((v.get('allMethods') or {}).items(), key=lambda x: -x[1])[:20]:
                print(f'   {n:5d}  {k}')
            print('--- 命中錯誤/過載/重試 ---')
            for x in (v.get('hits') or []): print('  ', x[:880], '\n')
            print('--- turn/completed ---')
            for x in (v.get('turnCompleted') or []): print('  ', x[:680], '\n')
            print('clean:', (await ev(ws, 3, CLEAN)).get('result', {}).get('value'))
asyncio.run(main())
