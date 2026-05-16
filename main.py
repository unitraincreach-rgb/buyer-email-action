import os
import re
import time
import uuid
from urllib.parse import urlparse

import pandas as pd
import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


app = FastAPI(
    title="Buyer Search Email Collector API",
    servers=[
        {"url": "https://buyer-email-action.onrender.com"}
    ]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

FILES_DIR = "files"
os.makedirs(FILES_DIR, exist_ok=True)


class SearchEmailRequest(BaseModel):
    query: str = Field(..., description="Google search query, for example: gland packing site:.nz -pdf")
    pages: int = Field(1, description="Number of Google result pages to collect. 1 page is about 10 results.")
    country: str = Field("nz", description="Google country code. Example: nz, us, au")
    language: str = Field("en", description="Google language code. Example: en")


class DomainEmailRequest(BaseModel):
    domains: list[str]


def normalize_domain(value: str) -> str | None:
    if not value:
        return None

    value = value.strip()

    if not value.startswith("http"):
        value = "https://" + value

    parsed = urlparse(value)
    domain = parsed.netloc.lower()

    if not domain:
        return None

    domain = domain.replace("www.", "")

    bad_domains = [
        "google.com", "youtube.com", "facebook.com", "linkedin.com",
        "instagram.com", "x.com", "twitter.com", "pinterest.com",
        "reddit.com", "wikipedia.org"
    ]

    if any(bad in domain for bad in bad_domains):
        return None

    return domain


def search_google_with_serpapi(query: str, pages: int = 1, country: str = "nz", language: str = "en") -> list[dict]:
    results = []

    pages = max(1, min(int(pages), 10))

    for page in range(pages):
        start = page * 10

        params = {
            "engine": "google",
            "q": query,
            "api_key": SERPAPI_KEY,
            "start": start,
            "num": 10,
            "gl": country,
            "hl": language,
        }

        try:
    response = requests.get(
        "https://serpapi.com/search.json",
        params=params,
        timeout=30
    )

    response.raise_for_status()
    data = response.json()

except Exception as e:
    return {
        "status": "error",
        "message": str(e)
    }

        organic_results = data.get("organic_results", [])

        for item in organic_results:
            link = item.get("link")
            title = item.get("title", "")
            snippet = item.get("snippet", "")

            if link:
                domain = normalize_domain(link)

                if domain:
                    results.append({
                        "company_title": title,
                        "domain": domain,
                        "url": link,
                        "snippet": snippet
                    })

        time.sleep(0.5)

    # domain 기준 중복 제거
    unique = {}
    for item in results:
        if item["domain"] not in unique:
            unique[item["domain"]] = item

    return list(unique.values())


def hunter_domain_search(domain: str) -> list[dict]:
    url = "https://api.hunter.io/v2/domain-search"

    params = {
        "domain": domain,
        "api_key": HUNTER_API_KEY
    }

    response = requests.get(url, params=params, timeout=30)
    data = response.json()

    emails = data.get("data", {}).get("emails", [])

    found = []

    if not emails:
        found.append({
            "domain": domain,
            "email": "이메일 없음",
            "confidence": "",
            "type": "",
            "first_name": "",
            "last_name": "",
            "position": "",
            "department": "",
            "linkedin": "",
            "phone_number": ""
        })
        return found

    for item in emails:
        found.append({
            "domain": domain,
            "email": item.get("value", ""),
            "confidence": item.get("confidence", ""),
            "type": item.get("type", ""),
            "first_name": item.get("first_name", ""),
            "last_name": item.get("last_name", ""),
            "position": item.get("position", ""),
            "department": item.get("department", ""),
            "linkedin": item.get("linkedin", ""),
            "phone_number": item.get("phone_number", "")
        })

    return found


def make_public_download_url(filename: str) -> str:
    base_url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if base_url:
        return f"{base_url}/download/{filename}"

    return f"/download/{filename}"


@app.get("/")
def home():
    return {
        "status": "running",
        "message": "Buyer Search Email Collector API is running"
    }


@app.post("/search-and-extract-emails")
def search_and_extract_emails(request: SearchEmailRequest):
    if not SERPAPI_KEY:
        return {
            "status": "error",
            "message": "SERPAPI_KEY is missing in Render environment variables"
        }

    if not HUNTER_API_KEY:
        return {
            "status": "error",
            "message": "HUNTER_API_KEY is missing in Render environment variables"
        }

    search_results = search_google_with_serpapi(
        query=request.query,
        pages=request.pages,
        country=request.country,
        language=request.language
    )

    all_rows = []

    for item in search_results:
        domain = item["domain"]

        try:
            emails = hunter_domain_search(domain)

            for email_row in emails:
                all_rows.append({
                    "search_query": request.query,
                    "company_title": item.get("company_title", ""),
                    "domain": domain,
                    "url": item.get("url", ""),
                    "snippet": item.get("snippet", ""),
                    **email_row
                })

        except Exception as e:
            all_rows.append({
                "search_query": request.query,
                "company_title": item.get("company_title", ""),
                "domain": domain,
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
                "email": "",
                "error": str(e)
            })

        time.sleep(0.5)

    filename = f"buyer_email_results_{uuid.uuid4().hex[:8]}.xlsx"
    filepath = os.path.join(FILES_DIR, filename)

    df = pd.DataFrame(all_rows)

    if df.empty:
        df = pd.DataFrame([{
            "search_query": request.query,
            "message": "검색 결과 또는 이메일 결과가 없습니다."
        }])

    df.to_excel(filepath, index=False)

    return {
        "status": "success",
        "query": request.query,
        "searched_domains": len(search_results),
        "rows": len(all_rows),
        "download_url": make_public_download_url(filename),
        "preview": all_rows[:20]
    }


@app.post("/extract-emails")
def extract_emails(request: DomainEmailRequest):
    if not HUNTER_API_KEY:
        return {
            "status": "error",
            "message": "HUNTER_API_KEY is missing in Render environment variables"
        }

    all_rows = []

    for raw_domain in request.domains:
        domain = normalize_domain(raw_domain)

        if not domain:
            continue

        try:
            emails = hunter_domain_search(domain)
            for email_row in emails:
                all_rows.append(email_row)
        except Exception as e:
            all_rows.append({
                "domain": domain,
                "email": "",
                "error": str(e)
            })

        time.sleep(0.5)

    filename = f"domain_email_results_{uuid.uuid4().hex[:8]}.xlsx"
    filepath = os.path.join(FILES_DIR, filename)

    df = pd.DataFrame(all_rows)

    if df.empty:
        df = pd.DataFrame([{"message": "처리할 도메인이 없습니다."}])

    df.to_excel(filepath, index=False)

    return {
        "status": "success",
        "rows": len(all_rows),
        "download_url": make_public_download_url(filename),
        "preview": all_rows[:20]
    }


@app.get("/download/{filename}")
def download_file(filename: str):
    filepath = os.path.join(FILES_DIR, filename)
    return FileResponse(
        filepath,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename
    )
