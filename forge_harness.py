"""Harness uji plugin hasil generate. Dijalankan di SUBPROCESS TERPISAH (python -I), env kosong, batas resource, audit hook
yang memblokir proses/shell/tulis file/hapus file (dan network kalau plugin tidak mendeklarasikannya)."""
import sys, json, asyncio, importlib.util, resource, os
sys.dont_write_bytecode = True
d, net, tp = sys.argv[1], sys.argv[2] == "1", sys.argv[3]
resource.setrlimit(resource.RLIMIT_AS, (600 * 2**20, 600 * 2**20))
resource.setrlimit(resource.RLIMIT_CPU, (40, 40))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
tests = json.load(open(tp))
import aiohttp  # noqa: F401  (impor di muka supaya hook tidak mengganggu)
BLOCK = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "ctypes.dlopen", "ctypes.dlsym",
         "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.truncate", "os.symlink", "os.link", "shutil.rmtree",
         "shutil.copyfile", "shutil.move", "os.putenv", "os.kill", "os.setuid", "pty.spawn"}
NETEV = {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.bind", "socket.sendto"}
def hook(ev, args):
    if ev in BLOCK: raise PermissionError("diblokir: " + ev)
    if not net and ev in NETEV: raise PermissionError("network tidak dideklarasikan: " + ev)
    if ev == "open":
        flags, mode = args[2], args[1]
        if (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND)) or (isinstance(mode, str) and any(c in mode for c in "wax+")):
            raise PermissionError("tulis file diblokir")
sys.addaudithook(hook)
out = {"ok": False, "results": [], "error": None}
try:
    spec = importlib.util.spec_from_file_location("va_forge_test", os.path.join(d, "plugin.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    if not hasattr(mod, "handle"): raise ValueError("tidak ada handle()")
    for t in tests:
        r = asyncio.run(asyncio.wait_for(mod.handle(t["tool"], t.get("args") or {}, {"user": "test", "memory": None}), 15))
        r = r if isinstance(r, dict) else {"hasil": r}
        s = json.dumps(r, ensure_ascii=False)
        if len(s) > 20000: raise ValueError("hasil terlalu besar")
        out["results"].append({"tool": t["tool"], "result": s[:400], "has_error": "error" in r})
    out["ok"] = True
except BaseException as e:
    out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
print("@@RESULT@@" + json.dumps(out, ensure_ascii=False))
