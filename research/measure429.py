"""假上游一律回 429（不帶 Retry-After），量 codex 自己的退避間隔。"""
import json, os, pathlib, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def find_codex():
    """Find the codex CLI: LOCALAPPDATA/OpenAI/Codex/bin first, then PATH."""
    import glob
    from shutil import which
    root = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'OpenAI', 'Codex', 'bin')
    hits = sorted(glob.glob(os.path.join(root, '*', 'codex.exe')),
                  key=os.path.getmtime, reverse=True)
    if hits:
        return hits[0]
    got = which('codex')
    if got:
        return got
    raise SystemExit('codex CLI not found; set CODEX_BIN')


CODEX = os.environ.get('CODEX_BIN') or find_codex()
RUN = int(sys.argv[1]) if len(sys.argv) > 1 else 95
hits = []

class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def do_POST(self):
        try: self.rfile.read(int(self.headers.get('content-length') or 0))
        except Exception: pass
        hits.append(time.monotonic())
        print(f'  hit #{len(hits)} t={hits[-1]-hits[0]:7.2f}s {self.path}', flush=True)
        b = json.dumps({"error": {"message": "server is overloaded", "type": "server_error"}}).encode()
        self.send_response(503)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass

srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

home = pathlib.Path.cwd() / 'probe-home'
home.mkdir(exist_ok=True)
(home / 'config.toml').write_text(f'''model_provider = "probe"
model = "gpt-5"

[model_providers.probe]
name = "probe"
wire_api = "responses"
base_url = "http://127.0.0.1:{port}/v1"
requires_openai_auth = false
env_key = "PROBE_KEY"
request_max_retries = 50
stream_max_retries = 50
''', encoding='utf-8')

env = {**os.environ, 'CODEX_HOME': str(home), 'PROBE_KEY': 'sk-probe'}
print(f'fake 429 upstream :{port}  跑 {RUN}s')
p = subprocess.Popen([CODEX, 'exec', '--skip-git-repo-check'], env=env, cwd=str(home),
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, text=True, errors='replace')
out=''
try: out,_ = p.communicate('hi' + chr(10), timeout=RUN)
except subprocess.TimeoutExpired:
    p.kill()
    try: out,_ = p.communicate(timeout=10)
    except Exception: pass
srv.shutdown()

print(f'\n=== {len(hits)} 次請求，間隔 ===')
for i in range(1, len(hits)):
    print(f'  #{i} -> #{i+1}: {hits[i]-hits[i-1]:7.2f}s')
