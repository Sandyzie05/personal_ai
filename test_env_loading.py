#!/usr/bin/env python3
"""
Test that environment variables are properly loaded.
"""

import os
from dotenv import load_dotenv

# Load .env file explicitly
load_dotenv('.env')

print("Environment variable test:")
print(f"omlx_api_key in os.environ: {'omlx_api_key' in os.environ}")
if 'omlx_api_key' in os.environ:
    print(f"omlx_api_key value: {os.environ['omlx_api_key'][:10]}...")

# Now test our OMLX client approach
import urllib.request
import urllib.error
import json
from urllib.parse import urljoin

OMLX_HOST = "http://127.0.0.1:8000"
API_KEY = os.environ.get("omlx_api_key") or os.environ.get("OMLX_API_KEY") or ""

print(f"\nUsing API key from env: {bool(API_KEY)}")
if API_KEY:
    print(f"API key (first 10 chars): {API_KEY[:10]}...")

def test_with_auth():
    url = urljoin(OMLX_HOST, "/v1/models")
    headers = {"Content-Type": "application/json"}

    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
        print("Authorization header added")
    else:
        print("No authorization header")

    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        print(f"Making request to: {url}")
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            print("✓ SUCCESS!")
            print(f"Status code: {response.getcode()}")
            if "data" in data:
                print(f"Found {len(data['data'])} models")
            return True
    except urllib.error.HTTPError as e:
        print(f"✗ HTTP Error {e.code}: {e.reason}")
        if e.code == 401:
            print("This is the expected authentication error - API key is being sent")
        return False
    except Exception as e:
        print(f"✗ Other error: {str(e)}")
        return False

if __name__ == "__main__":
    test_with_auth()