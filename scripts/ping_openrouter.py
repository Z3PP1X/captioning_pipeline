"""Ping OpenRouter API to verify authentication and connectivity."""
import json
import os
import urllib.request
from pathlib import Path

def ping():
    # 1. Resolve API Key from .env, env var, or user input
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not found in environment or .env file.")
        return False

    masked = f"{api_key[:10]}...{api_key[-4:]}"
    print(f"Checking OpenRouter with key: {masked}")

    # 2. Check /api/v1/auth/key
    url = "https://openrouter.ai/api/v1/auth/key"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Captioning Pipeline",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            print("========================================")
            print("OPENROUTER PING SUCCESSFUL (HTTP 200)")
            print("========================================")
            key_data = data.get("data", {})
            print(f"Key Label:       {key_data.get('label')}")
            print(f"Usage:           ${key_data.get('usage', 0):.4f}")
            print(f"Limit:           ${key_data.get('limit') if key_data.get('limit') is not None else 'No limit'}")
            print(f"Is Free Tier:    {key_data.get('is_free_tier')}")
            print(f"Rate Limit RPM:  {key_data.get('rate_limit', {}).get('requests', 'N/A')}")
            
            # 3. Test lightweight model completion with google/gemma-4-31b-it
            print("\nTesting model completion with google/gemma-4-31b-it...")
            chat_url = "https://openrouter.ai/api/v1/chat/completions"
            test_payload = json.dumps({
                "model": "google/gemma-4-31b-it",
                "messages": [{"role": "user", "content": "Respond with the single word: READY"}],
                "max_tokens": 10,
                "temperature": 0.1,
            }).encode("utf-8")
            chat_req = urllib.request.Request(
                chat_url,
                data=test_payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:8000",
                    "X-Title": "Captioning Pipeline",
                },
            )
            with urllib.request.urlopen(chat_req, timeout=30) as chat_resp:
                chat_data = json.loads(chat_resp.read().decode())
                reply = chat_data["choices"][0]["message"]["content"].strip()
                print("========================================")
                print(f"MODEL TEST SUCCESS: {reply}")
                print("========================================")
            return True
    except urllib.error.HTTPError as err:
        err_body = err.read().decode()
        print("========================================")
        print(f"OPENROUTER PING FAILED (HTTP {err.code})")
        print("========================================")
        print(f"Error details: {err_body}")
        return False
    except Exception as e:
        print(f"Network error connecting to OpenRouter: {e}")
        return False

if __name__ == "__main__":
    ping()
