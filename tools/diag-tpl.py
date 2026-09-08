import asyncio, json, urllib.request
import aiohttp
PORT = 9333
E = r"""
(() => {
  const h = window.__codexRetryHook;
  const rt = window.__rtCap;
  const out = {
    page: location.href.slice(-46),
    hook: !!h,
    hookTemplates: h ? Object.keys(h.templates()).map(k => k.slice(0,8)) : null,
    hookLog: h ? h.log().slice(-6) : null,
    standaloneArmed: !!rt,
    standaloneUserTurnsSeen: rt ? rt.userTurns : null,
    standaloneRetriesSeen: rt ? rt.retries.length : null,
    // 事件到底掛在哪：暫時掛一個計數器測 30 秒太久，這裡先看 listener 是否存在
    hasBridge: !!(window.electronBridge && window.electronBridge.sendMessageFromView),
  };
  return JSON.stringify(out);
})()
"""
async def main():
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as r:
        tg = [t for t in json.loads(r.read())
              if t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
    async with aiohttp.ClientSession() as s:
        for t in tg:
            try:
                async with s.ws_connect(t['webSocketDebuggerUrl'], max_msg_size=0) as ws:
                    await ws.send_json({'id': 1, 'method': 'Runtime.evaluate',
                                        'params': {'expression': E, 'returnByValue': True}})
                    while True:
                        m = json.loads(await ws.receive_str())
                        if m.get('id') == 1:
                            break
                v = (m.get('result') or {}).get('result', {}).get('value')
                d = json.loads(v)
                print(f"--- {d['page']}")
                print(f"   hook={d['hook']}  bridge={d['hasBridge']}  模板={d['hookTemplates']}")
                print(f"   獨立錄製器: armed={d['standaloneArmed']} "
                      f"看到使用者turn={d['standaloneUserTurnsSeen']} "
                      f"看到重試={d['standaloneRetriesSeen']}")
                for l in (d['hookLog'] or []):
                    print(f"     log> {l}")
            except Exception as e:
                print(f"--- {t.get('url','?')[-40:]} ERR {e}")
asyncio.run(main())
