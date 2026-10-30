"""PNG-иконки PWA из public/icon.svg (Chromium через Playwright).

any      — скруглённый квадрат, как в SVG: icon-192.png, icon-512.png
maskable — фон во весь квадрат, знак в безопасной зоне 80%: icon-maskable-512.png
apple    — без прозрачности, iOS скругляет сам: apple-touch-icon.png (180)
Запуск: python3 scripts/make_icons.py
"""
import asyncio, pathlib
from playwright.async_api import async_playwright

PUB = pathlib.Path(__file__).resolve().parent.parent / "public"
ACCENT, PAPER = "#E2553A", "#FFFDF8"
GLYPH = f'''<rect x="206" y="118" width="100" height="178" rx="50" fill="{PAPER}"/>
<path d="M160 250a96 96 0 0 0 192 0" fill="none" stroke="{PAPER}" stroke-width="28" stroke-linecap="round"/>
<path d="M256 346v48M206 394h100" stroke="{PAPER}" stroke-width="28" stroke-linecap="round"/>'''

def svg(kind: str) -> str:
    if kind == "any":
        body = f'<rect width="512" height="512" rx="112" fill="{ACCENT}"/>{GLYPH}'
    else:  # maskable / apple: фон на всю площадь, знак уменьшен к центру
        body = f'<rect width="512" height="512" fill="{ACCENT}"/><g transform="translate(256 256) scale(.78) translate(-256 -256)">{GLYPH}</g>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">{body}</svg>'

OUT = [("icon-192.png", 192, "any"), ("icon-512.png", 512, "any"),
       ("icon-maskable-512.png", 512, "maskable"), ("apple-touch-icon.png", 180, "apple")]

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for name, size, kind in OUT:
            pg = await b.new_page(viewport={"width": size, "height": size})
            await pg.set_content(f'<html><body style="margin:0;background:transparent">'
                                 f'<div style="width:{size}px;height:{size}px">{svg(kind).replace("<svg ", f"<svg width=\"{size}\" height=\"{size}\" ")}</div></body></html>')
            await pg.screenshot(path=str(PUB / name), omit_background=(kind == "any"), clip={"x": 0, "y": 0, "width": size, "height": size})
            await pg.close()
            print(name, size)
        await b.close()

asyncio.run(main())
