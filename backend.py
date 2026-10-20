import os
import re
import urllib.parse
from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

http_client: Optional[httpx.AsyncClient] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(
        timeout=10.0,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*"
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

# Транслитерация для Bamper.by (превращает "Фара передняя" -> "fara-perednyaya")
def slugify(text: str) -> str:
    trans = {
        'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh','з':'z',
        'и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r',
        'с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'ts','ч':'ch','ш':'sh','щ':'sch',
        'ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',' ':'-'
    }
    s = text.lower().strip()
    res = []
    for char in s:
        res.append(trans.get(char, char if char.isalnum() else '-'))
    return re.sub(r'-+', '-', ''.join(res)).strip('-')

@app.get("/")
@app.get("/ping")
async def ping():
    return {"status": "ok", "service": "PartsRB Backend Engine"}

@app.post("/api/search", response_model=List[PartItem])
async def search_parts(req: SearchRequest):
    results: List[PartItem] = []
    clean_car = req.car.strip()
    clean_part = req.part.strip()
    
    # 1. Запрос к реальному API Kufar с жестким фильтром раздела автозапчастей
    try:
        # Для Kufar лучше всего работает поиск: "Марка Модель Деталь"
        kufar_query = f"{clean_car} {clean_part}".strip()
        encoded_kufar = urllib.parse.quote(kufar_query)
        
        kufar_url = f"https://api.kufar.by/search-api/v2/search/rendered-paginated?cat=2010&query={encoded_kufar}&size=6"
        
        resp = await http_client.get(kufar_url)
        if resp.status_code == 200:
            ads = resp.json().get("ads", [])
            for ad in ads:
                # Цена в Kufar в копейках
                price_val = float(ad.get("price_byn", 0)) / 100.0
                if price_val <= 0:
                    price_val = float(ad.get("price_usd", 0)) * 3.25

                title = ad.get("subject", clean_part)
                link = ad.get("ad_link", "")

                images = ad.get("images", [])
                img_url = None
                if images:
                    img_id = images[0].get("id")
                    if img_id:
                        img_url = f"https://rms.kufar.by/v1/list_thumbs_2x/{img_id[:2]}/{img_id}.jpg"

                if price_val > 0 and link:
                    results.append(PartItem(
                        title=title,
                        car=clean_car,
                        condition="Б/У",
                        source="Kufar Авто (Реальное объявление)",
                        sourceUrl=link,
                        city="РБ",
                        priceByn=round(price_val, 1),
                        imageUrl=img_url
                    ))
    except Exception as e:
        print(f"Kufar API error: {e}")

    # 2. Формируем ТОЧНЫЕ ссылки под каждую площадку:

    # Bamper.by: формируем правильный slug
    part_slug = slugify(clean_part)
    car_slug = slugify(clean_car)
    bamper_exact = f"https://bamper.by/search/zapchast_{part_slug}/" if part_slug else f"https://bamper.by/zchbu/?q={urllib.parse.quote(clean_part)}"

    results.append(PartItem(
        title=f"Разборки Bamper.by: {clean_part}",
        car=clean_car,
        condition="Б/У",
        source="Bamper.by",
        sourceUrl=bamper_exact,
        city="Минск / Разборки РБ",
        priceByn=140.0,
        imageUrl="https://images.unsplash.com/photo-1486006920555-c77dce18193b?w=200&auto=format&fit=crop&q=60"
    ))

    # av.by Запчасти: точный поисковый URL
    av_query = urllib.parse.quote(f"{clean_car} {clean_part}")
    results.append(PartItem(
        title=f"Каталог av.by: {clean_part}",
        car=clean_car,
        condition="Б/У",
        source="av.by Запчасти",
        sourceUrl=f"https://zapchasti.av.by/search?query={av_query}",
        city="Вся Беларусь",
        priceByn=120.0,
        imageUrl="https://images.unsplash.com/photo-1517524008697-84bbe3c3fd98?w=200&auto=format&fit=crop&q=60"
    ))

    # Motorland.by: точный складской поиск
    motor_query = urllib.parse.quote(f"{clean_car} {clean_part}")
    results.append(PartItem(
        title=f"Склад Motorland: {clean_part}",
        car=clean_car,
        condition="Б/У",
        source="Motorland.by",
        sourceUrl=f"https://motorland.by/auto-parts/?search_string={motor_query}",
        city="Минск (Колядичи)",
        priceByn=155.0,
        imageUrl="https://images.unsplash.com/photo-1619642751034-765dfdf7c58e?w=200&auto=format&fit=crop&q=60"
    ))

    # Mg.by: поиск по названию детали или OEM
    mg_query = urllib.parse.quote(clean_part)
    results.append(PartItem(
        title=f"Новый аналог / склад Mg.by: {clean_part}",
        car=clean_car,
        condition="Новый",
        source="Mg.by / Armtek",
        sourceUrl=f"https://mg.by/Search?Query={mg_query}",
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
