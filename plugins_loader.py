"""Plugin loader. Tiap plugin = folder plugins/<nama>/ berisi manifest.json + plugin.py.
manifest: {"name","description","version","enabled":true,"tools":[{"name","description","parameters":{JSON schema, tipe UPPERCASE}}]}
plugin.py: async def handle(tool: str, args: dict, ctx: dict) -> dict|str   (ctx: user, memory)
Nonaktifkan: "enabled": false di manifest, atau buat file DISABLED di folder. Muat ulang: restart atau POST /api/plugins/reload."""
import os, json, importlib.util, asyncio, logging
log = logging.getLogger("voice")
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
_plugins = {}   # name -> dict(manifest, module, path)
_tools = {}     # tool name -> plugin name
_errors = {}

def load_all():
    global _plugins, _tools, _errors
    plugins, tools, errors = {}, {}, {}
    if os.path.isdir(DIR):
        for d in sorted(os.listdir(DIR)):
            p = os.path.join(DIR, d)
            mf = os.path.join(p, "manifest.json")
            if not os.path.isfile(mf): continue
            try:
                m = json.load(open(mf))
                name = m["name"]
                if not m.get("enabled", True) or os.path.exists(os.path.join(p, "DISABLED")):
                    errors[name] = "nonaktif"; continue
                spec = importlib.util.spec_from_file_location(f"va_plugin_{d}", os.path.join(p, "plugin.py"))
                mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
                if not hasattr(mod, "handle"): raise ValueError("plugin.py tidak punya handle()")
                tl = []
                for t in m.get("tools", []):
                    if t["name"] in tools: log.info("plugin %s: tool %s bentrok, dilewati", name, t["name"]); continue
                    tools[t["name"]] = name; tl.append(t)
                m["tools"] = tl
                plugins[name] = {"manifest": m, "module": mod, "path": p}
            except Exception as e:
                errors[d] = f"{type(e).__name__}: {e}"
                log.info("plugin %s gagal dimuat: %s", d, type(e).__name__)
    _plugins, _tools, _errors = plugins, tools, errors
    return info()

def info():
    return {"plugins": [{"name": n, "description": v["manifest"].get("description", ""), "version": v["manifest"].get("version", ""),
                         "tools": [t["name"] for t in v["manifest"]["tools"]]} for n, v in _plugins.items()],
            "errors": _errors}

def declarations():
    out = []
    for v in _plugins.values():
        for t in v["manifest"]["tools"]:
            d = {"name": t["name"], "description": t.get("description", "")}
            if t.get("parameters"): d["parameters"] = t["parameters"]
            out.append(d)
    return out

def prompts():
    return [v["manifest"]["prompt"] for v in _plugins.values() if v["manifest"].get("prompt")]

async def call(tool, args, ctx, timeout=20):
    pn = _tools.get(tool)
    if not pn: return {"error": "tool tidak dikenal"}
    try:
        r = await asyncio.wait_for(_plugins[pn]["module"].handle(tool, args or {}, ctx), timeout)
        return r if isinstance(r, dict) else {"hasil": r}
    except asyncio.TimeoutError:
        return {"error": "timeout"}
    except Exception as e:
        log.info("plugin %s error: %s", pn, type(e).__name__)
        return {"error": f"gagal ({type(e).__name__})"}
