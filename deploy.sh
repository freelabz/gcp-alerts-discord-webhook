gcloud run deploy discord-webhook-forwarder \
  --source . \
  --set-env-vars DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1407654919309230131/Vo5N9B7sfceSsQ-MYQWNGYiSw1PkZWNAv5ysjPidXo1dfNcpZH2j9yn3_XD3AxbXcBo4 \
  --set-env-vars BASIC_AUTH_USERNAME=secator \
  --set-env-vars BASIC_AUTH_PASSWORD=secator \
  --allow-unauthenticated
