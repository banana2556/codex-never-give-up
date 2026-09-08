"""renderer 到底怎麼跟 app-server 講話：看 CDP Network 事件 + 那個 client 物件。"""
import asyncio, json, collections, urllib.request
import aiohttp
PORT = 9333

CLIENT = r"""
(() => {
  const cur = window.__codexRoot?._internalRoot?.current;
  if (!cur) return {err: 'no root'};
  const q = [[cur, 0]];
  while (q.length) {
    const [f, d] = q.shift();
    if (!f || d > 16) continue;
    const p = f.memoizedProps;
    if (p && typeof p === 'object' && p.client && typeof p.client === 'object') {
      const c = p.client;
      const proto = Object.getPrototypeOf(c) || {};
      return {
        depth: d,
        ctor: c.constructor && c.constructor.name,
        ownKeys: Object.getOwnPropertyNames(c).slice(0, 25),
        protoMethods: Object.getOwnPropertyNames(proto)
          .filter(n => { try { return typeof c[n] === 'function'; } catch(e){ return false; } })
          .slice(0, 40),
      };
    }
    if (f.child) q.push([f.child, d + 1]);
    if (f.sibling && d > 0) q.push([f.sibling, d]);
  }
  return {err: 'client not found'};
})()
"""

async def main():
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as r:
        tg = [t for t in json.loads(r.read())
              if t.get('type') == 'page' and t.get('url', '').endswith('/index.html')]
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(tg[0]['webSocketDebuggerUrl'], max_msg_size=0) as ws:
            await ws.send_json({'id': 1, 'method': 'Runtime.evaluate',
                                'params': {'expression': CLIENT, 'returnByValue': True}})
            await ws.send_json({'id': 2, 'method': 'Network.enable', 'params': {}})
            kinds = collections.Counter()
            ws_urls, samples = set(), []
            got_client = None
            deadline = asyncio.get_event_loop().time() + 25
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.receive_str(), timeout=3)
                except asyncio.TimeoutError:
                    continue
                m = json.loads(raw)
                if m.get('id') == 1:
                    got_client = (m.get('result') or {}).get('result', {}).get('value')
                    continue
                meth = m.get('method')
                if not meth:
                    continue
                kinds[meth] += 1
                p = m.get('params') or {}
                if meth == 'Network.webSocketCreated':
                    ws_urls.add(p.get('url', '?'))
                if meth in ('Network.webSocketFrameReceived', 'Network.webSocketFrameSent'):
                    d = (p.get('response') or {}).get('payloadData', '')
                    if len(samples) < 6 and d:
                        samples.append(f'{meth[-13:]}: {d[:300]}')
                if meth == 'Network.requestWillBeSent' and len(samples) < 6:
                    u = (p.get('request') or {}).get('url', '')
                    if 'localhost' in u or '127.0.0.1' in u:
                        samples.append(f'HTTP {(p.get("request") or {}).get("method")} {u[:120]}')
            print('client 物件:', json.dumps(got_client, ensure_ascii=False)[:700])
            print('\nCDP Network 事件統計:')
            for k, v in kinds.most_common(12):
                print(f'   {k}: {v}')
            print(f'\nWebSocket 連線: {ws_urls or "(無)"}')
            print('取樣:')
            for x in samples:
                print('  ', x[:300])
            # 收乾淨
            await ws.send_json({'id': 99, 'method': 'Runtime.evaluate', 'params': {
                'expression': "window.__evTapOff&&window.__evTapOff();"
                              "delete window.__evTap;delete window.__evTapOff;'cleaned'",
                'returnByValue': True}})
            await asyncio.sleep(1)

asyncio.run(main())
