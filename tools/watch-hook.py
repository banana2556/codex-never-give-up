"""盯 hook 的動作紀錄，只在真的對 retry 類路徑動作時吐一行。"""
import json, sys, time, urllib.request, urllib.error
PORT = 9333
EXPR = ("(()=>{const h=window.__codexRetryHook;"
        "return h?JSON.stringify({log:h.log(),stats:h.stats()}):'NOHOOK';})()")
INTERESTING = ('localConversation', 'localTaskRow', 'click failed', 'stopped')
seen, missing = set(), 0

# CDP 只能走 websocket，這裡用 aiohttp 同步包一層
import asyncio, aiohttp
async def once():
    global missing
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as r:
            tg = [t for t in json.loads(r.read())
                  if t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
    except Exception:
        missing += 1
        if missing == 3:
            print('WARN debug port 連不上 — Codex 關了，hook 沒在跑', flush=True)
        return
    missing = 0
    async with aiohttp.ClientSession() as s:
        for t in tg:
            try:
                async with s.ws_connect(t['webSocketDebuggerUrl'], max_msg_size=0,
                                        timeout=10) as ws:
                    await ws.send_json({'id': 1, 'method': 'Runtime.evaluate',
                                        'params': {'expression': EXPR, 'returnByValue': True}})
                    while True:
                        m = json.loads(await ws.receive_str())
                        if m.get('id') == 1:
                            break
            except Exception:
                continue
            v = (m.get('result') or {}).get('result', {}).get('value')
            if not v or v == 'NOHOOK':
                continue
            d = json.loads(v)
            for line in d.get('log', []):
                if line in seen:
                    continue
                seen.add(line)
                if any(k in line for k in INTERESTING):
                    print(f'HOOK {line}   stats={json.dumps(d.get("stats"), ensure_ascii=False)}',
                          flush=True)

async def main():
    while True:
        await once()
        await asyncio.sleep(15)

asyncio.run(main())
