#!/usr/bin/env pythonw
"""Codex 自動重試 — 一鍵注入的 GUI。點一下就好，沒有黑框。

  pythonw codex_retry_gui.pyw              直接跑
  python  codex_retry_gui.pyw --selftest   自我檢查（不開視窗）
"""
import asyncio, json, os, queue, subprocess, sys, threading, time
import urllib.request

PORT = 9333
POLL_MS = 400
NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW，不讓 powershell 閃黑框

# CODEX 路徑狀態
P_SEARCH, P_FOUND, P_MISSING, P_INJECTED = '搜尋中', '已找到', '未安裝', '已注入'
# 只有這兩個狀態代表路徑確定存在，才允許注入
P_OK = (P_FOUND, P_INJECTED)


def res_dir():
    # PyInstaller 打包後資源在 _MEIPASS，沒打包就是腳本自己的目錄
    return getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))


def hook_src():
    p = os.path.join(res_dir(), 'codex-retry-hook.js')
    if not os.path.exists(p):
        raise FileNotFoundError('找不到 codex-retry-hook.js')
    with open(p, encoding='utf-8') as f:
        return f.read()


def ps(cmd):
    return subprocess.run(['powershell', '-NoProfile', '-Command', cmd],
                          capture_output=True, text=True,
                          creationflags=NO_WINDOW).stdout.strip()


def find_codex():
    """回傳 ChatGPT.exe 的完整路徑，找不到回 None。"""
    loc = ps("(Get-AppxPackage -Name 'OpenAI.Codex').InstallLocation")
    if loc:
        p = os.path.join(loc, 'app', 'ChatGPT.exe')
        if os.path.exists(p):
            return p
    # MSIX 查詢失敗時的退路：直接掃 WindowsApps
    guess = ps(r"(Get-ChildItem 'C:\Program Files\WindowsApps' -Filter 'OpenAI.Codex*' "
               r"-Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | "
               r"Select-Object -First 1).FullName")
    if guess:
        p = os.path.join(guess, 'app', 'ChatGPT.exe')
        if os.path.exists(p):
            return p
    return None


def live_pids():
    out = ps("(Get-Process -Name ChatGPT -ErrorAction SilentlyContinue | "
             "Where-Object { $_.Path -like '*OpenAI.Codex*' }).Id -join ','")
    return [x for x in out.split(',') if x.strip()]


def page_targets():
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=3) as r:
            return [t for t in json.loads(r.read())
                    if t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
    except Exception:
        return []


STATS_EXPR = ("(()=>{const h=window.__codexRetryHook;"
              "return h?JSON.stringify({stats:h.stats(),log:h.log().slice(-6)}):null;})()")


async def cdp_eval(session, target, expr):
    async with session.ws_connect(target['webSocketDebuggerUrl'],
                                  max_msg_size=0, timeout=15) as ws:
        await ws.send_json({'id': 1, 'method': 'Runtime.evaluate',
                            'params': {'expression': expr, 'returnByValue': True}})
        while True:
            m = json.loads(await ws.receive_str())
            if m.get('id') == 1:
                break
        return (m.get('result') or {}).get('result', {}).get('value')


async def cdp_install(session, target, src):
    async with session.ws_connect(target['webSocketDebuggerUrl'],
                                  max_msg_size=0, timeout=15) as ws:
        n = [0]

        async def call(method, params=None):
            n[0] += 1
            await ws.send_json({'id': n[0], 'method': method, 'params': params or {}})
            while True:
                m = json.loads(await ws.receive_str())
                if m.get('id') == n[0]:
                    return m

        await call('Runtime.evaluate', {'expression':
                   'window.__codexRetryHook && window.__codexRetryHook.stop();'
                   'delete window.__codexRetryHook; 0'})
        await call('Page.enable')
        # reload / 換頁之後自動再裝一次
        await call('Page.addScriptToEvaluateOnNewDocument', {'source': src})
        r = await call('Runtime.evaluate', {'expression': src})
        if (r.get('result') or {}).get('exceptionDetails'):
            raise RuntimeError((r['result']['exceptionDetails'] or {}).get('text', '?'))
        ok = await call('Runtime.evaluate', {'expression': 'typeof window.__codexRetryHook',
                                             'returnByValue': True})
        return (ok.get('result') or {}).get('result', {}).get('value') == 'object'


class Worker(threading.Thread):
    """所有網路 / 子行程都在這裡跑，GUI 只讀 out 佇列。"""

    def __init__(self):
        super().__init__(daemon=True)
        self.out = queue.Queue()
        self.cmd = queue.Queue()
        self.injected = set()
        self.exe = None
        self.path_state = P_SEARCH
        # 累計點擊要單調遞增：hook 的 stats 會因為閒置歸零，不能拿來加總。
        # 改成數 log 裡不重複的 click 行。
        self.seen_clicks = set()

    def say(self, kind, **kw):
        self.out.put(dict(kind=kind, **kw))

    def set_path(self, state, path=None):
        if state != self.path_state:
            self.path_state = state
            self.say('path', state=state, path=path or self.exe or '')

    def run(self):
        asyncio.run(self.loop())

    async def loop(self):
        import aiohttp
        self.say('path', state=P_SEARCH, path='')
        self.exe = await asyncio.get_running_loop().run_in_executor(None, find_codex)
        if self.exe:
            self.path_state = P_SEARCH          # 讓 set_path 一定會發出通知
            self.set_path(P_FOUND, self.exe)
            self.say('log', line=f'Codex: {self.exe}')
        else:
            self.path_state = P_SEARCH
            self.set_path(P_MISSING)
            self.say('log', line='找不到 OpenAI.Codex 套件，無法注入')
        async with aiohttp.ClientSession() as s:
            while True:
                try:
                    c = self.cmd.get_nowait()
                except queue.Empty:
                    c = None
                if c == 'quit':
                    return
                if c == 'inject':
                    if self.exe:
                        await self.do_inject(s)
                    else:
                        self.say('log', line='沒有 Codex 路徑，注入已略過')
                await self.do_poll(s)
                if not self.exe:                # 之後才安裝的話也能接上
                    self.exe = await asyncio.get_running_loop().run_in_executor(
                        None, find_codex)
                    if self.exe:
                        self.set_path(P_FOUND, self.exe)
                        self.say('log', line=f'Codex: {self.exe}')
                await asyncio.sleep(2)

    async def do_poll(self, s):
        tg = page_targets()
        if not tg:
            self.say('state', state='off',
                     msg='Codex 開著但沒注入' if live_pids() else '未注入')
            if self.exe:
                self.set_path(P_FOUND, self.exe)
            return
        lines, live = [], 0
        for t in tg:
            try:
                v = await cdp_eval(s, t, STATS_EXPR)
            except Exception:
                continue
            if not v:
                continue
            live += 1
            d = json.loads(v)
            for line in (d.get('log') or []):
                if ' click #' in line and line not in self.seen_clicks:
                    self.seen_clicks.add(line)
            lines = d.get('log') or lines
        if not live:
            self.say('state', state='off', msg='Codex 開著但沒注入')
            if self.exe:
                self.set_path(P_FOUND, self.exe)
            return
        self.set_path(P_INJECTED, self.exe)
        self.say('state', state='on', msg=f'運作中（{live} 個視窗）',
                 clicks=len(self.seen_clicks), log=lines)
        # 新開的視窗補上 hook
        if self.injected:
            src = hook_src()
            for t in [t for t in tg if t['id'] not in self.injected]:
                try:
                    if await cdp_install(s, t, src):
                        self.injected.add(t['id'])
                        self.say('log', line='新視窗已補上 hook')
                except Exception:
                    pass

    async def do_inject(self, s):
        try:
            src = hook_src()
        except Exception as e:
            return self.say('state', state='error', msg=str(e))

        if not page_targets():
            pids = live_pids()
            if pids:
                self.say('state', state='busy', msg=f'關閉 Codex（{len(pids)} 個處理程序）…')
                ps('Get-Process -Name ChatGPT -ErrorAction SilentlyContinue | '
                   "Where-Object { $_.Path -like '*OpenAI.Codex*' } | Stop-Process -Force")
                for _ in range(40):
                    if not live_pids():
                        break
                    await asyncio.sleep(0.5)
            self.say('state', state='busy', msg='啟動 Codex…')
            subprocess.Popen([self.exe, f'--remote-debugging-port={PORT}'],
                             creationflags=subprocess.DETACHED_PROCESS | NO_WINDOW)
            deadline = time.time() + 90
            while time.time() < deadline and not page_targets():
                await asyncio.sleep(1)
            if not page_targets():
                return self.say('state', state='error', msg=f'等不到 debug port {PORT}')

        self.say('state', state='busy', msg='注入中…')
        ok = 0
        for t in page_targets():
            try:
                if await cdp_install(s, t, src):
                    ok += 1
                    self.injected.add(t['id'])
            except Exception as e:
                self.say('log', line=f'{str(t.get("url", "?"))[:38]}: {e}')
        self.say('log', line=f'注入 {ok} 個視窗成功' if ok else '注入失敗')
        if not ok:
            self.say('state', state='error', msg='注入失敗')


COLORS = {'off': '#9aa0a6', 'busy': '#e8a33d', 'on': '#2ecc71', 'error': '#e74c3c'}
PATH_COLORS = {P_SEARCH: '#e8a33d', P_FOUND: '#5b9bf8',
               P_MISSING: '#e74c3c', P_INJECTED: '#2ecc71'}
FONT = 'Microsoft JhengHei UI'


def build(root, worker):
    import tkinter as tk
    root.title('Codex 自動重試')
    root.configure(bg='#16181d')
    root.geometry('420x340')
    root.resizable(False, False)

    tk.Label(root, text='Codex 自動重試', bg='#16181d', fg='#e8eaed',
             font=(FONT, 15, 'bold')).pack(pady=(18, 8))

    pathlbl = tk.Label(root, text=f'[CODEX路徑:{P_SEARCH}]', bg='#16181d',
                       fg=PATH_COLORS[P_SEARCH], font=('Consolas', 10, 'bold'))
    pathlbl.pack()

    row = tk.Frame(root, bg='#16181d')
    row.pack(pady=(14, 4))
    dot = tk.Canvas(row, width=14, height=14, bg='#16181d', highlightthickness=0)
    circle = dot.create_oval(2, 2, 12, 12, fill=COLORS['off'], outline='')
    dot.pack(side='left', padx=(0, 7))
    status = tk.Label(row, text='未注入', bg='#16181d', fg='#e8eaed',
                      font=(FONT, 11, 'bold'))
    status.pack(side='left')

    clicks = tk.Label(root, text='自動點擊 0 次', bg='#16181d', fg='#8a9099', font=(FONT, 10))
    clicks.pack()

    btn = tk.Button(root, text='一鍵注入', bg='#3b82f6', fg='white',
                    activebackground='#2f6fd0', activeforeground='white',
                    disabledforeground='#5a5f66',
                    relief='flat', cursor='hand2', bd=0, font=(FONT, 12, 'bold'),
                    state='disabled',                 # 找到路徑之前不准按
                    command=lambda: worker.cmd.put('inject'))
    btn.pack(pady=12, ipadx=28, ipady=8)

    logbox = tk.Text(root, height=6, bg='#0f1114', fg='#7d848d', relief='flat',
                     font=('Consolas', 8), wrap='word', state='disabled')
    logbox.pack(fill='both', expand=True, padx=14, pady=(0, 12))

    def put(line):
        logbox.configure(state='normal')
        logbox.insert('end', str(line) + '\n')
        logbox.see('end')
        logbox.configure(state='disabled')

    ui = {'path_state': P_SEARCH, 'run_state': 'off'}

    def refresh_button():
        allowed = ui['path_state'] in P_OK and ui['run_state'] != 'busy'
        btn.configure(state='normal' if allowed else 'disabled',
                      bg='#3b82f6' if allowed else '#232730',
                      text='重新注入' if ui['run_state'] == 'on' else '一鍵注入')

    def pump():
        try:
            while True:
                m = worker.out.get_nowait()
                if m['kind'] == 'path':
                    ui['path_state'] = m['state']
                    pathlbl.configure(text=f'[CODEX路徑:{m["state"]}]',
                                      fg=PATH_COLORS.get(m['state'], '#9aa0a6'))
                    refresh_button()
                elif m['kind'] == 'state':
                    ui['run_state'] = m['state']
                    col = COLORS.get(m['state'], COLORS['off'])
                    dot.itemconfig(circle, fill=col)
                    status.configure(text=m.get('msg', ''),
                                     fg='#e8eaed' if m['state'] == 'on' else col)
                    refresh_button()
                    if 'clicks' in m:
                        clicks.configure(text=f'自動點擊 {m["clicks"]} 次')
                    for line in m.get('log', []):
                        if line not in pump.seen:
                            pump.seen.add(line)
                            put(line)
                elif m['kind'] == 'log':
                    put(m['line'])
        except queue.Empty:
            pass
        root.after(POLL_MS, pump)

    pump.seen = set()
    root.after(POLL_MS, pump)
    ui.update({'status': status, 'btn': btn, 'clicks': clicks,
               'path': pathlbl, 'pump': pump, 'put': put})
    return ui


def selftest():
    import tkinter as tk
    assert os.path.exists(os.path.join(res_dir(), 'codex-retry-hook.js')), '少了 hook js'
    assert len(hook_src()) > 500, 'hook js 太短，可能是空檔'
    w = Worker()
    try:
        root = tk.Tk()
        root.withdraw()
    except Exception as e:
        print(f'跳過視窗測試（沒有顯示裝置）: {e}')
        return
    ui = build(root, w)

    def feed(*msgs):
        for m in msgs:
            w.out.put(m)
        ui['pump']()
        root.update()

    # 開場：路徑搜尋中，按鈕必須是停用的
    assert ui['path'].cget('text') == f'[CODEX路徑:{P_SEARCH}]', ui['path'].cget('text')
    assert str(ui['btn'].cget('state')) == 'disabled', '搜尋中就不該能按'

    # 未安裝 -> 一律不准注入，即使執行狀態看起來正常
    feed({'kind': 'path', 'state': P_MISSING},
         {'kind': 'state', 'state': 'off', 'msg': '未注入'})
    assert ui['path'].cget('text') == f'[CODEX路徑:{P_MISSING}]'
    assert str(ui['btn'].cget('state')) == 'disabled', '未安裝就不該能按'

    # 已找到 -> 解鎖
    feed({'kind': 'path', 'state': P_FOUND})
    assert str(ui['btn'].cget('state')) == 'normal', '找到路徑就該解鎖'
    assert ui['btn'].cget('text') == '一鍵注入'

    # 處理中 -> 暫時鎖住
    feed({'kind': 'state', 'state': 'busy', 'msg': '注入中…'})
    assert str(ui['btn'].cget('state')) == 'disabled', '處理中不該能按'

    # 已注入 -> 綠燈、計數、按鈕變重新注入
    feed({'kind': 'path', 'state': P_INJECTED},
         {'kind': 'state', 'state': 'on', 'msg': '運作中（2 個視窗）', 'clicks': 7})
    assert ui['path'].cget('text') == f'[CODEX路徑:{P_INJECTED}]'
    assert ui['status'].cget('text') == '運作中（2 個視窗）'
    assert '7' in ui['clicks'].cget('text')
    assert ui['btn'].cget('text') == '重新注入'
    assert str(ui['btn'].cget('state')) == 'normal'

    # 副標題已移除
    kids = [c for c in root.winfo_children() if isinstance(c, tk.Label)]
    texts = [c.cget('text') for c in kids]
    assert not any('不用你等' in t for t in texts), f'副標題還在: {texts}'

    root.destroy()
    print('ok  (路徑四態 / 未找到不准注入 / busy 鎖定 / 計數 / 副標題已移除)')


def main():
    import tkinter as tk
    w = Worker()
    w.start()
    root = tk.Tk()
    build(root, w)
    root.mainloop()


if __name__ == '__main__':
    selftest() if '--selftest' in sys.argv else main()
