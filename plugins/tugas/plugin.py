import agent_core
async def handle(tool, args, ctx):
    u = ctx["user"]
    if tool == "buat_tugas":
        return agent_core.create_task(u, str(args.get("judul", "")), str(args.get("perintah", "")), str(args.get("jadwal", "sekarang")), args.get("menit"), args.get("jam"))
    if tool == "daftar_tugas": return {"tugas": agent_core.list_tasks(u)}
    if tool == "batalkan_tugas": return agent_core.cancel_task(u, args.get("id", 0))
    if tool == "hasil_tugas": return agent_core.get_result(u, args.get("id", 0))
    return {"error": "tool tidak dikenal"}
