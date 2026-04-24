from dotenv import load_dotenv
import os
import urllib.request
import json

load_dotenv()

headers = {
    "x-api-key": os.getenv("CLAUDE_API_KEY"),
    "anthropic-version": "2023-06-01",
}

req = urllib.request.Request(
    "https://api.anthropic.com/v1/models",
    headers=headers
)

with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print(json.dumps(data, indent=2, ensure_ascii=False))