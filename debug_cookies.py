import os
from dotenv import load_dotenv

load_dotenv(override=True)

print("--- DEBUGGING ENV START ---")

# Check DISCORD_TOKEN
token = os.getenv("DISCORD_TOKEN")
print(f"DEBUG: DISCORD_TOKEN: {'PRESENT' if token else 'MISSING'}")

# Check COOKIES_CONTENT
cookies = os.getenv("COOKIES_CONTENT")
print(f"DEBUG: COOKIES_CONTENT: {'PRESENT' if cookies else 'MISSING'}")

# Check .env existence
if os.path.exists(".env"):
    print("DEBUG: .env file FOUND.")
else:
    print("DEBUG: .env file NOT FOUND.")

# Check for 'cookies.txt' file specificially
if os.path.exists("cookies.txt"):
    print(f"DEBUG: 'cookies.txt' FILE FOUND on disk. Size: {os.path.getsize('cookies.txt')} bytes.")
else:
    print("DEBUG: 'cookies.txt' FILE NOT FOUND on disk.")

# Check for weird env var name 'cookies.txt'
weird_env = os.getenv("cookies.txt")
print(f"DEBUG: Env Var 'cookies.txt': {'PRESENT' if weird_env else 'MISSING'}")

print("--- DEBUGGING ENV END ---")
