import integrations
async def handle(tool, args, ctx):
    if tool == "status_integrasi":
        s = integrations.mask_status()
        return {k: (v.get("status") or ("OK" if v.get("authkey_set") else "belum")) for k, v in s.items()}
    if tool == "minta_akses":
        return integrations.request_access(ctx["user"], str(args.get("nama", "")), str(args.get("alasan", "")))
    return {"error": "tool tidak dikenal"}
