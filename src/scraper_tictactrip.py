"""
Module de Scraping Tictactrip
Extrait automatiquement les prix réels des billets TER à partir des métadonnées officielles schema.org JSON-LD de Tictactrip.
"""

import asyncio
import json
import re
import unicodedata
from typing import Optional
from playwright.async_api import async_playwright

def slugify(text: str) -> str:
    """Normalise le nom de la ville en slug d'URL (sans accents, sans majuscules)."""
    text = unicodedata.normalize('NFD', text).encode('ascii', 'ignore').decode('utf-8')
    text = text.lower().strip()
    text = text.replace("saint-", "st-").replace("saint ", "st-")
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')

async def get_tictactrip_real_price(orig: str, dest: str) -> Optional[float]:
    """
    Scrape le prix minimum du billet train/TER sur Tictactrip
    en lisant les données structurées schema.org TrainTrip.
    """
    orig_slug = slugify(orig)
    dest_slug = slugify(dest)
    
    url = f"https://www.tictactrip.eu/search/{orig_slug}/{dest_slug}"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            locale="fr-FR"
        )
        page = await context.new_page()
        try:
            resp = await page.goto(url, timeout=12000, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            
            scripts = await page.locator("script[type='application/ld+json']").all_inner_texts()
            await browser.close()
            
            for s in scripts:
                try:
                    data = json.loads(s)
                    if isinstance(data, dict) and data.get("@type") == "TrainTrip":
                        desc = data.get("description", "")
                        m = re.search(r'Prix entre\s*:\s*(\d+[\.,]?\d*)\s*€', desc)
                        if m:
                            return float(m.group(1).replace(',', '.'))
                        offers = data.get("offers", {})
                        if isinstance(offers, dict) and "price" in offers:
                            return float(offers["price"])
                except Exception:
                    pass
            return None
        except Exception as e:
            await browser.close()
            return None

if __name__ == "__main__":
    test_routes = [
        ("Toulouse", "Carcassonne"),
        ("Nîmes", "Montpellier"),
        ("Lyon", "Grenoble"),
        ("Arras", "Lille"),
        ("Dijon", "Besançon"),
        ("Marseille", "Toulon")
    ]
    
    async def run_demo():
        print("=== TEST DU SCRAPER TICTACTRIP STRUCTURÉ (AVEC ACCENTS) ===")
        for o, d in test_routes:
            p = await get_tictactrip_real_price(o, d)
            print(f"➜ {o} -> {d} : Prix réel scrapé = {p} €")
            
    asyncio.run(run_demo())
