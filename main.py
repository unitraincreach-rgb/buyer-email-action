from fastapi import FastAPI
from pydantic import BaseModel
import requests
import pandas as pd
import uuid

app = FastAPI()

HUNTER_API_KEY = "여기에_HUNTER_API_KEY"


class EmailRequest(BaseModel):
    domains: list[str]


@app.get("/")
def home():
    return {"status": "running"}


@app.post("/extract-emails")
def extract_emails(request: EmailRequest):

    results = []

    for domain in request.domains:

        domain = domain.replace("https://", "")
        domain = domain.replace("http://", "")
        domain = domain.replace("www.", "")
        domain = domain.split("/")[0]

        url = "https://api.hunter.io/v2/domain-search"

        params = {
            "domain": domain,
            "api_key": HUNTER_API_KEY
        }

        try:

            response = requests.get(url, params=params)

            data = response.json()

            emails = data.get("data", {}).get("emails", [])

            if not emails:

                results.append({
                    "domain": domain,
                    "email": "이메일 없음"
                })

            for item in emails:

                results.append({
                    "domain": domain,
                    "email": item.get("value"),
                    "confidence": item.get("confidence")
                })

        except Exception as e:

            results.append({
                "domain": domain,
                "email": "",
                "error": str(e)
            })

    filename = f"emails_{uuid.uuid4().hex[:8]}.xlsx"

    df = pd.DataFrame(results)

    df.to_excel(filename, index=False)

    return {
        "status": "success",
        "file": filename,
        "results": results
    }
