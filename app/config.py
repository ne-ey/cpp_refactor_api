import os
from dotenv import load_dotenv

load_dotenv()  # .env file load karega

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_API_URL = os.getenv("OPENROUTER_API_URL")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL")

if not OPENROUTER_API_KEY:
    raise ValueError("⚠️ OPENROUTER_API_KEY missing in .env")
