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
    incident: dict
    alert_policy: dict
    condition: dict
    resource: Optional[dict] = None

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
        # Extract data from the alert
        incident = payload.incident
        policy = payload.alert_policy
        condition = payload.condition

        # Create Discord message embed
        embed = {
            "title": f"🚨 GKE Alert: {policy.get('display_name', 'Unknown Policy')}",
            "url": incident.get("url", ""),
            "color": 0xFF0000,  # Red
            "fields": [
                {"name": "Cluster", "value": incident.get("labels", {}).get("cluster_name", "N/A"), "inline": True},
                {"name": "Location", "value": incident.get("labels", {}).get("location", "N/A"), "inline": True},
                {"name": "Condition", "value": condition.get("display_name", "Unknown"), "inline": True},
                {"name": "Started At", "value": datetime.fromisoformat(incident["started_at"].replace('Z', '+00:00')).strftime("%Y-%m-%d %H:%M:%S %Z")},
                {"name": "Summary", "value": incident.get("summary", "No summary provided")},
                {"name": "Severity", "value": "⚠️ OPEN" if incident.get("state") == "open" else "✅ CLOSED", "inline": True},
            ],
            "footer": {"text": "GKE Monitoring Alert"},
            "timestamp": datetime.now().isoformat()
        }

        message = {
            "embeds": [embed],
            "content": f"New GKE alert: {policy.get('display_name', 'Unknown Policy')}"
        }

        # Send to Discord
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
