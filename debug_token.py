
import os
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("DISCORD_TOKEN")

if token:
    print(f"Token loaded (length: {len(token)})")
    print(f"Start: {token[:5]}...")
    print(f"End: ...{token[-5:]}")
    
    # Check for common issues
    if " " in token:
        print("WARNING: Token contains spaces!")
    if "\n" in token:
        print("WARNING: Token contains newlines!")
    if token.startswith('"') or token.startswith("'"):
        print("WARNING: Token starts with quotes (python-dotenv usually strips these but double check .env)")
else:
    print("ERROR: Token not found in environment variables.")

print(f"Current working directory: {os.getcwd()}")
print(f"Contents of .env file (first line stripped):")
try:
    with open(".env", "r") as f:
        content = f.readline().strip()
        print(f"'{content}'")
except Exception as e:
    print(f"Could not read .env: {e}")
