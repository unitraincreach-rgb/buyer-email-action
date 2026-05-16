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
    title="Unitra Global Buyer Finder API",
    version="3.0.0",
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
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://buyer-email-action.onrender.com"
).rstrip("/")

FILES_DIR = "files"
os.makedirs(FILES_DIR, exist_ok=True)


COUNTRY_MAP = {
    "nz": {"name": "New Zealand", "site": ".nz", "gl": "nz"},
    "au": {"name": "Australia", "site": ".au", "gl": "au"},
    "ca": {"name": "Canada", "site": ".ca", "gl": "ca"},
    "us": {"name": "United States", "site": ".us", "gl": "us"},
    "uk": {"name": "United Kingdom", "site": ".uk", "gl": "uk"},
}


class BuyerSearchRequest(BaseModel):
    product: str = Field(..., description="Product name or search query, for example backing ring or gland packing")
    country: str = Field("nz", description="Target country code. Example: nz, au, ca, us, uk")
    pages: int = Field(10, description="Google result pages per keyword. Default 10")
    language: str = Field("en", description="Google language code")
    max_keywords: int = Field(8, description="Maximum expanded keywords to search")


class SearchEmailRequest(BaseModel):
    query: str = Field(..., description="Direct Google search query")
    pages: int = Field(10, description="Number of Google result pages")
    country: str = Field("nz", description="Google country code")
    language: str = Field("en", description="Google language code")


class DomainEmailRequest(BaseModel):
    domains: list[str]


def clean_product(product: str) -> str:
    product = product.strip()
    product = re.sub(r"\s+", " ", product)
    return product


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

    blocked_domains = [
        "google.com",
        "youtube.com",
        "facebook.com",
        "instagram.com",
        "x.com",
        "twitter.com",
        "reddit.com",
        "wikipedia.org",
        "pinterest.com",
        "amazon.",
        "ebay.",
    ]

    if any(blocked in domain for blocked in blocked_domains):
        return None

    return domain


def guess_buyer_type(text: str) -> str:
    text = (text or "").lower()

    if any(word in text for word in ["importer", "imports"]):
        return "Importer"
    if any(word in text for word in ["distributor", "distribution"]):
        return "Distributor"
    if any(word in text for word in ["wholesale", "wholesaler"]):
        return "Wholesaler"
    if any(word in text for word in ["manufacturer", "factory"]):
        return "Manufacturer"
    if any(word in text for word in ["supplier", "supplies"]):
        return "Supplier"
    if "linkedin" in text:
        return "LinkedIn Company"
    if any(word in text for word in ["yellow pages", "yellowpages"]):
        return "Directory"
    if any(word in text for word in ["maps", "location"]):
        return "Google Maps Lead"

    return "Potential Buyer"


def build_keywords(product: str, country: str, max_keywords: int = 8):
    product = clean_product(product)
    country_info = COUNTRY_MAP.get(country.lower(), COUNTRY_MAP["nz"])
    country_name = country_info["name"]
    site = country_info["site"]

    keywords = [
        f'{product} supplier {country_name}',
        f'{product} importer {country_name}',
        f'{product} distributor {country_name}',
        f'{product} wholesaler {country_name}',
        f'{product} buyer {country_name}',
        f'{product} site:{site} -pdf',
        f'{product} LinkedIn company {country_name}',
        f'{product} yellow pages {country_name}',
        f'{product} Google Maps {country_name}',
        f'{product} industrial supplier {country_name}',
        f'{product} engineering supplier {country_name}',
        f'{product} stockist {country_name}',
    ]

    unique = []
    seen = set()

    for keyword in keywords:
        if keyword.lower() not in seen:
            unique.append(keyword)
            seen.add(keyword.lower())

    return unique[:max_keywords]


def serpapi_google_search(query: str, pages: int, country: str, language: str):
    if not SERPAPI_KEY:
        return {
            "status": "error",
            "message": "SERPAPI_KEY is missing",
            "results": [],
        }

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
                timeout=40,
            )
            response.raise_for_status()
            data = response.json()

        except Exception as e:
            print("SERPAPI GOOGLE ERROR:", str(e))
            return {
                "status": "error",
                "message": f"SerpAPI Google search failed: {str(e)}",
                "results": results,
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
                        "source": "Google Search",
                        "search_query": query,
                        "company_title": title,
                        "domain": domain,
                        "url": link,
                        "snippet": snippet,
                    }
                )

        time.sleep(0.4)

    return {
        "status": "success",
        "results": results,
    }


def serpapi_google_maps_search(query: str, country: str, language: str):
    if not SERPAPI_KEY:
        return {
            "status": "error",
            "message": "SERPAPI_KEY is missing",
            "results": [],
        }

    params = {
        "engine": "google_maps",
        "q": query,
        "api_key": SERPAPI_KEY,
        "gl": country,
        "hl": language,
    }

    try:
        response = requests.get(
            "https://serpapi.com/search.json",
            params=params,
            timeout=40,
        )
        response.raise_for_status()
        data = response.json()

    except Exception as e:
        print("SERPAPI MAPS ERROR:", str(e))
        return {
            "status": "error",
            "message": f"SerpAPI Google Maps search failed: {str(e)}",
            "results": [],
        }

    results = []
    local_results = data.get("local_results", [])

    for item in local_results:
        title = item.get("title", "")
        website = item.get("website", "")
        address = item.get("address", "")
        phone = item.get("phone", "")
        domain = normalize_domain(website)

        if domain:
            results.append(
                {
                    "source": "Google Maps",
                    "search_query": query,
                    "company_title": title,
                    "domain": domain,
                    "url": website,
                    "snippet": address,
                    "phone": phone,
                }
            )

    return {
        "status": "success",
        "results": results,
    }


def hunter_domain_search(domain: str):
    if not HUNTER_API_KEY:
        return [
            {
                "domain": domain,
                "email": "",
                "error": "HUNTER_API_KEY is missing",
            }
        ]

    params = {
        "domain": domain,
        "api_key": HUNTER_API_KEY,
    }

    try:
        response = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params=params,
            timeout=40,
        )
        response.raise_for_status()
        data = response.json()

    except Exception as e:
        print("HUNTER ERROR:", str(e))
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


def make_excel(rows, prefix: str):
    filename = f"{prefix}_{uuid.uuid4().hex[:8]}.xlsx"
    filepath = os.path.join(FILES_DIR, filename)

    if not rows:
        rows = [{"message": "검색 결과 또는 이메일 결과가 없습니다."}]

    df = pd.DataFrame(rows)
    df.to_excel(filepath, index=False)

    return filename


@app.get("/")
def home():
    return {
        "status": "running",
        "service": "Unitra Global Buyer Finder API",
        "version": "3.0.0",
    }


@app.post("/find-buyers")
def find_buyers(request: BuyerSearchRequest):
    product = clean_product(request.product)
    country = request.country.lower()
    country_info = COUNTRY_MAP.get(country, COUNTRY_MAP["nz"])
    country_name = country_info["name"]
    gl = country_info["gl"]
    pages = max(1, min(int(request.pages), 10))
    max_keywords = max(1, min(int(request.max_keywords), 12))

    keywords = build_keywords(product, country, max_keywords=max_keywords)

    lead_map = {}

    for keyword in keywords:
        google_response = serpapi_google_search(
            query=keyword,
            pages=pages,
            country=gl,
            language=request.language,
        )

        for item in google_response.get("results", []):
            domain = item.get("domain")
            if domain and domain not in lead_map:
                lead_map[domain] = item

        if any(word in keyword.lower() for word in ["supplier", "distributor", "wholesaler"]):
            maps_response = serpapi_google_maps_search(
                query=keyword,
                country=gl,
                language=request.language,
            )

            for item in maps_response.get("results", []):
                domain = item.get("domain")
                if domain and domain not in lead_map:
                    lead_map[domain] = item

        time.sleep(0.5)

    all_rows = []

    for domain, item in lead_map.items():
        email_rows = hunter_domain_search(domain)
        combined_text = f"{item.get('company_title', '')} {item.get('snippet', '')} {item.get('search_query', '')}"
        buyer_type = guess_buyer_type(combined_text)

        for email_row in email_rows:
            all_rows.append(
                {
                    "product": product,
                    "country": country_name,
                    "source": item.get("source", ""),
                    "search_query": item.get("search_query", ""),
                    "company_title": item.get("company_title", ""),
                    "buyer_type": buyer_type,
                    "domain": domain,
                    "url": item.get("url", ""),
                    "email": email_row.get("email", ""),
                    "confidence": email_row.get("confidence", ""),
                    "email_type": email_row.get("type", ""),
                    "first_name": email_row.get("first_name", ""),
                    "last_name": email_row.get("last_name", ""),
                    "position": email_row.get("position", ""),
                    "department": email_row.get("department", ""),
                    "phone": item.get("phone", ""),
                    "snippet": item.get("snippet", ""),
                    "send_status": "",
                    "sent_date": "",
                    "reply_status": "",
                    "follow_up_date": "",
                    "unsubscribe": "",
                    "error": email_row.get("error", ""),
                }
            )

        time.sleep(0.4)

    filename = make_excel(all_rows, "unitra_buyer_results")

    return {
        "status": "success",
        "product": product,
        "country": country_name,
        "keywords_used": keywords,
        "unique_domains": len(lead_map),
        "rows": len(all_rows),
        "download_url": f"{PUBLIC_BASE_URL}/download/{filename}",
        "preview": all_rows[:20],
    }


@app.post("/search-and-extract-emails")
def search_and_extract_emails(request: SearchEmailRequest):
    google_response = serpapi_google_search(
        query=request.query,
        pages=request.pages,
        country=request.country,
        language=request.language,
    )

    if google_response.get("status") != "success":
        return google_response

    lead_map = {}

    for item in google_response.get("results", []):
        domain = item.get("domain")
        if domain and domain not in lead_map:
            lead_map[domain] = item

    all_rows = []

    for domain, item in lead_map.items():
        email_rows = hunter_domain_search(domain)

        for email_row in email_rows:
            all_rows.append(
                {
                    "search_query": request.query,
                    "source": item.get("source", ""),
                    "company_title": item.get("company_title", ""),
                    "domain": domain,
                    "url": item.get("url", ""),
                    "email": email_row.get("email", ""),
                    "confidence": email_row.get("confidence", ""),
                    "position": email_row.get("position", ""),
                    "department": email_row.get("department", ""),
                    "snippet": item.get("snippet", ""),
                    "error": email_row.get("error", ""),
                }
            )

        time.sleep(0.4)

    filename = make_excel(all_rows, "buyer_email_results")

    return {
        "status": "success",
        "query": request.query,
        "searched_domains": len(lead_map),
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

    filename = make_excel(all_rows, "domain_email_results")

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


@app.get("/openapi.yaml")
async def get_openapi_yaml():
    return FileResponse("openapi.yaml", media_type="application/yaml")
