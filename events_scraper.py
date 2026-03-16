import asyncio
from playwright.async_api import async_playwright
from datetime import datetime

EVENTS_URLS = [
    ("Career Events", "https://www.basf.com/in/en/careers/application/events.html"),
]

async def scrape_events():
    all_events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )

        for category, url in EVENTS_URLS:
            print(f"\n📅 Lade {category}: {url}")
            page = await context.new_page()

            try:
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_timeout(4000)

                # "Load more" / "Show more" Button klicken
                for i in range(10):
                    clicked = await page.evaluate("""
                        () => {
                            const buttons = Array.from(document.querySelectorAll('button'));
                            const btn = buttons.find(b =>
                                b.innerText && (
                                    b.innerText.trim() === 'Load more' ||
                                    b.innerText.trim() === 'Show more' ||
                                    b.innerText.trim() === 'More' ||
                                    b.innerText.trim() === 'Mehr zeigen'
                                )
                            );
                            if (btn) { btn.click(); return btn.innerText.trim(); }
                            return null;
                        }
                    """)
                    if clicked:
                        print(f"  🖱 Klick {i+1}: '{clicked}'")
                        await page.wait_for_timeout(2000)
                    else:
                        print(f"  ✅ Kein Button mehr — {i} Klicks gesamt")
                        break

                # Events extrahieren
                event_items = await page.query_selector_all(
                    ".event-item, .events-list__item, article.event, "
                    "[class*='event'], li[class*='event']"
                )
                print(f"  Gefundene DOM-Elemente: {len(event_items)}")

                if len(event_items) == 0:
                    event_items = await page.query_selector_all(
                        "li:has(time), div:has(time), "
                        ".teaser, [class*='teaser'], [class*='list-item']"
                    )
                    print(f"  Fallback-Elemente: {len(event_items)}")

                for item in event_items:
                    try:
                        date_el   = await item.query_selector("time, [class*='date'], [class*='Date']")
                        date_text = ""
                        date_iso  = ""
                        if date_el:
                            date_text = (await date_el.inner_text()).strip()
                            date_iso  = await date_el.get_attribute("datetime") or ""

                        title_el = await item.query_selector(
                            "h2, h3, h4, [class*='title'], [class*='Title'], strong"
                        )
                        title = ""
                        if title_el:
                            title = (await title_el.inner_text()).strip()

                        link_el    = await item.query_selector("a[href*='/events/'], a:has-text('Read more'), a:has-text('Details')")
                        detail_url = ""
                        if link_el:
                            href = await link_el.get_attribute("href")
                            if href:
                                detail_url = href if href.startswith("http") else f"https://www.basf.com{href}"

                        loc_el   = await item.query_selector("[class*='location'], [class*='Location'], [class*='place']")
                        location = ""
                        if loc_el:
                            location = (await loc_el.inner_text()).strip()

                        if title and len(title) > 3 and title not in ["Load more", "Show more", "More"]:
                            event = {
                                "title":        title,
                                "date_text":    date_text,
                                "date_iso":     date_iso,
                                "location":     location,
                                "category":     category,
                                "url":          detail_url,
                            }
                            if not any(e["title"] == title and e["date_iso"] == date_iso for e in all_events):
                                all_events.append(event)

                    except Exception as e:
                        print(f"  ⚠ Fehler bei Event-Item: {e}")
                        continue

            except Exception as e:
                print(f"  ❌ Fehler beim Laden von {url}: {e}")
            finally:
                await page.close()

        await browser.close()

    print(f"\n✅ {len(all_events)} Events gefunden")

    all_events.sort(key=lambda e: e.get("date_iso") or e.get("date_text") or "")

    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = ""
    for e in all_events:
        url_link = f'<a href="{e["url"]}">Details →</a>' if e.get("url") else ""
        rows += f"""<div class="event">
  <h2>{e['title']}</h2>
  <p><strong>Date:</strong> {e['date_text']} {f"({e['date_iso']})" if e['date_iso'] and e['date_iso'] != e['date_text'] else ""}</p>
  {"<p><strong>Location:</strong> " + e['location'] + "</p>" if e['location'] else ""}
  <p><strong>Category:</strong> {e['category']}</p>
  {"<p>" + url_link + "</p>" if url_link else ""}
</div>
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BASF India – Events & Career Fairs</title>
<style>
  body   {{ font-family: Arial, sans-serif; max-width: 860px; margin: 40px auto; padding: 0 20px; }}
  h1     {{ color: #004a96; }}
  .event {{ border-bottom: 1px solid #eee; padding: 16px 0; }}
  .event h2 {{ color: #333; font-size: 1.1em; margin: 0 0 8px 0; }}
  .event p  {{ margin: 3px 0; font-size: 0.9em; color: #555; }}
  a      {{ color: #004a96; }}
  .meta  {{ color: #777; font-size: 0.9em; margin-bottom: 24px; }}
</style>
</head>
<body>
<p><a href="index.html">← India Overview</a></p>
<h1>🌏 BASF India – Events & Career Fairs</h1>
<p class="meta">Updated: {timestamp} | {len(all_events)} events</p>
{rows}
</body>
</html>"""

    with open("events.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ events.html gespeichert!")


asyncio.run(scrape_events())
