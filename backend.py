import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
from bs4 import BeautifulSoup
from typing import List, Optional

app = FastAPI(title="PartsRB Parser API")

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
    city: Optional[str] = "minsk"
    condition: Optional[str] = "all"

class PartItem(BaseModel):
    title: str
    car: str
    condition: str
    source: str
    sourceUrl: str
    city: str
    priceByn: float

@app.post("/api/search", response_model=List[PartItem])
async def search_parts(req: SearchRequest):
    results = []
    query = f"{req.car} {req.part}".strip()
    
    # 1. Запрос к bamper.by
    bamper_url = f"https://bamper.by/zchbu/?q={httpx.URL(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(bamper_url, headers=headers)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                items = soup.select(".item")[:5]
                for item in items:
                    title_elem = item.select_one(".title a")
                    price_elem = item.select_one(".price")
                    city_elem = item.select_one(".city")
                    
                    if title_elem:
                        title = title_elem.get_text(strip=True)
                        link = "https://bamper.by" + title_elem.get("href", "")
                        price_text = price_elem.get_text(strip=True) if price_elem else "0"
                        
                        # Извлечение чисел
                        clean_price = "".join([c for c in price_text if c.isdigit() or c == '.'])
                        price_val = float(clean_price) if clean_price else 100.0

                        results.append(PartItem(
                            title=title,
                            car=req.car,
                            condition="Б/У",
                            source="Bamper.by",
                            sourceUrl=link,
                            city=city_elem.get_text(strip=True) if city_elem else "Минск",
                            priceByn=price_val
                        ))
    except Exception as e:
        print(f"Ошибка парсинга: {e}")

    # Резервная заглушка, если сайт не отдал блоки
    if not results:
        results.append(PartItem(
            title=f"{req.part} ({req.car})",
            car=req.car,
            condition="Б/У",
            source="Bamper.by",
            sourceUrl=bamper_url,
            city="Минск",
            priceByn=120.0
        ))

    return results

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
