"""把改過的 hook 熱換進正在跑的 Codex，不用重開 app。"""
import asyncio, json, pathlib, sys, urllib.request
import aiohttp
PORT = 9333
src = pathlib.Path('codex-retry-hook.js').read_text(encoding='utf-8')

async def main():
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as r:
        tg = [t for t in json.loads(r.read())
              if t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
    async with aiohttp.ClientSession() as s:
        for t in tg:
            async with s.ws_connect(t['webSocketDebuggerUrl'], max_msg_size=0) as ws:
                n = [0]
                async def ev(expr):
                    n[0] += 1
                    await ws.send_json({'id': n[0], 'method': 'Runtime.evaluate',
                                        'params': {'expression': expr, 'returnByValue': True}})
                    while True:
                        m = json.loads(await ws.receive_str())
                        if m.get('id') == n[0]:
                            return m
                await ev("window.__codexRetryHook && window.__codexRetryHook.stop(); "
                         "delete window.__codexRetryHook; 'stopped'")
                await ws.send_json({'id': 900, 'method': 'Page.enable', 'params': {}})
                while True:
                    m = json.loads(await ws.receive_str())
                    if m.get('id') == 900:
                        break
                await ws.send_json({'id': 901, 'method': 'Page.addScriptToEvaluateOnNewDocument',
                                    'params': {'source': src}})
                while True:
                    m = json.loads(await ws.receive_str())
                    if m.get('id') == 901:
                        break
                await ev(src)
                r2 = await ev("(()=>{const h=window.__codexRetryHook;"
                              "return h? 'ok cfg='+Object.keys(h.cfg).length : 'MISSING';})()")
            print(f"  {t.get('url','?')[:60]:62s} {(r2.get('result') or {}).get('result',{}).get('value')}")
asyncio.run(main())
