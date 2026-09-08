#!/usr/bin/env python
"""從 debug port 檢查 hook 現況：裝上了嗎、fiber 讀得到 key 嗎、按過幾次。"""
import asyncio, json, sys, urllib.request
import aiohttp

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9333

DIAG = r"""
(() => {
  const out = {hook: typeof window.__codexRetryHook, url: location.href};
  if (out.hook === 'object') {
    out.stats = window.__codexRetryHook.stats();
    out.log = window.__codexRetryHook.log().slice(-15);
    out.cfgKeys = Object.keys(window.__codexRetryHook.cfg).length;
  }
  // 不套白名單，掃出所有按鈕能解析到的 i18n key —— 驗證 fiber 走法在這版 app 上有效
  function fiberOf(el){for(const k in el){if(k.startsWith('__reactFiber$')||k.startsWith('__reactInternalInstance$'))return el[k];}return null;}
  const RE=/^[a-z][A-Za-z0-9]*(\.[A-Za-z0-9]+)+$/;
  function anyId(btn){
    const f=fiberOf(btn); if(!f) return null;
    const q=[[f,0]];
    while(q.length){const [n,d]=q.shift(); if(!n||d>8) continue;
      const id=n.memoizedProps&&n.memoizedProps.id;
      if(typeof id==='string'&&RE.test(id)) return id;
      if(n.child)q.push([n.child,d+1]);
      if(n.sibling&&d>0)q.push([n.sibling,d]);}
    let up=f,d=0;
    while(up&&d++<12){const id=up.memoizedProps&&up.memoizedProps.id;
      if(typeof id==='string'&&RE.test(id))return id; up=up.return;}
    return null;
  }
  const btns=[...document.querySelectorAll('button:not([disabled]),[role=button]')].filter(b=>b.offsetParent);
  const keys={}; let withKey=0, noFiber=0;
  for(const b of btns){
    if(!fiberOf(b)) { noFiber++; continue; }
    const k=anyId(b); if(k){withKey++; keys[k]=(keys[k]||0)+1;}
  }
  out.buttons=btns.length; out.noFiber=noFiber; out.resolvedKeys=withKey;
  out.sampleKeys=Object.entries(keys).slice(0,12);
  out.retryKeys=Object.entries(keys).filter(([k])=>/retry|Retry|resume/i.test(k));
  return out;
})()
"""

async def main():
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as r:
        targets = [t for t in json.loads(r.read())
                   if t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
    if not targets:
        sys.exit(f'port {PORT} 上沒有頁面 — Codex 沒開，或不是用注入器啟動的')
    async with aiohttp.ClientSession() as s:
        for t in targets:
            async with s.ws_connect(t['webSocketDebuggerUrl'], max_msg_size=0) as ws:
                await ws.send_json({'id': 1, 'method': 'Runtime.evaluate',
                                    'params': {'expression': DIAG, 'returnByValue': True,
                                               'awaitPromise': False}})
                while True:
                    m = json.loads(await ws.receive_str())
                    if m.get('id') == 1:
                        break
            res = (m.get('result') or {}).get('result', {})
            if res.get('subtype') == 'error' or (m.get('result') or {}).get('exceptionDetails'):
                print(f"ERR {t.get('url')}: {res.get('description') or m}")
                continue
            v = res.get('value') or {}
            print(f"--- {v.get('url','?')}")
            print(f"  hook 安裝     : {v.get('hook')}   CFG 條目: {v.get('cfgKeys')}")
            print(f"  可見按鈕      : {v.get('buttons')}  無 fiber: {v.get('noFiber')}  "
                  f"解析出 key: {v.get('resolvedKeys')}")
            print(f"  key 取樣      : {v.get('sampleKeys')}")
            print(f"  retry 類 key  : {v.get('retryKeys')}")
            print(f"  已自動按過    : {v.get('stats')}")
            for line in (v.get('log') or []):
                print(f"    log> {line}")

asyncio.run(main())
