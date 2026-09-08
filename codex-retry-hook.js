/* Codex 桌面版 — 對話內重試按鈕的單一入口。
 *
 * 凡是會在對話裡長出重試按鈕的狀態，一律換成「按鈕已被按下」的行為。
 * 上限與間隔全部由下面的 CFG 決定，不再吃 app 內建的 4 次 / 5 次 / 10 次。
 *
 * 分類靠 React fiber 上的 i18n key，不靠按鈕文字 —
 * serverOverloaded / writerConflict / sharedTaskUnavailable 三者的按鈕
 * 文字都是「重試」，靠文字無法分辨。
 */
(function () {
  'use strict';
  if (typeof window !== 'undefined' && window.__codexRetryHook) return;   // 已安裝過

  // max: Infinity = 不設上限。gapMs = 同一條路徑兩次自動按之間至少隔多久。
  const CFG = {
    // 429 / 伺服器過載 —— 就是你要的那條，無限重送
    'localConversation.serverOverloaded.retry':          { max: Infinity, gapMs: 1000 },
    'localConversation.serverOverloaded.retryCountdown': { max: Infinity, gapMs: 1000 },
    // 其他確實是暫時性的，給有限次數
    'localConversation.writerConflict.retry':            { max: 30, gapMs: 2000 },
    'localConversation.sharedTaskUnavailable.retry':     { max: 10, gapMs: 3000 },
    'localConversation.threadHandoff.error.retry':       { max: 10, gapMs: 3000 },
    'localConversation.retryHistoryLoad':                { max: 10, gapMs: 2000 },
    'localTaskRow.resumeError.retry':                    { max: 30, gapMs: 2000 },
    'localTaskRow.resumeLiveWriterError':                { max: 30, gapMs: 2000 },
    // 背景重送（直接送 RPC，不經 DOM）的上限，key 是 'ipc:' + codexErrorInfo
    'ipc:serverOverloaded':          { max: Infinity, gapMs: 1000 },
    'ipc:internalServerError':       { max: Infinity, gapMs: 2000 },
    'ipc:httpConnectionFailed':      { max: Infinity, gapMs: 1000 },
    'ipc:responseStreamDisconnected':{ max: Infinity, gapMs: 1000 },

    // 故意不自動按，理由寫在這裡免得以後有人手癢加回去：
    //   localConversation.usageLimit.retry            配額用完，重按只是空敲，等重置才有用
    //   localConversation.turnRenderError.retry       UI 渲染失敗，重按會無限空轉
    //   localConversation.summaryPanelRenderError.retry  同上
    //   localTaskRow.resumeConfigError                config.toml 有錯，重按會無限空轉
  };

  // IPC 層的錯誤分類：app-server 的 error 通知直接給 codexErrorInfo，
  // 比按鈕的 i18n key 精確（按鈕文字三種錯誤都是「重試」）。
  // 只有 willRetry:false 才是我們該接手的時機 —— app-server 自己說它放棄了。
  const IPC_CFG = {
    serverOverloaded:          { retry: true,  gapMs: 1000, note: '伺服器/模型滿載' },
    internalServerError:       { retry: true,  gapMs: 2000, note: '上游 5xx' },
    httpConnectionFailed:      { retry: true,  gapMs: 1000, note: '連線失敗' },
    responseStreamDisconnected:{ retry: true,  gapMs: 1000, note: '串流中斷' },
    // 故意不重試：
    usageLimitExceeded:        { retry: false, note: '配額用完，等重置才有用' },
    contextWindowExceeded:     { retry: false, note: 'context 爆了，重試無用' },
    misalignmentPolicyViolation:{ retry: false, note: '政策拒絕，重試無用' },
  };

  // 背景重送用的常數 —— 全部來自實錄的真實 turn/start，不是推測。
  // 錄到的重試把所有 policy 欄位送 null（app-server 會從 thread 既有狀態解），
  // 所以照抄這個行為，不去複製 permissions/sandboxPolicy 這類東西。
  const RETRY_TRIGGER = 'capacity_retry_manual';
  const NULL_FIELDS = ['approvalPolicy', 'approvalsReviewer', 'sandboxPolicy',
                       'permissions', 'model', 'effort', 'outputSchema'];
  // per-thread，必須從該 thread 自己送過的 turn/start 抄
  const TEMPLATE_FIELDS = ['cwd', 'runtimeWorkspaceRoots', 'collaborationMode',
                           'responsesapiClientMetadata', 'multiAgentMode',
                           'summary', 'personality', 'serviceTier'];
  let backgroundSend = true;

  const IDLE_RESET_MS = 60_000;   // 一條路徑安靜這麼久就把它的次數歸零（等於「這回合成功了」）
  // 全域間隔：gapMs 是 per-key 的，擋不住「同一次失敗連續長出兩顆不同的鈕」。
  // 實測過 retryCountdown 和 retry 相隔 23ms 都被按下 —— 那會送出重複的 turn。
  const GLOBAL_GAP_MS = 1500;
  const KEY_RE = /^[a-z][A-Za-z0-9]*(\.[A-Za-z0-9]+)+$/;

  /* ---------- 組出背景重送請求（純函式，可單獨測試） ---------- */
  function buildRetryRequest(threadId, tpl, uuid, now) {
    if (!threadId) throw new Error('no threadId');
    if (!tpl) throw new Error('no template for thread');
    const params = { threadId, turnTrigger: RETRY_TRIGGER,
                     clientUserMessageId: uuid(), input: [] };
    for (const k of NULL_FIELDS) params[k] = null;
    for (const k of TEMPLATE_FIELDS) if (k in tpl) params[k] = tpl[k];
    return {
      type: 'mcp-request', hostId: tpl.hostId || 'local',
      retainResponse: true, priority: 'critical', source: 'turn',
      timeoutMs: 30000, expiresAtMs: now() + 30000,
      request: { id: uuid(), method: 'turn/start', params },
    };
  }

  // 沒有模板時的退路：只放「thread/read 真的讀到的欄位」，缺的一律省略。
  // 特別是 collaborationMode —— 讀不到就不送，寧可被 app-server 拒絕（會記在 log），
  // 也不要編一個 {mode:'default'} 把人家的 Plan mode 偷偷切掉。
  function buildMinimalRetryRequest(threadId, threadInfo, uuid, now) {
    if (!threadId) throw new Error('no threadId');
    if (!threadInfo) throw new Error('no threadInfo');
    const params = { threadId, turnTrigger: RETRY_TRIGGER,
                     clientUserMessageId: uuid(), input: [] };
    for (const k of NULL_FIELDS) params[k] = null;
    if (threadInfo.cwd) params.cwd = threadInfo.cwd;
    return {
      type: 'mcp-request', hostId: 'local',
      retainResponse: true, priority: 'critical', source: 'turn',
      timeoutMs: 30000, expiresAtMs: now() + 30000,
      request: { id: uuid(), method: 'turn/start', params },
      _minimal: true,
    };
  }

  // 模板要能活過熱換 / app 重開 / 頁面重載 —— 不然最需要它的時候剛好沒有。
  const TPL_KEY = 'codexRetryHook.templates.v1';
  const TPL_MAX = 50;
  const TPL_TTL_MS = 30 * 24 * 60 * 60 * 1000;      // 30 天
  const TPL_MAX_BYTES = 200_000;                    // localStorage 別塞爆

  // 純函式：留最近的、丟過期的、超量就砍舊的
  function pruneTemplates(entries, nowMs, max, ttlMs) {
    return entries
      .filter(([, v]) => v && typeof v.at === 'number' && nowMs - v.at < ttlMs)
      .sort((a, b) => b[1].at - a[1].at)
      .slice(0, max);
  }

  /* ---------- 決策核心（可單獨測試，不碰 DOM） ---------- */
  function makeBrain(cfg, now) {
    now = now || (() => Date.now());
    const seen = new Map();               // key -> {n, last}
    let lastAny = 0;                      // 任何一條路徑上次按下的時間
    return {
      decide(key) {
        const c = cfg[key];
        if (!c) return { click: false, why: 'not whitelisted' };
        const t = now();
        if (lastAny && t - lastAny < GLOBAL_GAP_MS) {
          return { click: false, why: 'global cooldown' };
        }
        let s = seen.get(key);
        if (s && t - s.last > IDLE_RESET_MS) s = null;   // 安靜夠久 -> 重新計數
        s = s || { n: 0, last: 0 };
        if (s.n >= c.max) return { click: false, why: `cap ${c.max} reached` };
        if (s.last && t - s.last < c.gapMs) return { click: false, why: 'cooldown' };
        return {
          click: true, n: s.n + 1,
          commit: () => { seen.set(key, { n: s.n + 1, last: t }); lastAny = t; },
        };
      },
      stats: () => Object.fromEntries(seen),
      reset: () => { seen.clear(); lastAny = 0; },
    };
  }

  /* ---------- node 下只匯出核心，給 test 用 ---------- */
  if (typeof window === 'undefined') {
    if (typeof module !== 'undefined') {
      module.exports = { makeBrain, CFG, IDLE_RESET_MS, GLOBAL_GAP_MS, IPC_CFG,
                         buildRetryRequest, buildMinimalRetryRequest,
                         pruneTemplates, RETRY_TRIGGER, NULL_FIELDS, TEMPLATE_FIELDS,
                         TPL_MAX, TPL_TTL_MS };
    }
    return;
  }

  /* ---------- 從 DOM 節點走 React fiber 找出 i18n key ---------- */
  function fiberOf(el) {
    for (const k in el) {
      if (k.charCodeAt(0) === 95 && (k.startsWith('__reactFiber$') ||
                                     k.startsWith('__reactInternalInstance$'))) return el[k];
    }
    return null;
  }

  function i18nId(btn) {
    const f = fiberOf(btn);
    if (!f) return null;
    // 先往下找（<intl id> 通常是按鈕的子節點，就是那顆按鈕的文字）
    const q = [[f, 0]];
    while (q.length) {
      const [node, d] = q.shift();
      if (!node || d > 8) continue;
      const id = node.memoizedProps && node.memoizedProps.id;
      if (typeof id === 'string' && KEY_RE.test(id) && CFG.hasOwnProperty(id)) return id;
      if (node.child) q.push([node.child, d + 1]);
      if (node.sibling && d > 0) q.push([node.sibling, d]);
    }
    // 再往上找（有些是整塊錯誤區塊帶著 key）
    let up = f, d = 0;
    while (up && d++ < 12) {
      const id = up.memoizedProps && up.memoizedProps.id;
      if (typeof id === 'string' && CFG.hasOwnProperty(id)) return id;
      up = up.return;
    }
    return null;
  }

  const brain = makeBrain(CFG);
  const log = [];
  const lastWhy = new Map();          // key -> 上次記過的理由，一樣就不重複記
  function note(msg) {
    log.push(`${new Date().toLocaleTimeString()} ${msg}`);
    if (log.length > 200) log.shift();
    console.log('[codex-retry]', msg);
  }

  let sweeps = 0, lastSweepAt = 0;
  function sweep() {
    sweeps += 1; lastSweepAt = Date.now();
    const btns = document.querySelectorAll('button:not([disabled]),[role=button]');
    for (const b of btns) {
      if (b.offsetParent === null) continue;              // 看不見的不算
      const key = i18nId(b);
      if (!key) continue;
      const d = brain.decide(key);
      if (!d.click) {
        if (d.why !== 'cooldown' && d.why !== 'global cooldown' &&
            lastWhy.get(key) !== d.why) {
          lastWhy.set(key, d.why);
          note(`skip ${key}: ${d.why}`);
        }
        continue;
      }
      lastWhy.delete(key);
      d.commit();
      threads.forEach((v) => { v.handled = true; });   // DOM 點擊已接手當前對話
      note(`click #${d.n} ${key} ("${(b.innerText || '').trim().slice(0, 24)}")`);
      try { b.click(); } catch (e) { note(`click failed: ${e}`); }
      return;                                             // 一輪只按一顆，等下一輪再看
    }
  }

  // ---------- IPC 偵測：app-server 的事件全部走 window message，每則帶 threadId ----------
  // 這條路不管 UI 有沒有掛載都收得到，背景 thread 也一樣。
  const threads = new Map();     // threadId -> {errorInfo, turnId, at, willRetry, active}
  function normKind(info) {
    if (typeof info === 'string') return info;
    if (info && typeof info === 'object') {
      const k = Object.keys(info)[0];
      return typeof k === 'string' ? k : null;
    }
    return null;
  }
  function onIpc(e) {
    const d = e.data;
    if (!d || typeof d !== 'object' || d.type !== 'mcp-notification') return;
    const p = d.params || {};

    if (d.method === 'error' && p.threadId) {
      const kind = normKind(p.error && p.error.codexErrorInfo);
      const cfg = kind && IPC_CFG[kind];
      threads.set(p.threadId, {
        errorInfo: kind, turnId: p.turnId, at: Date.now(),
        willRetry: !!p.willRetry, msg: (p.error && p.error.message) || '',
        handled: false,
      });
      if (p.willRetry) {
        note(`ipc ${kind}: app-server 會自己重試，不插手 (${p.threadId.slice(0, 8)})`);
      } else if (!cfg) {
        note(`ipc ${kind || '?'}: 未分類，不動作 (${p.threadId.slice(0, 8)})`);
      } else if (!cfg.retry) {
        note(`ipc ${kind}: ${cfg.note}，刻意不重試 (${p.threadId.slice(0, 8)})`);
      } else {
        note(`ipc ${kind}: ${cfg.note} (${p.threadId.slice(0, 8)})`);
        sweep();                                   // 先試 DOM（當前對話走 app 自己的路徑）
        // 2.5 秒後還卡著，就是背景 thread（DOM 沒有它的按鈕）-> 直接送 RPC
        const tid = p.threadId;
        setTimeout(() => {
          const st = threads.get(tid);
          if (!st || st.handled) return;
          const d2 = brain.decide('ipc:' + kind);
          if (!d2.click) { note('bg 略過 ' + tid.slice(0, 8) + ': ' + d2.why); return; }
          if (sendBackgroundRetry(tid)) { d2.commit(); st.handled = true; }
        }, 2500);
      }
    } else if (d.method === 'turn/started' || d.method === 'turn/completed') {
      // 新的 turn 開始/結束就清掉舊的錯誤狀態
      const t = p.threadId && threads.get(p.threadId);
      const failed = d.method === 'turn/completed' &&
                     p.turn && p.turn.status === 'failed';
      if (t && !failed) threads.delete(p.threadId);
    }
  }
  window.addEventListener('message', onIpc, true);

  // ---------- 快取每個 thread 自己送過的 turn/start，當背景重送的模板 ----------
  function loadTemplates() {
    try {
      const raw = localStorage.getItem(TPL_KEY);
      if (!raw) return new Map();
      const arr = JSON.parse(raw);
      if (!Array.isArray(arr)) return new Map();
      return new Map(pruneTemplates(arr, Date.now(), TPL_MAX, TPL_TTL_MS));
    } catch (e) { return new Map(); }
  }
  function saveTemplates() {
    try {
      let kept = pruneTemplates([...templates], Date.now(), TPL_MAX, TPL_TTL_MS);
      let json = JSON.stringify(kept);
      while (json.length > TPL_MAX_BYTES && kept.length > 1) {   // 太大就砍最舊的
        kept = kept.slice(0, Math.max(1, Math.floor(kept.length / 2)));
        json = JSON.stringify(kept);
      }
      localStorage.setItem(TPL_KEY, json);
    } catch (e) { note('模板存檔失敗: ' + e); }
  }

  const templates = loadTemplates();
  function onOutbound(e) {
    const d = e.detail;
    if (!d || d.type !== 'mcp-request') return;
    const rq = d.request;
    if (!rq || rq.method !== 'turn/start') return;
    const p = rq.params || {};
    if (!p.threadId) return;
    const tpl = { hostId: d.hostId, at: Date.now() };
    for (const k of TEMPLATE_FIELDS) if (k in p) tpl[k] = p[k];
    templates.set(p.threadId, tpl);      // 只留設定欄位，input 不進快取
    saveTemplates();                     // 立刻落地，熱換/重開都不會丟
  }
  window.addEventListener('codex-message-from-view', onOutbound, true);
  document.addEventListener('codex-message-from-view', onOutbound, true);

  let minimalFallback = false;      // 預設關閉：這條路會省略 collaborationMode

  function rpc(method, params, timeoutMs) {
    return new Promise((resolve) => {
      const bridge = window.electronBridge;
      if (!bridge || typeof bridge.sendMessageFromView !== 'function') {
        resolve(null); return;
      }
      const id = crypto.randomUUID();
      let done = false;
      const h = (e) => {
        const d = e.data;
        if (!d || typeof d !== 'object') return;
        const msg = d.message;
        if (!msg || msg.id !== id) return;
        done = true;
        window.removeEventListener('message', h, true);
        resolve(msg.result !== undefined ? msg.result : msg);
      };
      window.addEventListener('message', h, true);
      bridge.sendMessageFromView({
        type: 'mcp-request', hostId: 'local', priority: 'background',
        request: { id, method, params },
      }).catch(() => {});
      setTimeout(() => {
        if (done) return;
        window.removeEventListener('message', h, true);
        resolve(null);
      }, timeoutMs || 8000);
    });
  }

  function sendBackgroundRetry(threadId) {
    const short = threadId.slice(0, 8);
    if (!backgroundSend) { note('bg 已停用，跳過 ' + short); return false; }
    const tpl = templates.get(threadId);
    if (!tpl) {
      if (!minimalFallback) {
        note('bg 無模板可抄，' + short + ' 只記錄不送出'
             + '（在該對話送一則訊息即可建立模板）');
        return false;
      }
      // 退路：唯讀讀回 thread 資訊，只用讀到的欄位
      rpc('thread/read', { threadId }).then((res) => {
        const info = res && (res.thread || res);
        if (!info || !info.cwd) { note('bg thread/read 沒有可用資訊 ' + short); return; }
        let msg;
        try {
          msg = buildMinimalRetryRequest(threadId, info,
                                         () => crypto.randomUUID(), () => Date.now());
        } catch (err) { note('bg 最小組裝失敗 ' + short + ': ' + err.message); return; }
        try {
          window.electronBridge.sendMessageFromView(msg);
          note('bg turn/start 已送出（最小參數，省略 collaborationMode）' + short);
        } catch (err) { note('bg 最小送出失敗 ' + short + ': ' + err); }
      });
      return false;
    }
    const bridge = window.electronBridge;
    if (!bridge || typeof bridge.sendMessageFromView !== 'function') {
      note('bg 無法送出：electronBridge 不可用');
      return false;
    }
    let msg;
    try {
      msg = buildRetryRequest(threadId, tpl, () => crypto.randomUUID(), () => Date.now());
    } catch (err) { note('bg 組裝失敗 ' + short + ': ' + err.message); return false; }
    try {
      bridge.sendMessageFromView(msg);
      note('bg turn/start 已送出 ' + short + ' (' + RETRY_TRIGGER + ')');
      return true;
    } catch (err) { note('bg 送出失敗 ' + short + ': ' + err); return false; }
  }

  let timer = setInterval(sweep, 700);
  const mo = new MutationObserver(() => sweep());
  mo.observe(document.documentElement, { childList: true, subtree: true });

  window.__codexRetryHook = {
    cfg: CFG,
    // 診斷用：背景視窗會不會被 Chromium 節流，看這兩個值有沒有在動
    health: () => ({ sweeps, lastSweepAt,
                     visibility: document.visibilityState, hidden: document.hidden }),
    stats: () => brain.stats(),
    log: () => log.slice(),
    sweep,
    ipcCfg: IPC_CFG,
    retryTrigger: RETRY_TRIGGER,
    templates: () => Object.fromEntries(templates),
    clearTemplates() { templates.clear(); saveTemplates(); note('模板已清空'); },
    setBackgroundSend(v) { backgroundSend = !!v; note('背景重送 ' + (v ? '啟用' : '停用')); },
    // 沒有模板的 thread 是否用 thread/read 的最小參數硬送（會省略 collaborationMode）
    setMinimalFallback(v) { minimalFallback = !!v; note('最小參數退路 ' + (v ? '啟用' : '停用')); },
    rpc,                                     // 唯讀查詢用，例如 rpc('thread/read',{threadId})
    // 目前偵測到有錯誤在身上的 thread（含背景的）
    threads: () => Object.fromEntries(threads),
    stop() {
      clearInterval(timer); mo.disconnect();
      window.removeEventListener('message', onIpc, true);
      window.removeEventListener('codex-message-from-view', onOutbound, true);
      document.removeEventListener('codex-message-from-view', onOutbound, true);
      note('stopped');
    },
  };
  note('installed; 上限由 CFG 決定，不吃 app 內建次數'
       + '；載入 ' + templates.size + ' 筆模板');
})();
