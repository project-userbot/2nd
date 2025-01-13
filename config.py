import os
from dotenv import load_dotenv

load_dotenv()

# Get these values from https://my.telegram.org/apps
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')

# Session file name
SESSION_NAME = 'my_telegram_session'

# Message patterns and responses
RESPONSES = {
    'hello': 'Hi there! 👋',
    'help': 'How can I assist you?',
    'thanks': "You're welcome! 😊"
}