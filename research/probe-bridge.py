"""看 window.electronBridge 的形狀，以及能不能被掛勾。"""
import asyncio, json, sys, urllib.request
import aiohttp
PORT = 9333
E = r"""
(() => {
  const out = {};
  const d = Object.getOwnPropertyDescriptor(window, 'electronBridge');
  out.descriptor = d ? {writable: !!d.writable, configurable: !!d.configurable,
                        enumerable: !!d.enumerable, hasGet: !!d.get} : null;
  const b = window.electronBridge;
  out.type = typeof b;
  if (b && typeof b === 'object') {
    out.frozen = Object.isFrozen(b);
    out.sealed = Object.isSealed(b);
    const names = Object.getOwnPropertyNames(b);
    out.memberCount = names.length;
    out.members = names.map(n => {
      let t = 'err';
      try { t = typeof b[n]; } catch (e) {}
      const dd = Object.getOwnPropertyDescriptor(b, n);
      return [n, t, dd ? `w=${!!dd.writable} c=${!!dd.configurable}` : '?'];
    });
    // 試著改一個成員（不真的破壞，改完立刻還原）
    const first = names.find(n => { try { return typeof b[n] === 'function'; } catch(e){ return false; } });
    if (first) {
      const orig = b[first];
      let can = false, err = null;
      try { b[first] = function(){ return orig.apply(this, arguments); }; can = (b[first] !== orig); }
      catch (e) { err = String(e).slice(0, 90); }
      try { b[first] = orig; } catch (e) {}
      out.canPatchMember = {name: first, patched: can, error: err, restored: b[first] === orig};
    }
  }
  out.windowType = (() => { try { return window.codexWindowType; } catch(e){ return 'err'; } })();
  out.otherGlobals = Object.getOwnPropertyNames(window)
    .filter(k => /codex|electron|oai|openai|app/i.test(k)).slice(0, 25);
  return out;
})()
"""
async def main():
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5) as r:
        tg=[t for t in json.loads(r.read())
            if t.get('type')=='page' and t.get('url','').endswith('/index.html')]
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(tg[0]['webSocketDebuggerUrl'], max_msg_size=0) as ws:
            await ws.send_json({'id':1,'method':'Runtime.evaluate',
                                'params':{'expression':E,'returnByValue':True}})
            while True:
                m=json.loads(await ws.receive_str())
                if m.get('id')==1: break
    r=(m.get('result') or {})
    if r.get('exceptionDetails'): print('ERR', r['exceptionDetails'].get('text')); return
    v=r.get('result',{}).get('value') or {}
    print('descriptor      :', v.get('descriptor'))
    print('type            :', v.get('type'), ' frozen=', v.get('frozen'), ' sealed=', v.get('sealed'))
    print('windowType      :', v.get('windowType'))
    print('canPatchMember  :', v.get('canPatchMember'))
    print('otherGlobals    :', v.get('otherGlobals'))
    print(f'members ({v.get("memberCount")}):')
    for row in (v.get('members') or []):
        print('   ', row)
asyncio.run(main())
