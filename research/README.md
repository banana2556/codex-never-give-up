# 探針：每一支發現了什麼

這些是當初把協定挖出來的工具。Codex 改版後 hook 失效時，用它們重新對格式。
全部只讀，除了 `probe-threadread.py` 會發一個唯讀 RPC。

先用 GUI 或 `codex-inject.py` 注入（需要 debug port 9333）。

| 檔案 | 發現 |
|---|---|
| `probe-bridge.py` | `window.electronBridge` 是 frozen + sealed，21 個成員全不可寫 —— 不能掛勾，但可以呼叫。裡面有 `sendMessageFromView` |
| `probe-net.py` | renderer **零網路活動、零 WebSocket** —— 全部走 Electron IPC。也證實 `client` 那個物件是 react-query 的 QueryClient，不是 app-server client |
| `probe-ipc.py` | app-server 的通知走 `window` 的 `message` 事件（`MessagePort` 那條是 app state 同步，不是這個） |
| `catch-overload.py` | 抓到真實的過載通知：`method:"error"` + `codexErrorInfo:"serverOverloaded"` + **`willRetry:false`** + `threadId` |
| `probe-threads.py` | 只有當前開啟的對話會掛載在 DOM 裡 —— 背景對話的按鈕根本不存在。側邊欄也沒有重試鈕 |
| `tap-request.py` | 送出方向的信封：`{type:'mcp-request', hostId, request:{id, method, params}, priority, source}`。送出時 transport 會派發 `codex-message-from-view` CustomEvent，`detail` 就是原始信封 |
| `arm-retry-trigger.py` | 只錄 `input` 為空的 `turn/start`，抓到重試專用的 `turnTrigger: "capacity_retry_manual"`，且所有 policy 欄位為 `null` |
| `probe-threadread.py` | `thread/read` 回得出 `cwd` / `model` / `reasoningEffort`，但**回不出** `collaborationMode` —— 所以模板不能完全靠它重建 |
| `measure429.py` | codex CLI 的重試退避**沒有上限**，純翻倍：0.2 → 0.38 → 0.89 → … → 213.53 秒 |
