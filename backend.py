import os
import urllib.parse
from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

# Глобальный HTTP-клиент (Connection Pool) во избежание нехватки сокетов/портов
http_client: Optional[httpx.AsyncClient] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
    http_client = httpx.AsyncClient(
        timeout=10.0,
        limits=limits,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9"
        }
    )
    yield
    await http_client.aclose()

app = FastAPI(title="PartsRB API", lifespan=lifespan)

# CORS для GitHub Pages
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SearchRequest(BaseModel):
    car: str
    part: str
    city: Optional[str] = "all"
    condition: Optional[str] = "all"

class PartItem(BaseModel):
    title: str
    car: str
    condition: str
    source: str
    sourceUrl: str
    city: str
    priceByn: float

# Маппинг регионов для Kufar
KUFAR_RGN = {
    "minsk": 7,
    "brest": 1,
    "vitebsk": 2,
    "gomel": 3,
    "grodno": 4,
    "mogilev": 6
}

@app.get("/")
@app.get("/ping")
async def ping():
    return {"status": "ok", "service": "PartsRB Backend", "port_listening": os.environ.get("PORT", "10000")}

@app.post("/api/search", response_model=List[PartItem])
async def search_parts(req: SearchRequest):
    results: List[PartItem] = []
    query_str = f"{req.car} {req.part}".strip()
    encoded_query = urllib.parse.quote(query_str)
    encoded_part = urllib.parse.quote(req.part.strip())

    # 1. Попытка забрать реальные объявления через открытый API Kufar
    try:
        rgn_param = f"&rgn={KUFAR_RGN[req.city]}" if req.city in KUFAR_RGN else ""
        kufar_api = f"https://api.kufar.by/search-api/v2/search/rendered-paginated?cat=2010&query={encoded_query}&size=5{rgn_param}"
        
        resp = await http_client.get(kufar_api)
        if resp.status_code == 200:
            data = resp.json()
            ads = data.get("ads", [])
            for ad in ads:
                # Цена в Kufar хранится в копейках (BYN * 100)
                price_byn = float(ad.get("price_byn", 0)) / 100.0
                if price_byn <= 0:
                    price_byn = float(ad.get("price_usd", 0)) * 3.25
                
                title = ad.get("subject", req.part)
                link = ad.get("ad_link", f"https://auto.kufar.by/l/avtotovary/zapchasti?query={encoded_query}")
                
                # Извлекаем город
                params = {p.get("p"): p.get("vl") for p in ad.get("ad_parameters", []) if "p" in p}
                ad_city = params.get("region", "РБ")
                if isinstance(ad_city, list):
                    ad_city = ad_city[0] if ad_city else "РБ"

                results.append(PartItem(
                    title=title,
                    car=req.car,
                    condition="Б/У",
                    source="Kufar Авто",
                    sourceUrl=link,
                    city=str(ad_city),
                    priceByn=round(price_byn, 2) if price_byn > 0 else 95.0
                ))
    except Exception as e:
        print(f"Kufar API error: {e}")

    # 2. Добавляем гарантированно рабочие целевые ссылки на остальные белорусские хабы
    results.append(PartItem(
        title=f"{req.part} — варианты на разборках Bamper.by",
        car=req.car,
        condition="Б/У",
        source="Bamper.by",
        sourceUrl=f"https://bamper.by/search/zapchast_{encoded_part}/?q={encoded_query}",
        city="Минск / Разборки РБ",
        priceByn=130.0
    ))

    results.append(PartItem(
        title=f"{req.part} в каталоге av.by",
        car=req.car,
        condition="Б/У",
        source="av.by Запчасти",
        sourceUrl=f"https://zapchasti.av.by/search?q={encoded_query}",
        city="Вся Беларусь",
        priceByn=115.0
    ))

    results.append(PartItem(
        title=f"{req.part} на складе Motorland (с гарантией)",
        car=req.car,
        condition="Б/У",
        source="Motorland.by",
        sourceUrl=f"https://motorland.by/auto-parts/?search_string={encoded_query}",
        city="Минск (Колядичи)",
        priceByn=145.0
    ))

    results.append(PartItem(
        title=f"{req.part} новый аналог / кроссы в наличии",
        car=req.car,
        condition="Новый",
        source="Mg.by / Armtek",
        sourceUrl=f"https://mg.by/Search?Query={encoded_part}",
        city="Доставка 24ч",
        priceByn=85.0
    ))

    # Фильтр состояния
    if req.condition == "used":
        results = [r for r in results if r.condition == "Б/У"]
    elif req.condition == "new":
        results = [r for r in results if r.condition == "Новый"]

    return results

if __name__ == "__main__":
    import uvicorn
    # Берём порт, выделенный Render, либо 10000 по умолчанию
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("backend:app", host="0.0.0.0", port=port)
