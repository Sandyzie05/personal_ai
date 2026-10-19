#!/usr/bin/env python3
"""
Test OMLX connection directly without importing the full module structure.
"""

import os
import urllib.request
import urllib.error
import json
from urllib.parse import urljoin

# Configure the OMLX client settings
OMLX_HOST = "http://127.0.0.1:8000"
OMLX_MODEL = "Qwen3.8-27B-4bit"
API_KEY = os.environ.get("omlx_api_key") or os.environ.get("OMLX_API_KEY") or ""

print(f"Testing OMLX connection with:")
print(f"Host: {OMLX_HOST}")
print(f"Model: {OMLX_MODEL}")
print(f"API Key available: {bool(API_KEY)}")

def test_connection():
    """Test connection to OMLX server"""
    url = urljoin(OMLX_HOST, "/v1/models")
    headers = {"Content-Type": "application/json"}

    # Add API key if available
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
        print("Using API key for authentication")
    else:
        print("No API key found - may require authentication")

    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        print(f"Making request to: {url}")
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            print("✓ Connection successful!")
            print(f"Status code: {response.getcode()}")
            if "data" in data:
                print(f"Found {len(data['data'])} models")
                for model in data['data'][:3]:
                    print(f"  - {model.get('id', 'unknown')}")
            return True
    except urllib.error.HTTPError as e:
        print(f"✗ HTTP Error {e.code}: {e.reason}")
        if e.code == 401:
            print("Authentication required - API key may be incorrect or missing")
        elif e.code == 403:
            print("Forbidden - API key may be invalid")
        return False
    except urllib.error.URLError as e:
        print(f"✗ Connection Error: {str(e)}")
        print("This could be:")
        print("  - OMLX server not running")
        print("  - Wrong host/port (127.0.0.1:8000)")
        print("  - Firewall blocking the connection")
        return False
    except Exception as e:
        print(f"✗ Unexpected error: {str(e)}")
        return False

if __name__ == "__main__":
    success = test_connection()
    print(f"\nTest result: {'PASS' if success else 'FAIL'}")