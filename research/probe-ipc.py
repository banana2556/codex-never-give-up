"""app-server 事件從哪條路進 renderer：window message / MessagePort / 其他。"""
import asyncio, json, urllib.request
import aiohttp
PORT = 9333

SETUP = r"""
(() => {
  if (window.__ipcTap) return 'already';
  const T = window.__ipcTap = {win: 0, port: 0, portSend: 0, samples: [], portObjs: 0};

  // (1) window 的 message 事件
  window.addEventListener('message', (e) => {
    T.win += 1;
    if (T.samples.length < 10) {
      let s;
      try { s = typeof e.data === 'string' ? e.data.slice(0,240)
                                           : JSON.stringify(e.data).slice(0,240); }
      catch (err) { s = 'unserialisable ' + Object.prototype.toString.call(e.data); }
      T.samples.push('WIN  ' + s);
    }
  }, true);

  // (2) MessagePort：prototype 不是 frozen，可以包
  const mpProto = MessagePort.prototype;
  const origPost = mpProto.postMessage;
  mpProto.postMessage = function (...a) {
    T.portSend += 1;
    if (T.samples.length < 10) {
      let s; try { s = JSON.stringify(a[0]).slice(0,240); } catch(e){ s='?'; }
      T.samples.push('PORT-SEND ' + s);
    }
    return origPost.apply(this, a);
  };
  const desc = Object.getOwnPropertyDescriptor(mpProto, 'onmessage');
  T.canHookOnmessage = !!(desc && desc.set);
  if (desc && desc.set) {
    Object.defineProperty(mpProto, 'onmessage', {
      configurable: true, enumerable: desc.enumerable,
      get: desc.get,
      set(fn) {
        T.portObjs += 1;
        desc.set.call(this, function (ev) {
          T.port += 1;
          if (T.samples.length < 10) {
            let s; try { s = typeof ev.data==='string'? ev.data.slice(0,240)
                                                      : JSON.stringify(ev.data).slice(0,240); }
            catch(e){ s='unserialisable'; }
            T.samples.push('PORT-RECV ' + s);
          }
          return fn.apply(this, arguments);
        });
      },
    });
  }
  T.origPost = origPost; T.origDesc = desc;
  return 'tapped';
})()
"""
READ = "(()=>{const t=window.__ipcTap||{};return {win:t.win,port:t.port,portSend:t.portSend,portObjs:t.portObjs,canHookOnmessage:t.canHookOnmessage,samples:t.samples};})()"
CLEAN = r"""
(() => {
  const T = window.__ipcTap;
  if (!T) return 'none';
  try { MessagePort.prototype.postMessage = T.origPost; } catch(e){}
  try { if (T.origDesc) Object.defineProperty(MessagePort.prototype,'onmessage',T.origDesc); } catch(e){}
  delete window.__ipcTap;
  return 'cleaned';
})()
"""

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
            r = await ev(ws, 1, SETUP)
            print('setup:', r.get('result', {}).get('value'),
                  r.get('exceptionDetails', {}).get('text', '') if r.get('exceptionDetails') else '')
            await asyncio.sleep(25)
            v = (await ev(ws, 2, READ)).get('result', {}).get('value') or {}
            print(f"window message : {v.get('win')}")
            print(f"port recv      : {v.get('port')}   (掛到 {v.get('portObjs')} 個 port)")
            print(f"port send      : {v.get('portSend')}")
            print(f"onmessage 可掛  : {v.get('canHookOnmessage')}")
            print('取樣:')
            for x in (v.get('samples') or []):
                print('  ', x[:260])
            print('clean:', (await ev(ws, 3, CLEAN)).get('result', {}).get('value'))

asyncio.run(main())
