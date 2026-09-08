"""查清楚：背景（非當前開啟）的對話，其重試 UI 到底有沒有進到 DOM。"""
import asyncio, json, urllib.request
import aiohttp

PORT = 9333

E = r"""
(() => {
  function fiberOf(el){
    for(const k in el){
      if(k.startsWith('__reactFiber$')||k.startsWith('__reactInternalInstance$')) return el[k];
    }
    return null;
  }
  const RE=/^[a-z][A-Za-z0-9]*(\.[A-Za-z0-9]+)+$/;
  // react-intl 的 FormattedMessage 只回字串，帶 id 的 fiber 是子節點 -> 必須往下走
  function keyOf(el){
    const f=fiberOf(el); if(!f) return null;
    const q=[[f,0]];
    while(q.length){
      const [n,d]=q.shift();
      if(!n||d>4) continue;
      const id=n.memoizedProps&&n.memoizedProps.id;
      if(typeof id==='string'&&RE.test(id)) return id;
      if(n.child) q.push([n.child,d+1]);
      if(n.sibling&&d>0) q.push([n.sibling,d]);
    }
    return null;
  }
  const keys={};
  const all=document.querySelectorAll('*');
  for(const el of all){
    const k=keyOf(el);
    if(k) keys[k]=(keys[k]||0)+1;
  }
  const names=Object.keys(keys);

  // 側邊欄的執行緒/任務列
  const links=[...document.querySelectorAll('a[href]')]
    .map(a=>({href:a.getAttribute('href')||'', text:(a.innerText||'').trim().slice(0,26)}))
    .filter(o=>/thread|task|\/c\//i.test(o.href));

  return {
    totalEls: all.length,
    i18nKeyCount: names.length,
    taskRowKeys: names.filter(k=>k.startsWith('localTaskRow')).map(k=>[k,keys[k]]),
    convoKeys:   names.filter(k=>k.startsWith('localConversation')).map(k=>[k,keys[k]]),
    sidebarKeys: names.filter(k=>/sidebar|taskRow|inbox|threadList/i.test(k))
                      .slice(0,14).map(k=>[k,keys[k]]),
    threadLinkCount: links.length,
    threadLinks: links.slice(0,6),
  };
})()
"""


async def main():
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as r:
        tg = [t for t in json.loads(r.read())
              if t.get('type') == 'page' and t.get('url', '').endswith('/index.html')]
    if not tg:
        print('找不到主視窗')
        return
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(tg[0]['webSocketDebuggerUrl'], max_msg_size=0) as ws:
            await ws.send_json({'id': 1, 'method': 'Runtime.evaluate',
                                'params': {'expression': E, 'returnByValue': True}})
            while True:
                m = json.loads(await ws.receive_str())
                if m.get('id') == 1:
                    break
    r = m.get('result') or {}
    if r.get('exceptionDetails'):
        print('ERR', r['exceptionDetails'].get('text'))
        return
    v = r.get('result', {}).get('value') or {}
    print(f"DOM 元素總數      : {v.get('totalEls')}")
    print(f"解析出的 i18n key : {v.get('i18nKeyCount')}")
    print(f"localConversation : {v.get('convoKeys')}")
    print(f"localTaskRow      : {v.get('taskRowKeys')}")
    print(f"側邊欄相關 key    : {v.get('sidebarKeys')}")
    print(f"執行緒連結數      : {v.get('threadLinkCount')}")
    for o in v.get('threadLinks') or []:
        print(f"   {o['href'][:44]}  {o['text']}")


asyncio.run(main())
