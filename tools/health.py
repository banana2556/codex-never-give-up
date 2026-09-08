import asyncio, json, sys, time, urllib.request
import aiohttp
PORT = 9333
E = "(()=>{const h=window.__codexRetryHook;return h?JSON.stringify(h.health()):null;})()"
async def snap(s, tg):
    out = {}
    for t in tg:
        try:
            async with s.ws_connect(t['webSocketDebuggerUrl'], max_msg_size=0) as ws:
                await ws.send_json({'id':1,'method':'Runtime.evaluate',
                                    'params':{'expression':E,'returnByValue':True}})
                while True:
                    m = json.loads(await ws.receive_str())
                    if m.get('id')==1: break
            v = (m.get('result') or {}).get('result',{}).get('value')
            if v: out[t['url'][-38:]] = json.loads(v)
        except Exception as e:
            out[t['url'][-38:]] = f'ERR {e}'
    return out
async def main():
    wait = int(sys.argv[1]) if len(sys.argv)>1 else 20
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as r:
        tg=[t for t in json.loads(r.read()) if t.get('type')=='page' and t.get('webSocketDebuggerUrl')]
    async with aiohttp.ClientSession() as s:
        a = await snap(s, tg)
        print(f'--- t=0'); [print(f'  {k}: {v}') for k,v in a.items()]
        await asyncio.sleep(wait)
        b = await snap(s, tg)
        print(f'--- t={wait}s'); [print(f'  {k}: {v}') for k,v in b.items()]
        print('--- sweeps 增量 ---')
        for k in b:
            if isinstance(a.get(k),dict) and isinstance(b[k],dict):
                d=b[k]['sweeps']-a[k]['sweeps']
                print(f"  {k}: +{d} sweeps / {wait}s   visibility={b[k]['visibility']}"
                      f"  ({'節流/停擺' if d < wait/2 else '正常'})")
asyncio.run(main())
