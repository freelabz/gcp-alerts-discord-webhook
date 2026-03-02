from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
from datetime import datetime
from pydantic import BaseModel
from typing import Optional
import logging

app = FastAPI()

# Configure CORS if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Security (optional - if you configured basic auth in the notification channel)
security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = os.getenv("BASIC_AUTH_USERNAME", "secator")
    correct_password = os.getenv("BASIC_AUTH_PASSWORD", "secator")

    if not (correct_username and correct_password):
        return  # Skip auth if not configured

    if credentials.username != correct_username or credentials.password != correct_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials

class AlertPayload(BaseModel):
    version: Optional[str] = None
    incident: dict

@app.post("/api/discord_webhook")
async def forward_to_discord(
    request: Request,
    payload: AlertPayload,
    credentials: HTTPBasicCredentials = Depends(verify_credentials)
):
    discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not discord_webhook_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Discord webhook URL not configured"
        )

    try:
        incident = payload.incident
        resource = incident.get("resource", {})
        resource_labels = resource.get("labels", {})
        condition = incident.get("condition", {})

        policy_name = incident.get("policy_name", "Unknown Policy")
        condition_name = incident.get("condition_name", "Unknown")

        # started_at is Unix epoch seconds in the real GCP payload
        started_at = incident.get("started_at")
        if isinstance(started_at, (int, float)):
            started_str = datetime.utcfromtimestamp(started_at).strftime("%Y-%m-%d %H:%M:%S UTC")
        elif isinstance(started_at, str):
            started_str = datetime.fromisoformat(started_at.replace('Z', '+00:00')).strftime("%Y-%m-%d %H:%M:%S %Z")
        else:
            started_str = "N/A"

        state = incident.get("state", "unknown")
        color = 0xFF0000 if state == "open" else 0x00FF00

        embed = {
            "title": f"🚨 GCP Alert: {policy_name}",
            "url": incident.get("url", ""),
            "color": color,
            "fields": [
                {"name": "Cluster", "value": resource_labels.get("cluster_name", "N/A"), "inline": True},
                {"name": "Location", "value": resource_labels.get("location", "N/A"), "inline": True},
                {"name": "Condition", "value": condition_name, "inline": True},
                {"name": "Started At", "value": started_str},
                {"name": "Summary", "value": incident.get("summary", "No summary provided")},
                {"name": "Severity", "value": "⚠️ OPEN" if state == "open" else "✅ CLOSED", "inline": True},
            ],
            "footer": {"text": "GCP Monitoring Alert"},
            "timestamp": datetime.now().isoformat()
        }

        message = {
            "embeds": [embed],
            "content": f"New GCP alert: {policy_name}"
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(discord_webhook_url, json=message)
            response.raise_for_status()

        logger.info(f"Successfully forwarded alert to Discord: {incident.get('url', '')}")
        return {"status": "success", "message": "Alert forwarded to Discord"}

    except Exception as e:
        logger.error(f"Error forwarding alert to Discord: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error forwarding alert: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
