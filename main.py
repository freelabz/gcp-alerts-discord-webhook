from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
from datetime import datetime
from pydantic import BaseModel
from typing import Optional
from urllib.parse import urlparse
import logging

from google.cloud import storage

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


# --- GCS helpers ---

def _get_gcs_bucket():
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    if not bucket_name:
        return None
    client = storage.Client()
    return client.bucket(bucket_name)


def save_message_id(incident_id: str, message_id: str):
    bucket = _get_gcs_bucket()
    if not bucket:
        logger.warning("GCS_BUCKET_NAME not set, skipping message_id persistence")
        return
    blob = bucket.blob(f"incidents/{incident_id}")
    blob.upload_from_string(message_id)
    logger.info(f"Saved message_id {message_id} for incident {incident_id}")


def get_message_id(incident_id: str) -> Optional[str]:
    bucket = _get_gcs_bucket()
    if not bucket:
        return None
    blob = bucket.blob(f"incidents/{incident_id}")
    if not blob.exists():
        return None
    return blob.download_as_text()


def delete_message_id(incident_id: str):
    bucket = _get_gcs_bucket()
    if not bucket:
        return
    blob = bucket.blob(f"incidents/{incident_id}")
    if blob.exists():
        blob.delete()
        logger.info(f"Deleted message_id blob for incident {incident_id}")


# --- Discord helpers ---

def parse_webhook_ids(webhook_url: str) -> tuple[str, str]:
    """Extract webhook_id and webhook_token from a Discord webhook URL."""
    path = urlparse(webhook_url).path
    parts = path.rstrip("/").split("/")
    # URL format: https://discord.com/api/webhooks/{id}/{token}
    return parts[-2], parts[-1]


def build_embed(incident: dict) -> dict:
    resource = incident.get("resource", {})
    resource_labels = resource.get("labels", {})

    policy_name = incident.get("policy_name", "Unknown Policy")
    condition_name = incident.get("condition_name", "Unknown")

    started_at = incident.get("started_at")
    if isinstance(started_at, (int, float)):
        started_str = datetime.utcfromtimestamp(started_at).strftime("%Y-%m-%d %H:%M:%S UTC")
    elif isinstance(started_at, str):
        started_str = datetime.fromisoformat(started_at.replace('Z', '+00:00')).strftime("%Y-%m-%d %H:%M:%S %Z")
    else:
        started_str = "N/A"

    state = incident.get("state", "unknown")
    color = 0xFF0000 if state == "open" else 0x00FF00

    fields = [
        {"name": "Cluster", "value": resource_labels.get("cluster_name", "N/A"), "inline": True},
        {"name": "Location", "value": resource_labels.get("location", "N/A"), "inline": True},
        {"name": "Condition", "value": condition_name, "inline": True},
        {"name": "Started At", "value": started_str},
        {"name": "Summary", "value": incident.get("summary", "No summary provided")},
        {"name": "Severity", "value": "OPEN" if state == "open" else "CLOSED", "inline": True},
    ]

    if state == "closed":
        ended_at = incident.get("ended_at")
        if isinstance(ended_at, (int, float)):
            ended_str = datetime.utcfromtimestamp(ended_at).strftime("%Y-%m-%d %H:%M:%S UTC")
        elif isinstance(ended_at, str):
            ended_str = datetime.fromisoformat(ended_at.replace('Z', '+00:00')).strftime("%Y-%m-%d %H:%M:%S %Z")
        else:
            ended_str = "N/A"
        fields.append({"name": "Ended At", "value": ended_str})

    title = f"\u26a0\ufe0f GCP Alert: {policy_name}" if state == "open" else f"\u2705 GCP Alert Resolved: {policy_name}"

    return {
        "title": title,
        "url": incident.get("url", ""),
        "color": color,
        "fields": fields,
        "footer": {"text": "GCP Monitoring Alert"},
        "timestamp": datetime.now().isoformat()
    }


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
        incident_id = incident.get("incident_id", "")
        state = incident.get("state", "unknown")
        policy_name = incident.get("policy_name", "Unknown Policy")

        embed = build_embed(incident)
        message = {
            "embeds": [embed]
        }
        # Page: mention the on-call role on newly opened CRITICAL incidents only.
        # WARNING/ERROR (tickets) and resolutions post without a mention.
        oncall_role_id = os.getenv("DISCORD_ONCALL_ROLE_ID")
        if oncall_role_id and state == "open" and str(incident.get("severity", "")).lower() == "critical":
            message["content"] = f"<@&{oncall_role_id}>"
            message["allowed_mentions"] = {"roles": [oncall_role_id]}

        async with httpx.AsyncClient() as client:
            if state == "closed" and incident_id:
                # Try to edit the original message
                existing_message_id = get_message_id(incident_id)
                if existing_message_id:
                    webhook_id, webhook_token = parse_webhook_ids(discord_webhook_url)
                    patch_url = f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}/messages/{existing_message_id}"
                    response = await client.patch(patch_url, json=message)
                    response.raise_for_status()
                    delete_message_id(incident_id)
                    logger.info(f"Updated Discord message {existing_message_id} for closed incident {incident_id}")
                else:
                    # Fallback: no stored message_id, post new message
                    logger.warning(f"No stored message_id for incident {incident_id}, posting new message")
                    response = await client.post(discord_webhook_url, json=message)
                    response.raise_for_status()
            else:
                # Open alert: post new message with ?wait=true to get message_id back
                post_url = f"{discord_webhook_url}?wait=true"
                response = await client.post(post_url, json=message)
                response.raise_for_status()

                if incident_id:
                    discord_message = response.json()
                    message_id = discord_message.get("id")
                    if message_id:
                        save_message_id(incident_id, message_id)

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
