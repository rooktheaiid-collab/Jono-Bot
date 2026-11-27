async def handle(tool, args, ctx):
    mem, user = ctx["memory"], ctx["user"]
    if tool == "simpan_fakta":
        return {"tersimpan": bool(mem.add_fact(user, str(args.get("fakta", ""))))}
    if tool == "cari_ingatan":
        return {"hasil": mem.search(user, str(args.get("kata", "")))}
    return {"error": "tool tidak dikenal"}
