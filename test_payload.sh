curl -X POST \
  -H "Content-Type: application/json" \
  -u "secator:secator" \
  -d @test_payload.json \
  http://localhost:8000/api/discord_webhook
