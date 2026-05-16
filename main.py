import os
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
    servers=[{"url": "https://buyer-email-action.onrender.com"}],
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
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://buyer-email-action.onrender.com").rstrip("/")

FILES_DIR = "files"
os.makedirs(FILES_DIR, exist_ok=True)


class SearchEmailRequest(BaseModel):
    query: str = Field(..., description="Google search query")
    pages: int = Field(1, description="Number of Google result pages")
    country: str = Field("nz", description="Google country code")
    language: str = Field("en", description="Google language code")


class DomainEmailRequest(BaseModel):
    domains: list[str]


def normalize_domain(value: str):
    if not value:
        return None

    value = value.strip()

    if not value.startswith("http"):
        value = "https://" + value

    parsed = urlparse(value)
    domain = parsed.netloc.lower().replace("www.", "")

    if not domain:
        return None

    blocked = [
        "google.com",
        "youtube.com",
        "facebook.com",
        "linkedin.com",
        "instagram.com",
        "x.com",
        "twitter.com",
        "reddit.com",
        "wikipedia.org",
    ]

    if any(bad in domain for bad in blocked):
        return None

    return domain


def search_google_with_serpapi(query: str, pages: int, country: str, language: str):
    results = []
    pages = max(1, min(int(pages), 10))

    for page in range(pages):
        params = {
            "engine": "google",
            "q": query,
            "api_key": SERPAPI_KEY,
            "start": page * 10,
            "num": 10,
            "gl": country,
            "hl": language,
        }

        try:
            response = requests.get(
                "https://serpapi.com/search.json",
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

       except Exception as e:
         print("ERROR:", str(e))
         return {
          "results": [],
          "error": str(e)
         }

        organic_results = data.get("organic_results", [])

        for item in organic_results:
            link = item.get("link")
            title = item.get("title", "")
            snippet = item.get("snippet", "")

            domain = normalize_domain(link)

            if domain:
                results.append(
                    {
                        "company_title": title,
                        "domain": domain,
                        "url": link,
                        "snippet": snippet,
                    }
                )

        time.sleep(0.5)

    unique = {}
    for item in results:
        unique[item["domain"]] = item

    return {
        "status": "success",
        "message": "search completed",
        "results": list(unique.values()),
    }


def hunter_domain_search(domain: str):
    url = "https://api.hunter.io/v2/domain-search"

    params = {
        "domain": domain,
        "api_key": HUNTER_API_KEY,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

    except Exception as e:
        return [
            {
                "domain": domain,
                "email": "",
                "error": f"Hunter API failed: {str(e)}",
            }
        ]

    emails = data.get("data", {}).get("emails", [])

    if not emails:
        return [
            {
                "domain": domain,
                "email": "이메일 없음",
                "confidence": "",
                "type": "",
                "first_name": "",
                "last_name": "",
                "position": "",
                "department": "",
            }
        ]

    rows = []

    for item in emails:
        rows.append(
            {
                "domain": domain,
                "email": item.get("value", ""),
                "confidence": item.get("confidence", ""),
                "type": item.get("type", ""),
                "first_name": item.get("first_name", ""),
                "last_name": item.get("last_name", ""),
                "position": item.get("position", ""),
                "department": item.get("department", ""),
            }
        )

    return rows


@app.get("/")
def home():
    return {"status": "running"}


@app.post("/search-and-extract-emails")
def search_and_extract_emails(request: SearchEmailRequest):
    if not SERPAPI_KEY:
        return {"status": "error", "message": "SERPAPI_KEY is missing"}

    if not HUNTER_API_KEY:
        return {"status": "error", "message": "HUNTER_API_KEY is missing"}

    search_response = search_google_with_serpapi(
        query=request.query,
        pages=request.pages,
        country=request.country,
        language=request.language,
    )

    if search_response.get("status") != "success":
        return search_response

    search_results = search_response.get("results", [])

    all_rows = []

    for item in search_results:
        domain = item["domain"]
        email_rows = hunter_domain_search(domain)

        for email_row in email_rows:
            all_rows.append(
                {
                    "search_query": request.query,
                    "company_title": item.get("company_title", ""),
                    "domain": domain,
                    "url": item.get("url", ""),
                    "snippet": item.get("snippet", ""),
                    **email_row,
                }
            )

        time.sleep(0.5)

    if not all_rows:
        all_rows.append(
            {
                "search_query": request.query,
                "message": "검색 결과 또는 이메일 결과가 없습니다.",
            }
        )

    filename = f"buyer_email_results_{uuid.uuid4().hex[:8]}.xlsx"
    filepath = os.path.join(FILES_DIR, filename)

    df = pd.DataFrame(all_rows)
    df.to_excel(filepath, index=False)

    return {
        "status": "success",
        "query": request.query,
        "searched_domains": len(search_results),
        "rows": len(all_rows),
        "download_url": f"{PUBLIC_BASE_URL}/download/{filename}",
        "preview": all_rows[:20],
    }


@app.post("/extract-emails")
def extract_emails(request: DomainEmailRequest):
    all_rows = []

    for raw_domain in request.domains:
        domain = normalize_domain(raw_domain)

        if not domain:
            continue

        email_rows = hunter_domain_search(domain)
        all_rows.extend(email_rows)

    filename = f"domain_email_results_{uuid.uuid4().hex[:8]}.xlsx"
    filepath = os.path.join(FILES_DIR, filename)

    df = pd.DataFrame(all_rows)
    df.to_excel(filepath, index=False)

    return {
        "status": "success",
        "rows": len(all_rows),
        "download_url": f"{PUBLIC_BASE_URL}/download/{filename}",
        "preview": all_rows[:20],
    }


@app.get("/download/{filename}")
def download_file(filename: str):
    filepath = os.path.join(FILES_DIR, filename)
    return FileResponse(filepath, filename=filename)
