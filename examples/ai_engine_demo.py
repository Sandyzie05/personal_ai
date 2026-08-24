"""
Example usage of the AI Engine module.

Uses AuthManager (not a raw key file) to get the vault's encryption key, the
same way the real Streamlit app does after login - so this demo exercises
the actual security path instead of bypassing it.
"""

from src.ai_engine.chat_engine import ChatEngine
from src.security.auth import AuthManager
from src.config import DEFAULT_CHAT_MODEL, DEFAULT_EMBED_MODEL


def main():
    """Demonstrate AI Engine functionality."""

    print("Personal AI Engine - Quick Start Demo")
    print("=" * 50)

    try:
        auth = AuthManager()
        if auth.is_registered():
            print("Enter your existing vault password:")
            encryption_key = auth.authenticate()
        else:
            print("No vault yet - create a password for this demo vault:")
            encryption_key = auth.register()

        chat_engine = ChatEngine(encryption_key=encryption_key)

        print("\n1. Adding sample data to vault...")
        chat_engine.add_data_to_vault(
            "sample_note",
            {
                "title": "My First Note",
                "content": "This is a test note stored in the encrypted vault.",
                "tags": ["test", "demo"],
                "priority": "high",
            },
        )
        print("✓ Data added successfully")

        print("\n2. Initializing RAG engine with vault data...")
        chat_engine.initialize_rag()
        print("✓ RAG engine ready")

        print("\n3. Querying vault...")
        result = chat_engine.query_vault("What is in my vault?")
        print(f"Query: {result['query']}")
        print(f"Response: {result['response']}")

        print("\n4. Adding more data...")
        chat_engine.add_data_to_vault(
            "meeting_notes",
            {
                "title": "Team Meeting",
                "date": "2024-01-15",
                "attendees": ["Alice", "Bob", "Charlie"],
                "topics": ["Project update", "Timeline review"],
                "decisions": ["Proceed with Phase 1", "Review by Friday"],
            },
        )
        print("✓ More data added")

        print("\n5. Querying with context...")
        result = chat_engine.query_vault("What decisions were made in the meeting?")
        print(f"Response: {result['response']}")

        print("\n" + "=" * 50)
        print("Demo complete!")

    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        print("\nTroubleshooting:")
        print("- Ensure Ollama is running: ollama serve")
        print("- Pull the required model: ollama pull " + DEFAULT_CHAT_MODEL)
        print("- Pull embeddings model: ollama pull " + DEFAULT_EMBED_MODEL)


if __name__ == "__main__":
    main()
