
import os
from dotenv import load_dotenv

print(f"Token BEFORE load_dotenv: {os.environ.get('DISCORD_TOKEN', 'None')}")

load_dotenv()
print(f"Token AFTER load_dotenv (default): {os.environ.get('DISCORD_TOKEN', 'None')}")

load_dotenv(override=True)
print(f"Token AFTER load_dotenv (override=True): {os.environ.get('DISCORD_TOKEN', 'None')}")
