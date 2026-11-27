import asyncio, plugin_forge
_busy = set()
async def _bg(user, desc):
    try:
        ok, msg = await plugin_forge.forge(user, desc)
    except Exception as e:
        ok, msg = False, f"Pembuatan plugin error: {type(e).__name__}"
    plugin_forge.record_result(user, "Buat plugin: " + desc[:50], msg, "selesai" if ok else "gagal")
    _busy.discard(user)
async def handle(tool, args, ctx):
    u = ctx["user"]
    if tool == "buat_plugin":
        d = str(args.get("deskripsi", "")).strip()
        if len(d) < 10: return {"error": "deskripsi terlalu pendek"}
        if u in _busy: return {"error": "masih ada plugin yang sedang dibuat, tunggu selesai"}
        _busy.add(u)
        asyncio.get_running_loop().create_task(_bg(u, d))
        return {"status": "dimulai", "catatan": "dibuat di latar belakang, hasil dilaporkan di sesi berikutnya"}
    if tool == "daftar_plugin_buatan": return {"plugin": plugin_forge.list_generated()}
    if tool == "hapus_plugin_buatan": return plugin_forge.remove_generated(str(args.get("nama", "")))
    return {"error": "tool tidak dikenal"}
