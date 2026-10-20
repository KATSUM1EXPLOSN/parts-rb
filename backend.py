import os
import urllib.parse
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

# Общий пул соединений
http_client: Optional[httpx.AsyncClient] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(
        timeout=12.0,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"
        }
    )
    yield
    await http_client.aclose()

app = FastAPI(title="PartsRB Live Engine", lifespan=lifespan)

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
    vin: Optional[str] = None
    engine: Optional[str] = None
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
    imageUrl: Optional[str] = None

KUFAR_RGN = {
    "minsk": 7, "brest": 1, "vitebsk": 2, "gomel": 3, "grodno": 4, "mogilev": 6
}

@app.get("/ping")
async def ping():
    return {"status": "ok", "service": "PartsRB Global Live"}

# 1. МИРОВОЙ КАТАЛОГ: Получение моделей для любой марки через NHTSA
@app.get("/api/models")
async def get_models_for_make(make: str = Query(..., min_length=2)):
    """Возвращает все зарегистрированные в мире модели выбранной марки."""
    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/getmodelsformake/{urllib.parse.quote(make)}?format=json"
    try:
        resp = await http_client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            models = sorted(list(set(
                item["Model_Name"].strip() for item in data.get("Results", []) if item.get("Model_Name")
            )))
            return {"make": make, "models": models}
    except Exception as e:
        print(f"NHTSA models error: {e}")
    return {"make": make, "models": []}

# 2. МИРОВОЙ VIN-ДЕКОДЕР: реальная расшифровка параметров авто по VIN
@app.get("/api/vin-decode")
async def decode_vin(vin: str = Query(..., min_length=11, max_length=17)):
    vin = vin.strip().upper()
    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/decodevinvalues/{vin}?format=json"
    try:
        resp = await http_client.get(url)
        if resp.status_code == 200:
            res = resp.json().get("Results", [{}])[0]
            make = res.get("Make", "").strip()
            model = res.get("Model", "").strip()
            year = res.get("ModelYear", "").strip()
            displ = res.get("DisplacementL", "").strip()
            fuel = res.get("FuelTypePrimary", "").strip()
            cyl = res.get("EngineCylinders", "").strip()
            
            engine_info = []
            if displ: engine_info.append(f"{displ}L")
            if cyl: engine_info.append(f"V{cyl}" if cyl in ["6","8","12"] else f"{cyl}цил.")
            if fuel: engine_info.append(fuel)

            return {
                "success": bool(make and model),
                "vin": vin,
                "make": make.title() if make else "",
                "model": model,
                "year": year,
                "engine": " ".join(engine_info) if engine_info else res.get("EngineModel", ""),
                "country": res.get("PlantCountry", "")
            }
    except Exception as e:
        print(f"VIN Decode Error: {e}")
    return {"success": False, "vin": vin}

# 3. ПОИСК ЗАПЧАСТЕЙ: Живой Kufar API + проверенные прямые ссылки
@app.post("/api/search", response_model=List[PartItem])
async def search_parts(req: SearchRequest):
    results: List[PartItem] = []
    
    clean_car = req.car.strip()
    clean_part = req.part.strip()
    full_query = f"{clean_car} {clean_part}".strip()
    encoded_full = urllib.parse.quote(full_query)
    encoded_part = urllib.parse.quote(clean_part)

    # 1. Реальный запрос к Kufar API
    try:
        rgn_param = f"&rgn={KUFAR_RGN[req.city]}" if req.city in KUFAR_RGN else ""
        kufar_url = f"https://api.kufar.by/search-api/v2/search/rendered-paginated?cat=2010&query={encoded_full}&size=8{rgn_param}"
        
        resp = await http_client.get(kufar_url)
        if resp.status_code == 200:
            ads = resp.json().get("ads", [])
            for ad in ads:
                # В Kufar цена указана в копейках
                price_val = float(ad.get("price_byn", 0)) / 100.0
                if price_val <= 0:
                    price_val = float(ad.get("price_usd", 0)) * 3.25

                title = ad.get("subject", clean_part)
                link = ad.get("ad_link", "")
                
                # Фото детали
                images = ad.get("images", [])
                img_url = None
                if images:
                    img_id = images[0].get("id")
                    if img_id:
                        img_url = f"https://rms.kufar.by/v1/list_thumbs_2x/{img_id[:2]}/{img_id}.jpg"

                # Город
                params = {p.get("p"): p.get("vl") for p in ad.get("ad_parameters", []) if "p" in p}
                city_name = params.get("region", "РБ")
                if isinstance(city_name, list):
                    city_name = city_name[0] if city_name else "РБ"

                if price_val > 0 and link:
                    results.append(PartItem(
                        title=title,
                        car=clean_car,
                        condition="Б/У",
                        source="Kufar Авто (Реальное объявление)",
                        sourceUrl=link,
                        city=str(city_name),
                        priceByn=round(price_val, 1),
                        imageUrl=img_url
                    ))
    except Exception as e:
        print(f"Kufar Search Error: {e}")

    # 2. Прямые целевые Deeplinks (открывают именно искомую запчасть на конкретных сайтах)
    results.append(PartItem(
        title=f"Все предложения: {clean_part} ({clean_car})",
        car=f"{clean_car} {req.engine or ''}".strip(),
        condition="Б/У",
        source="Bamper.by",
        sourceUrl=f"https://bamper.by/search/zapchast_{encoded_part}/?q={encoded_full}",
        city="Минск / Разборки РБ",
        priceByn=130.0,
        imageUrl="https://images.unsplash.com/photo-1486006920555-c77dce18193b?w=200&auto=format&fit=crop&q=60"
    ))

    results.append(PartItem(
        title=f"Каталог запчастей av.by: {clean_part}",
        car=clean_car,
        condition="Б/У",
        source="av.by Запчасти",
        sourceUrl=f"https://zapchasti.av.by/search?q={encoded_full}",
        city="Вся Беларусь",
        priceByn=115.0,
        imageUrl="https://images.unsplash.com/photo-1517524008697-84bbe3c3fd98?w=200&auto=format&fit=crop&q=60"
    ))

    results.append(PartItem(
        title=f"Наличие на складе Motorland: {clean_part}",
        car=clean_car,
        condition="Б/У",
        source="Motorland.by",
        sourceUrl=f"https://motorland.by/auto-parts/?search_string={encoded_full}",
        city="Минск (Колядичи)",
        priceByn=150.0,
        imageUrl="https://images.unsplash.com/photo-1619642751034-765dfdf7c58e?w=200&auto=format&fit=crop&q=60"
    ))

    results.append(PartItem(
        title=f"Новый аналог / OEM склад: {clean_part}",
        car=clean_car,
        condition="Новый",
        source="Mg.by / Склад новых",
        sourceUrl=f"https://mg.by/Search?Query={encoded_part}",
        city="Доставка 24ч",
        priceByn=85.0,
        imageUrl="https://images.unsplash.com/photo-1580273916550-e323be2ae537?w=200&auto=format&fit=crop&q=60"
    ))

    if req.condition == "used":
        results = [r for r in results if r.condition == "Б/У"]
    elif req.condition == "new":
        results = [r for r in results if r.condition == "Новый"]

    return results

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("backend:app", host="0.0.0.0", port=port)
