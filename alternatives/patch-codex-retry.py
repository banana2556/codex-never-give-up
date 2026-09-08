#!/usr/bin/env python
"""讓 Codex 桌面版遇到 429 過載時無限自動重送，永遠不丟「重試」按鈕給你手點。

renderer bundle 裡的邏輯（RDr）：

    let r=0;
    for(let e=n.length-1; e>0 && r<LDr.length; --e){ ...; r+=1 }
    return LDr[r]??null

r = 已經連續過載幾次。LDr=[10,30,120,300] 是等待秒數表。
連續第 5 次時 LDr[4] 是 undefined -> 回 null -> 父層的
`errorInfo==='serverOverloaded' && o!=null` 不成立 -> 倒數不渲染
-> 掉回那顆要你手點的按鈕。**這才是它會停下來的原因，不是秒數。**

兩處同長度覆寫（asar 的 offset 不會跑掉）：
    LDr=[10,30,120,300]  ->  LDr=[1,1,2,2,3,3,4]
    return LDr[r]??null  ->  return LDr[r%7]||1;

改完等待秒數是 1,1,2,2,3,3,4 之後固定 1 秒，永遠是正數、永遠不回 null，
所以永遠不會掉回手點按鈕。語意用 node 對過（check-semantics.cjs）。

  python patch-codex-retry.py            看狀態 / dry-run
  python patch-codex-retry.py --apply    實際寫入（要管理員權限）
  python patch-codex-retry.py --revert   還原
  python patch-codex-retry.py --selftest 自我檢查

Codex 每次更新都會換掉 app.asar，更新後要再跑一次 --apply。
"""
import ctypes, json, mmap, os, pathlib, subprocess, sys

try:  # Windows 主控台預設不是 UTF-8，中文訊息會變亂碼
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# (原始, 改後)。兩邊長度必須一樣，否則 asar 檔頭的 offset 全錯。
# ponytail: 固定 1 秒無限重試。你的中轉真的長時間掛掉時這會一直敲它，
# 想溫和一點就把 [1,1,2,2,3,3,4] 換成 [2,2,3,3,5,5,8]（一樣 19 bytes）
PATCHES = [
    (b'LDr=[10,30,120,300]', b'LDr=[1,1,2,2,3,3,4]'),
    (b'return LDr[r]??null', b'return LDr[r%7]||1;'),
]
for a, b in PATCHES:
    assert len(a) == len(b), f'length mismatch: {a!r} vs {b!r}'

STATE = pathlib.Path(os.environ['LOCALAPPDATA']) / 'codex-retry-patch.json'

def find_asar():
    out = subprocess.run(
        ['powershell', '-NoProfile', '-Command',
         "(Get-AppxPackage -Name 'OpenAI.Codex').InstallLocation"],
        capture_output=True, text=True).stdout.strip()
    if not out:
        sys.exit('找不到 OpenAI.Codex 套件')
    p = pathlib.Path(out) / 'app' / 'resources' / 'app.asar'
    if not p.exists():
        sys.exit(f'找不到 {p}')
    return p

def find_all(mm, pat):
    offs, i = [], mm.find(pat)
    while i != -1:
        offs.append(i)
        i = mm.find(pat, i + 1)
    return offs

def survey(path, revert):
    """回傳 [(offset, 要寫入的 bytes, 說明)]，或 None 表示不用動 / 不敢動。"""
    todo, done, bad = [], [], []
    with open(path, 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            for old, new in PATCHES:
                src, dst = (old, new) if not revert else (new, old)
                here, there = find_all(mm, src), find_all(mm, dst)
                if len(here) == 1:
                    todo.append((here[0], dst, f'{src.decode()} -> {dst.decode()}'))
                elif not here and len(there) == 1:
                    done.append(src.decode())
                else:
                    bad.append(f'{src.decode()!r}: 找到 {len(here)} 處（預期 1）')
        finally:
            mm.close()
    return todo, done, bad

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def write_all(path, todo):
    # WindowsApps 是 TrustedInstaller 的，先取得所有權再放行寫入，改完立刻收回
    f = str(path)
    subprocess.run(['takeown', '/F', f], capture_output=True)
    subprocess.run(['icacls', f, '/grant', 'Administrators:F'], capture_output=True)
    try:
        with open(f, 'r+b') as fh:
            for off, data, _ in todo:
                fh.seek(off)
                fh.write(data)
    finally:
        subprocess.run(['icacls', f, '/setowner', r'NT SERVICE\TrustedInstaller'],
                       capture_output=True)
        subprocess.run(['icacls', f, '/remove:g', 'Administrators'], capture_output=True)

def main():
    apply_, revert = '--apply' in sys.argv, '--revert' in sys.argv
    asar = find_asar()
    print(f'app.asar : {asar}')
    print(f'大小     : {asar.stat().st_size} bytes')

    todo, done, bad = survey(asar, revert)
    for d in done:
        print(f'已完成   : {d}')
    for b in bad:
        print(f'異常     : {b}')
    if bad:
        sys.exit('\npattern 對不上 — Codex 版本可能變了，不敢動。')
    if not todo:
        print('\n已經是目標狀態，不用做事。')
        return

    for off, data, desc in todo:
        print(f'待寫入   : offset {off} ({off:#x})  {desc}')
    if not (apply_ or revert):
        print('\n(dry-run。要實際寫入請用管理員身分跑 --apply)')
        return
    if not is_admin():
        sys.exit('\n需要管理員權限：用管理員的 PowerShell 再跑一次。')

    size_before = asar.stat().st_size
    write_all(asar, todo)

    if asar.stat().st_size != size_before:
        sys.exit('檔案大小變了！asar 可能已損壞，請用 --revert 或重裝 Codex。')
    left, _, _ = survey(asar, revert)
    if left:
        sys.exit('寫入後驗證失敗！')
    STATE.write_text(json.dumps({'asar': str(asar),
                                 'state': 'reverted' if revert else 'patched',
                                 'offsets': [o for o, _, _ in todo]}), encoding='utf-8')
    print(f'\n完成（{len(todo)} 處）。重開 Codex app 生效。狀態記在 {STATE}')

def selftest():
    import tempfile
    blob = b'\x00' * 50 + PATCHES[0][0] + b'mid' * 20 + PATCHES[1][0] + b'\xff' * 50
    with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as t:
        t.write(blob)
        tmp = pathlib.Path(t.name)
    try:
        todo, done, bad = survey(tmp, False)
        assert not bad and not done and len(todo) == 2, (todo, done, bad)
        with open(tmp, 'r+b') as fh:
            for off, data, _ in todo:
                fh.seek(off); fh.write(data)
        assert tmp.stat().st_size == len(blob), '大小變了'

        # 改完之後：正向沒事可做，反向剛好兩處
        assert survey(tmp, False) == ([], [p[0].decode() for p in PATCHES], [])
        back, _, _ = survey(tmp, True)
        assert len(back) == 2, back

        # 還原後要 byte-for-byte 一致
        with open(tmp, 'r+b') as fh:
            for off, data, _ in back:
                fh.seek(off); fh.write(data)
        assert tmp.read_bytes() == blob, '還原後不一致'

        # 長度守門：任何長度不等的 pattern 都該在 import 時就炸
        for a, b in PATCHES:
            assert len(a) == len(b)
        print('ok')
    finally:
        tmp.unlink()

if __name__ == '__main__':
    selftest() if '--selftest' in sys.argv else main()
