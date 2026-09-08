#!/usr/bin/env python
"""把 codex-retry-hook.js 注入 Codex 桌面版的 renderer。

用 Chromium 的 remote debugging 介面注入，不改 app.asar：
不用管理員、不用重寫 285MB、Codex 更新也不會被蓋掉。

  python codex-inject.py            注入（Codex 已開著就叫你先關）
  python codex-inject.py --force    自動關掉再重開並注入
  python codex-inject.py --watch    注入後持續盯新開的視窗
  python codex-inject.py --port N   換 debug port（預設 9333）

注入後在 Codex 的 DevTools console 可以查：
  __codexRetryHook.stats()   每條路徑按了幾次
  __codexRetryHook.log()     動作紀錄
  __codexRetryHook.stop()    停掉
"""
import asyncio, json, pathlib, subprocess, sys, time, urllib.error, urllib.request

import aiohttp

HOOK = pathlib.Path(__file__).with_name('codex-retry-hook.js')
PORT = 9333
for i, a in enumerate(sys.argv):
    if a == '--port' and i + 1 < len(sys.argv):
        PORT = int(sys.argv[i + 1])
FORCE = '--force' in sys.argv
WATCH = '--watch' in sys.argv

def ps(cmd):
    return subprocess.run(['powershell', '-NoProfile', '-Command', cmd],
                          capture_output=True, text=True).stdout.strip()

def app_exe():
    loc = ps("(Get-AppxPackage -Name 'OpenAI.Codex').InstallLocation")
    if not loc:
        sys.exit('找不到 OpenAI.Codex 套件')
    exe = pathlib.Path(loc) / 'app' / 'ChatGPT.exe'
    if not exe.exists():
        sys.exit(f'找不到 {exe}')
    return exe

def live_pids():
    out = ps("(Get-Process -Name ChatGPT -ErrorAction SilentlyContinue | "
             "Where-Object { $_.Path -like '*OpenAI.Codex*' }).Id -join ','")
    return [p for p in out.split(',') if p.strip()]

def http_json(path):
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}{path}', timeout=3) as r:
        return json.loads(r.read())

async def inject_into(session, target, src):
    async with session.ws_connect(target['webSocketDebuggerUrl'],
                                  max_msg_size=0, timeout=15) as ws:
        async def call(method, params=None, _id=[0]):
            _id[0] += 1
            await ws.send_json({'id': _id[0], 'method': method, 'params': params or {}})
            while True:
                msg = json.loads(await ws.receive_str())
                if msg.get('id') == _id[0]:
                    return msg
        await call('Page.enable')
        # 之後每次重新載入（換視窗、reload）都自動再裝一次
        await call('Page.addScriptToEvaluateOnNewDocument', {'source': src})
        # 目前這個已經載入的頁面也立刻裝上
        r = await call('Runtime.evaluate', {'expression': src, 'awaitPromise': False})
        err = (r.get('result') or {}).get('exceptionDetails')
        if err:
            return f"FAILED {target.get('url','?')}: {err.get('text')}"
        probe = await call('Runtime.evaluate',
                           {'expression': 'typeof window.__codexRetryHook', 'returnByValue': True})
        ok = (probe.get('result') or {}).get('result', {}).get('value')
        return f"{'OK  ' if ok == 'object' else 'HUH '} {target.get('url','?')}  (hook={ok})"

async def run():
    src = HOOK.read_text(encoding='utf-8')
    exe = app_exe()

    pids = live_pids()
    if pids:
        if not FORCE:
            sys.exit(f'Codex 正在跑（pid {",".join(pids)}）。Chromium 只在啟動時吃 '
                     f'--remote-debugging-port，要先關掉。\n'
                     f'確定可以關就加 --force 重跑。')
        print(f'關掉現有的 Codex（pid {",".join(pids)}）...')
        ps("Get-Process -Name ChatGPT -ErrorAction SilentlyContinue | "
           "Where-Object { $_.Path -like '*OpenAI.Codex*' } | Stop-Process -Force")
        for _ in range(30):
            if not live_pids():
                break
            time.sleep(0.5)

    print(f'啟動 {exe.name} --remote-debugging-port={PORT}')
    subprocess.Popen([str(exe), f'--remote-debugging-port={PORT}'],
                     creationflags=subprocess.DETACHED_PROCESS)

    deadline = time.time() + 90
    targets = []
    while time.time() < deadline:
        try:
            targets = [t for t in http_json('/json/list')
                       if t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
            if targets:
                break
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            pass
        time.sleep(1)
    if not targets:
        sys.exit(f'等不到 debug port {PORT} 上的頁面 — 這個版本可能不吃這個參數。')

    print(f'找到 {len(targets)} 個頁面')
    async with aiohttp.ClientSession() as session:
        for t in targets:
            try:
                print('  ' + await inject_into(session, t, src))
            except Exception as e:
                print(f'  ERR {t.get("url","?")}: {type(e).__name__}: {e}')
    print('\n注入完成。DevTools console 用 __codexRetryHook.stats() 看狀況。')

if __name__ == '__main__':
    if not HOOK.exists():
        sys.exit(f'找不到 {HOOK}')
    asyncio.run(run())
