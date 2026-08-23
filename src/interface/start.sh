#!/usr/bin/env python3
"""Start the Personal AI Web Interface."""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.interface.main import app


def main():
    """Run the Streamlit interface."""
    print("🚀 Starting Personal AI Interface...")
    print("📱 Open http://localhost:8501 in your browser")
    print("🔒 All data is encrypted and stored locally")
    print()
    
    app.run()


if __name__ == "__main__":
    main()
