"""Check a Gemini API key locally without saving or displaying it."""

from getpass import getpass

from backend.learning_engine import check_api_key


def main() -> int:
    api_key = getpass("Paste your Gemini API key (input hidden): ").strip()
    if not api_key:
        print("No key entered.")
        return 1

    try:
        model_name = check_api_key(api_key)
    except Exception as exc:
        error = str(exc).upper()
        if "401" in error or "API_KEY_INVALID" in error or "UNAUTHENTICATED" in error:
            message = "Google rejected the key. Check that it was copied correctly and is still active."
        elif "403" in error or "PERMISSION_DENIED" in error:
            message = "Google received the key, but this project does not have API access."
        elif "429" in error or "RESOURCE_EXHAUSTED" in error:
            message = "Google received the key, but a request or quota limit was reached."
        elif "503" in error or "UNAVAILABLE" in error:
            message = "Google's model service is temporarily unavailable. Try again later."
        else:
            message = "The check failed. Verify the key and network, then retry."
        print(message)
        return 1

    if not model_name:
        print("Google returned no text-generation models for this key.")
        return 1

    print("Google recognized the key and listed a text-generation model.")
    print(f"Example text-generation model: {model_name}")
    print("Temporary capacity errors can still occur while generating content.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
