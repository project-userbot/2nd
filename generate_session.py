from telethon.sync import TelegramClient
from telethon.sessions import StringSession
import os
from dotenv import load_dotenv

load_dotenv()

# Get your API credentials from environment variables
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
PHONE_NUMBER = os.getenv('PHONE_NUMBER')

# Create a new session
with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    # Login
    client.start(phone=PHONE_NUMBER)
    
    # Print the session string
    print("\n\nYOUR SESSION STRING IS:\n")
    print(client.session.save())
    print("\n\nKeep this string safe and add it to your environment variables in Koyeb as SESSION_STRING") 