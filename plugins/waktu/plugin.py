import datetime, aiohttp
from zoneinfo import ZoneInfo
HARI = ["Senin","Selasa","Rabu","Kamis","Jumat","Sabtu","Minggu"]
WMO = {0:"cerah",1:"cerah berawan",2:"berawan",3:"mendung",45:"berkabut",48:"berkabut",51:"gerimis",53:"gerimis",55:"gerimis lebat",61:"hujan ringan",63:"hujan",65:"hujan deras",80:"hujan lokal",81:"hujan lokal",82:"hujan lebat",95:"badai petir",96:"badai petir",99:"badai petir"}

async def handle(tool, args, ctx):
    if tool == "waktu_sekarang":
        try: z = ZoneInfo(str(args.get("zona") or "Asia/Jakarta"))
        except Exception: return {"error": "zona tidak valid"}
        n = datetime.datetime.now(z)
        return {"zona": str(z), "hari": HARI[n.weekday()], "tanggal": n.strftime("%d-%m-%Y"), "jam": n.strftime("%H:%M")}
    if tool == "cuaca":
        kota = str(args.get("kota") or "Cileungsi")[:60]
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": kota, "count": 1, "language": "id"}) as r:
                g = (await r.json()).get("results")
            if not g: return {"error": f"kota '{kota}' tidak ketemu"}
            g = g[0]
            async with s.get("https://api.open-meteo.com/v1/forecast", params={"latitude": g["latitude"], "longitude": g["longitude"],
                    "current": "temperature_2m,weather_code,wind_speed_10m,precipitation", "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "auto", "forecast_days": 2}) as r:
                j = await r.json()
        c, d = j["current"], j["daily"]
        return {"kota": g["name"], "sekarang": {"suhu_c": c["temperature_2m"], "kondisi": WMO.get(c["weather_code"], "tidak diketahui"), "angin_kmh": c["wind_speed_10m"]},
                "hari_ini": {"min": d["temperature_2m_min"][0], "max": d["temperature_2m_max"][0], "peluang_hujan_persen": d["precipitation_probability_max"][0]},
                "besok": {"min": d["temperature_2m_min"][1], "max": d["temperature_2m_max"][1], "peluang_hujan_persen": d["precipitation_probability_max"][1]},
                "sumber": "Open-Meteo"}
    return {"error": "tool tidak dikenal"}
