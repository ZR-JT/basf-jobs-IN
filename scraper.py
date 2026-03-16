import json
import re
import asyncio
import aiohttp
import os
import glob
from playwright.async_api import async_playwright
from datetime import datetime

# ── KONFIGURATION ────────────────────────────────────────────────────────────
COUNTRY = {
    "code":   "in",
    "name":   "India",
    "filter": "addresses/any(a: a/country eq 'India')",
    "lang":   "en",
}

SEARCH_URL = "https://basf.jobs/?currentPage=1&pageSize=1000&addresses%2Fcountry=India"
AZURE_URL  = "https://searchui.search.windows.net/indexes/basf-prod/docs/search?api-version=2020-06-30"
BASE_URL   = "https://ZR-JT.github.io/basf-jobs-IN"

PREFERRED_LOCALES = ["en_US", "en_IN", "en_GB", "de_DE"]
PAGE_SIZE = 1000
# ─────────────────────────────────────────────────────────────────────────────


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')


# ── API KEY ──────────────────────────────────────────────────────────────────
async def get_api_key():
    api_key = None
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        async def handle_request(request):
            nonlocal api_key
            if "searchui.search.windows.net" in request.url:
                headers   = dict(request.headers)
                found_key = (
                    headers.get("api-key") or
                    headers.get("Api-Key") or
                    headers.get("authorization") or ""
                )
                if found_key:
                    api_key = found_key

        context.on("request", handle_request)
        await page.goto(SEARCH_URL, timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        await browser.close()
    return api_key


# ── JOBS LADEN ───────────────────────────────────────────────────────────────
async def fetch_jobs(session, api_key):
    print(f"\n🌏 Lade Jobs für: {COUNTRY['name']}")

    all_raw_jobs = []
    skip = 0

    while True:
        search_body = {
            "search": "*",
            "filter": COUNTRY["filter"],
            "select": "*",
            "top":    PAGE_SIZE,
            "skip":   skip,
            "count":  True,
        }
        async with session.post(
            AZURE_URL,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=search_body,
        ) as resp:
            if resp.status != 200:
                print(f"  ❌ Fehler bei skip={skip}: {(await resp.text())[:300]}")
                break
            data = await resp.json()

        batch       = data.get("value", [])
        total_count = data.get("@odata.count", "?")
        if skip == 0:
            print(f"  API meldet @odata.count: {total_count}")

        all_raw_jobs.extend(batch)
        print(f"  skip={skip}: {len(batch)} geladen (gesamt: {len(all_raw_jobs)})")

        if len(batch) < PAGE_SIZE:
            break
        skip += PAGE_SIZE

    print(f"  Rohdaten: {len(all_raw_jobs)} (inkl. alle Locales)")

    # ── Deduplizieren ────────────────────────────────────────────────────────
    job_map = {}
    for job in all_raw_jobs:
        full_id    = str(job.get("jobId", ""))
        numeric_id = full_id.split("-")[0] if "-" in full_id else full_id
        language   = job.get("language", "")

        if numeric_id not in job_map:
            job_map[numeric_id] = job
        else:
            current_lang = job_map[numeric_id].get("language", "")
            current_pref = PREFERRED_LOCALES.index(current_lang) if current_lang in PREFERRED_LOCALES else 999
            new_pref     = PREFERRED_LOCALES.index(language)     if language     in PREFERRED_LOCALES else 999
            if new_pref < current_pref:
                job_map[numeric_id] = job

    print(f"  Nach Deduplizierung: {len(job_map)} unique Jobs")

    # ── Jobs aufbereiten ─────────────────────────────────────────────────────
    jobs = []
    for numeric_id, job in job_map.items():
        addr      = {}
        addresses = job.get("addresses", [])
        if isinstance(addresses, list) and addresses:
            addr = addresses[0] if isinstance(addresses[0], dict) else {}

        recruiter_raw = job.get("recruiter") or {}
        recruiter     = {}
        if recruiter_raw:
            recruiter = {
                "name":  f"{recruiter_raw.get('firstName','')} {recruiter_raw.get('lastName','')}".strip(),
                "email": recruiter_raw.get("email", ""),
                "phone": recruiter_raw.get("phone", ""),
            }
            recruiter = {k: v for k, v in recruiter.items() if v}

        # Vollständige Description ohne Zeichenlimit
        description = strip_html(job.get("description") or "")

        city  = addr.get("city")  or addr.get("locationCity") or "Unknown"
        state = addr.get("state") or "Unknown"

        entry = {
            "job_id":        numeric_id,
            "title":         (job.get("title") or "").strip(),
            "url":           job.get("link") or f"https://basf.jobs/job/{numeric_id}/",
            "city":          city,
            "state":         state,
            "country":       addr.get("country") or COUNTRY["name"],
            "company":       job.get("legalEntity")   or "BASF",
            "business_unit": job.get("businessUnit")  or "",
            "department":    job.get("department")    or "",
            "job_field":     job.get("jobField")      or job.get("category")     or "",
            "job_level":     job.get("jobLevel")      or job.get("customfield1") or "",
            "job_type":      job.get("jobType")       or job.get("customfield5") or "",
            "hybrid":        job.get("hybrid")        or False,
            "date_posted":   job.get("datePosted")    or "",
            "description":   description,
            "recruiter":     recruiter if recruiter else None,
        }
        entry = {k: v for k, v in entry.items() if v is not None and v != "" and v != {}}
        jobs.append(entry)

    jobs.sort(key=lambda j: j.get("date_posted", ""), reverse=True)
    return jobs


# ── ALTE DATEIEN LÖSCHEN ─────────────────────────────────────────────────────
def cleanup_old_files(current_job_ids):
    """Löscht veraltete Job-Seiten und alte Regionsseiten."""
    deleted_jobs = 0
    if os.path.exists("jobs"):
        for filepath in glob.glob("jobs/job-*.html"):
            job_id = os.path.basename(filepath).replace("job-", "").replace(".html", "")
            if job_id not in current_job_ids:
                os.remove(filepath)
                deleted_jobs += 1

    deleted_regions = 0
    if os.path.exists("regions"):
        for filepath in glob.glob("regions/region-*.html"):
            os.remove(filepath)
            deleted_regions += 1

    if deleted_jobs:
        print(f"  🗑  {deleted_jobs} veraltete Job-Seiten gelöscht")
    if deleted_regions:
        print(f"  🗑  {deleted_regions} alte Regionsseiten gelöscht (werden neu erstellt)")


# ── EINZELNE JOB-SEITE ───────────────────────────────────────────────────────
def generate_job_page(j, timestamp):
    job_id      = j.get("job_id", "")
    city        = j.get("city",   "")
    state       = j.get("state",  "")
    region_slug = f"region-{slugify(state)}-{slugify(city)}"
    region_url  = f"{BASE_URL}/regions/{region_slug}.html"
    description = j.get("description", "").replace("\n", "<br>")

    recruiter_html = ""
    if j.get("recruiter"):
        r     = j["recruiter"]
        parts = []
        if r.get("name"):  parts.append(f'<strong>Name:</strong> {r["name"]}')
        if r.get("email"): parts.append(f'<strong>Email:</strong> <a href="mailto:{r["email"]}">{r["email"]}</a>')
        if r.get("phone"): parts.append(f'<strong>Phone:</strong> {r["phone"]}')
        recruiter_html = (
            "<div class='recruiter'><h3>Contact / Recruiter</h3>"
            "<p>" + " &nbsp;|&nbsp; ".join(parts) + "</p></div>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{j.get('title', '')} – BASF Jobs India</title>
<style>
  body         {{ font-family: Arial, sans-serif; max-width: 860px; margin: 40px auto; padding: 0 20px; color: #333; }}
  h1           {{ color: #004a96; font-size: 1.6em; margin-bottom: 8px; }}
  h3           {{ color: #004a96; margin-top: 28px; border-bottom: 1px solid #eee; padding-bottom: 6px; }}
  .breadcrumb  {{ font-size: 0.9em; margin-bottom: 24px; }}
  .breadcrumb a {{ color: #004a96; text-decoration: none; }}
  .breadcrumb a:hover {{ text-decoration: underline; }}
  .meta        {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 16px 0 24px 0; }}
  .badge       {{ background: #f0f5fa; border: 1px solid #cde; padding: 5px 12px;
                  border-radius: 4px; font-size: 0.85em; color: #004a96; }}
  .description {{ line-height: 1.9; margin-top: 12px; }}
  .recruiter   {{ background: #f9f9f9; border-left: 4px solid #004a96;
                  padding: 14px 18px; margin-top: 28px; border-radius: 0 4px 4px 0; }}
  .recruiter p {{ margin: 0; font-size: 0.95em; }}
  .apply-btn   {{ display: inline-block; margin-top: 28px; padding: 13px 30px;
                  background: #004a96; color: #fff; text-decoration: none;
                  border-radius: 4px; font-size: 1em; font-weight: bold; }}
  .apply-btn:hover {{ background: #003070; }}
  hr           {{ border: none; border-top: 1px solid #eee; margin: 28px 0; }}
  .footer-meta {{ font-size: 0.8em; color: #aaa; margin-top: 40px; }}
</style>
</head>
<body>

<div class="breadcrumb">
  <a href="{BASE_URL}/index.html">🌏 India Overview</a>
  &nbsp;›&nbsp;
  <a href="{region_url}">{city}, {state}</a>
  &nbsp;›&nbsp;
  {j.get('title', '')}
</div>

<h1>{j.get('title', '')}</h1>

<div class="meta">
  <span class="badge">📍 {city}, {state}</span>
  {"<span class='badge'>🏢 " + j.get('company','')      + "</span>" if j.get('company')      else ""}
  {"<span class='badge'>📂 " + j.get('job_field','')    + "</span>" if j.get('job_field')    else ""}
  {"<span class='badge'>🏛 " + j.get('department','')   + "</span>" if j.get('department')   else ""}
  {"<span class='badge'>📊 " + j.get('job_level','')    + "</span>" if j.get('job_level')    else ""}
  {"<span class='badge'>⏱ "  + j.get('job_type','')     + "</span>" if j.get('job_type')     else ""}
  {"<span class='badge'>🏠 Hybrid</span>"                            if j.get('hybrid')       else ""}
  {"<span class='badge'>📅 " + j.get('date_posted','')[:10] + "</span>" if j.get('date_posted') else ""}
</div>

<hr>

<h3>Job Description</h3>
<div class="description">{description}</div>

{recruiter_html}

<a class="apply-btn" href="{j.get('url', '')}" target="_blank">Apply on basf.jobs →</a>

<hr>
<p class="footer-meta">Job ID: {job_id} &nbsp;|&nbsp; Last updated: {timestamp}</p>

</body>
</html>"""

    os.makedirs("jobs", exist_ok=True)
    with open(f"jobs/job-{job_id}.html", "w", encoding="utf-8") as f:
        f.write(html)


# ── REGIONSSEITEN ─────────────────────────────────────────────────────────────
def generate_region_page(state, city, region_jobs, timestamp):
    slug = f"region-{slugify(state)}-{slugify(city)}"
    rows = ""

    for j in region_jobs:
        job_id  = j.get("job_id", "")
        job_url = f"{BASE_URL}/jobs/job-{job_id}.html"
        rows += f"""<div class="job-row">
  <div class="job-title"><a href="{job_url}">{j.get('title', '')}</a></div>
  <div class="job-meta">
    {j.get('job_field', '')}
    {' · ' + j.get('job_level', '') if j.get('job_level') else ''}
    {' · ' + j.get('job_type', '')  if j.get('job_type')  else ''}
    {' · <strong>Hybrid</strong>'   if j.get('hybrid')    else ''}
  </div>
  <div class="job-date">📅 {j.get('date_posted', '')[:10]}</div>
</div>
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BASF Jobs – {city}, {state} | India</title>
<style>
  body      {{ font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; }}
  h1        {{ color: #004a96; }}
  .job-row  {{ border-bottom: 1px solid #eee; padding: 14px 0; }}
  .job-title {{ font-size: 1.05em; margin-bottom: 4px; }}
  .job-title a {{ color: #004a96; text-decoration: none; }}
  .job-title a:hover {{ text-decoration: underline; }}
  .job-meta {{ font-size: 0.85em; color: #666; margin-top: 3px; }}
  .job-date {{ font-size: 0.8em; color: #999; margin-top: 4px; }}
  .back     {{ color: #004a96; font-size: 0.9em; text-decoration: none; }}
  .back:hover {{ text-decoration: underline; }}
  .meta-bar {{ color: #777; font-size: 0.9em; margin-bottom: 20px; }}
</style>
</head>
<body>
<p><a class="back" href="{BASE_URL}/index.html">← India Overview</a></p>
<h1>BASF Jobs – {city}, {state}</h1>
<p class="meta-bar">Updated: {timestamp} &nbsp;|&nbsp; {len(region_jobs)} position(s)</p>
{rows}
</body>
</html>"""

    os.makedirs("regions", exist_ok=True)
    with open(f"regions/{slug}.html", "w", encoding="utf-8") as f:
        f.write(html)

    return slug


# ── INDIA INDEX ───────────────────────────────────────────────────────────────
def generate_index(jobs, regions, sorted_regions, region_slugs, timestamp):
    index_rows    = ""
    current_state = None

    for (state, city) in sorted_regions:
        if state != current_state:
            if current_state is not None:
                index_rows += "</ul>\n"
            index_rows += f"<h2>{state}</h2>\n<ul>\n"
            current_state = state

        slug        = region_slugs[(state, city)]
        region_jobs = regions[(state, city)]
        region_url  = f"{BASE_URL}/regions/{slug}.html"

        index_rows += f'<li><a href="{region_url}">{city}</a> ({len(region_jobs)} position(s))<ul>\n'
        for j in region_jobs:
            job_id  = j.get("job_id", "")
            job_url = f"{BASE_URL}/jobs/job-{job_id}.html"
            index_rows += (
                f'  <li>{j.get("date_posted","")[:10]} – '
                f'<a href="{job_url}">{j.get("title","")}</a></li>\n'
            )
        index_rows += '</ul></li>\n'

    if current_state is not None:
        index_rows += "</ul>\n"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BASF Jobs India – Overview</title>
<style>
  body  {{ font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; }}
  h1   {{ color: #004a96; }}
  h2   {{ color: #333; border-bottom: 2px solid #004a96; padding-bottom: 4px; margin-top: 32px; }}
  ul   {{ line-height: 1.9; }}
  li   {{ margin-bottom: 2px; }}
  a    {{ color: #004a96; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .meta {{ color: #777; font-size: 0.9em; margin-bottom: 28px; }}
</style>
</head>
<body>
<h1>🌏 BASF Jobs – India</h1>
<p class="meta">
  Updated: {timestamp} &nbsp;|&nbsp;
  Total: <strong>{len(jobs)} positions</strong> &nbsp;|&nbsp;
  {len(sorted_regions)} locations
</p>
{index_rows}
</body>
</html>"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ index.html gespeichert")


# ── MAIN ──────────────────────────────────────────────────────────────────────
async def scrape_jobs():
    print("🔑 Hole API Key...")
    api_key = await get_api_key()
    if not api_key:
        print("❌ Kein API Key gefunden!")
        return
    print("✅ API Key gefunden")

    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    async with aiohttp.ClientSession() as session:
        jobs = await fetch_jobs(session, api_key)

    # Aktuelle Job IDs
    current_job_ids = {j["job_id"] for j in jobs}

    # Veraltete Dateien löschen
    cleanup_old_files(current_job_ids)

    # jobs.json speichern
    with open("jobs.json", "w", encoding="utf-8") as f:
        json.dump({
            "last_updated": timestamp,
            "country":      COUNTRY["name"],
            "total_active": len(jobs),
            "jobs":         jobs,
        }, f, ensure_ascii=False, indent=2)
    print(f"✅ jobs.json gespeichert — {len(jobs)} Jobs")

    # Nach State + City gruppieren
    regions = {}
    for j in jobs:
        key = (j.get("state", "Unknown"), j.get("city", "Unknown"))
        regions.setdefault(key, []).append(j)

    sorted_regions = sorted(regions.keys(), key=lambda k: (k[0].lower(), k[1].lower()))
    region_slugs   = {}

    # Job-Seiten generieren
    for j in jobs:
        generate_job_page(j, timestamp)
    print(f"✅ {len(jobs)} Job-Seiten generiert → jobs/")

    # Regionsseiten generieren
    for (state, city) in sorted_regions:
        slug = generate_region_page(state, city, regions[(state, city)], timestamp)
        region_slugs[(state, city)] = slug
    print(f"✅ {len(sorted_regions)} Regionsseiten generiert → regions/")

    # Index generieren
    generate_index(jobs, regions, sorted_regions, region_slugs, timestamp)


asyncio.run(scrape_jobs())
