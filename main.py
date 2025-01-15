import asyncio
import logging
from telethon import TelegramClient, events
from telethon.tl.types import User, Channel, Dialog
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty
from ai_handler import GeminiHandler as AIHandler
from db_handler import DatabaseHandler
import os
from dotenv import load_dotenv
import time
import random
from aiohttp import web
import threading
from telethon.sessions import StringSession

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
PHONE_NUMBER = os.getenv('PHONE_NUMBER')
PORT = int(os.getenv('PORT', 8080))
SESSION_STRING = os.getenv('SESSION_STRING')  # New environment variable for session string

# Get default group ID from environment variables
DEFAULT_GROUP_ID = int(os.getenv('DEFAULT_GROUP_ID', '-4666305725'))

async def health_check(request):
    """Health check endpoint for Koyeb"""
    return web.Response(text="OK", status=200)

async def start_health_server():
    """Start the health check server"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Health check server started on port {PORT}")

class UserBot:
    def __init__(self):
        self.client = TelegramClient(
            StringSession(SESSION_STRING),  # Use session string instead of file
            API_ID, 
            API_HASH
        )
        self.ai_handler = AIHandler()
        self.db_handler = DatabaseHandler()
        self.selected_group_id = DEFAULT_GROUP_ID
        self.last_message_time = {}
        self.message_count = {}
        self.admin_id = 7608205234  # Add admin ID
        self.commands = {
            '/help': 'Show all available commands',
            '/refresh': 'Refresh group selection',
            '/status': 'Show current selected group',
            '/stop': 'Stop responding in current group',
            '/start': 'Start responding in current group',
            '/context': 'Show or change current context (Usage: /context [context_name])',
            '/contexts': 'List all available contexts',
            '/addcontext': 'Add a new context (Usage: /addcontext name|context_text)',
            '/resetcontext': 'Reset chat with current context',
            '/setgroup': 'Set a new group ID (Usage: /setgroup -123456789)'
        }
        self.is_responding = True
        self.app = web.Application()
        self.app.router.add_get("/health", self.health_check)
        self.runner = web.AppRunner(self.app)

    async def health_check(self, request):
        """Health check endpoint for Koyeb"""
        return web.Response(text="OK", status=200)

    async def get_groups(self):
        """Get all groups the user is part of"""
        groups = []
        async for dialog in self.client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                groups.append(dialog)
        return groups

    async def display_groups(self):
        """Display all groups with numbers"""
        groups = await self.get_groups()
        print("\nAvailable Groups:")
        print("-" * 50)
        for i, group in enumerate(groups, 1):
            print(f"{i}. {group.name} (ID: {group.id})")
        print("-" * 50)
        
        while True:
            try:
                choice = input("\nEnter the number of the group you want to monitor (0 to exit): ")
                if choice == "0":
                    return None
                choice = int(choice)
                if 1 <= choice <= len(groups):
                    selected_group = groups[choice-1]
                    print(f"\nSelected: {selected_group.name}")
                    return selected_group.id
                else:
                    print("Invalid choice. Please try again.")
            except ValueError:
                print("Please enter a valid number.")

    async def show_help(self, event):
        """Display all available commands"""
        help_text = "**Available Commands:**\n\n"
        for cmd, desc in self.commands.items():
            help_text += f"{cmd} - {desc}\n"
        await event.reply(help_text)

    async def refresh_selection(self):
        """Refresh group selection"""
        new_group_id = await self.display_groups()
        if new_group_id:
            self.selected_group_id = new_group_id
            return f"Now monitoring group with ID: {self.selected_group_id}"
        return "Group selection cancelled"

    async def start(self):
        """Initialize and start the userbot"""
        logger.info("Starting userbot initialization...")
        
        # Start health check server
        await start_health_server()
        
        # Start the client and handle login
        await self.client.start(phone=PHONE_NUMBER)
        
        # Check if we're logged in
        if not await self.client.is_user_authorized():
            logger.error("Not logged in. Please run the bot once with proper authentication.")
            return
        
        logger.info("Successfully logged in!")
        logger.info(f"Using default group ID: {self.selected_group_id}")

        # Register command handler
        @self.client.on(events.NewMessage(pattern=r'/\w+'))
        async def handle_commands(event):
            if event.message.from_id != await self.client.get_me():
                return

            command = event.message.text.split()[0].lower()
            args = event.message.text.split()[1:] if len(event.message.text.split()) > 1 else []

            if command == '/help':
                await self.show_help(event)
            elif command == '/status':
                await event.reply(f"Currently monitoring group ID: {self.selected_group_id}")
            elif command == '/setgroup':
                if args:
                    try:
                        new_group_id = int(args[0])
                        self.selected_group_id = new_group_id
                        await event.reply(f"Now monitoring group ID: {self.selected_group_id}")
                    except ValueError:
                        await event.reply("Please provide a valid group ID")
                else:
                    await event.reply("Usage: /setgroup -123456789")
            elif command == '/stop':
                self.is_responding = False
                await event.reply("Stopped responding in current group")
            elif command == '/start':
                self.is_responding = True
                await event.reply("Started responding in current group")
            elif command == '/refresh':
                await self.refresh_group_selection(event)
            elif command == '/context':
                await self.show_or_change_context(event, args)
            elif command == '/contexts':
                await self.list_all_contexts(event)
            elif command == '/addcontext':
                await self.add_new_context(event, args)
            elif command == '/resetcontext':
                await self.reset_chat_with_context(event)
            else:
                await event.reply("Unknown command. Type /help for a list of available commands.")

        # Message handler
        @self.client.on(events.NewMessage)
        async def handle_messages(event):
            try:
                # Get message sender
                sender = await event.get_sender()
                
                message_text = event.message.text
                if not message_text:
                    return

                # Handle commands only from admin
                if message_text.startswith('/'):
                    if event.sender_id != self.admin_id:
                        await event.reply("Only admin can use commands")
                        return

                    # Check for command messages
                    command = message_text.split()[0].lower()
                    args = message_text.split()[1:] if len(message_text.split()) > 1 else []

                    if command == '/help':
                        await self.show_help(event)
                    elif command == '/status':
                        await event.reply(f"Currently monitoring group ID: {self.selected_group_id}")
                    elif command == '/stop':
                        self.is_responding = False
                        await event.reply("Stopped responding in current group")
                    elif command == '/start':
                        self.is_responding = True
                        await event.reply("Started responding in current group")
                    elif command == '/setgroup':
                        if args:
                            try:
                                new_group_id = int(args[0])
                                self.selected_group_id = new_group_id
                                await event.reply(f"Now monitoring group ID: {self.selected_group_id}")
                            except ValueError:
                                await event.reply("Please provide a valid group ID")
                        else:
                            await event.reply("Usage: /setgroup -123456789")
                    elif command == '/context':
                        if not args:
                            current_context = self.ai_handler.context_manager.get_current_context()
                            await event.reply(f"Current context:\n{current_context}")
                        else:
                            context_name = args[0].lower()
                            if self.ai_handler.context_manager.set_context(context_name):
                                await event.reply(f"Context changed to: {context_name}")
                            else:
                                await event.reply(f"Context '{context_name}' not found")
                    elif command == '/contexts':
                        contexts = self.ai_handler.context_manager.list_contexts()
                        await event.reply("Available contexts:\n" + "\n".join(f"- {ctx}" for ctx in contexts))
                    elif command == '/resetcontext':
                        self.ai_handler.reset_chat()
                        await event.reply("Chat reset with current context")
                    return  # Exit after handling command

                # Only respond in selected group and if responding is enabled
                if event.chat_id != self.selected_group_id:
                    return

                if not self.is_responding:
                    return

                # Get AI response
                response_data = await self.ai_handler.get_response(message_text, event.chat_id, event.sender_id)
                
                if response_data:
                    # Clean up formatting from response while preserving content
                    response_text = response_data['text']
                    response_text = response_text.replace('****', '')  # Remove asterisks
                    response_text = response_text.replace('***', '')
                    response_text = response_text.replace('**', '')
                    response_text = response_text.replace('*', '')
                    response_text = response_text.strip()
                    
                    logger.info(f"AI response: {response_text}")
                    
                    # Initial human-like delay
                    await asyncio.sleep(response_data['initial_delay'])
                    
                    # Simulate typing
                    async with self.client.action(event.chat_id, 'typing'):
                        await asyncio.sleep(response_data['typing_duration'])
                        
                        # Send the response
                        await event.reply(response_text)
                        logger.info("Reply sent successfully")
                else:
                    logger.debug("No response generated")

            except Exception as e:
                logger.error(f"Error processing message: {str(e)}")
                logger.exception("Full exception:")

        logger.info("Userbot started successfully!")
        logger.info("Available commands:")
        for cmd, desc in self.commands.items():
            logger.info(f"{cmd} - {desc}")
        
        # Keep the bot running
        await self.client.run_until_disconnected()

    async def handle_context_commands(self, event, command, args):
        """Handle context-related commands"""
        if command == '/contexts':
            contexts = self.ai_handler.context_manager.list_contexts()
            await event.reply("Available contexts:\n" + "\n".join(f"- {ctx}" for ctx in contexts))
            return True

        elif command == '/context':
            if not args:
                current_context = self.ai_handler.context_manager.get_current_context()
                await event.reply(f"Current context:\n{current_context}")
            else:
                context_name = args[0].lower()
                if self.ai_handler.set_context(context_name):
                    await event.reply(f"Context changed to: {context_name}")
                else:
                    await event.reply(f"Context '{context_name}' not found. Use /contexts to see available contexts.")
            return True

        elif command == '/addcontext':
            if not args or '|' not in event.message.text:
                await event.reply("Usage: /addcontext name|context_text")
                return True
            
            name, context = event.message.text.split('|', 1)[1].split('|', 1)
            self.ai_handler.add_custom_context(name.strip(), context.strip())
            await event.reply(f"Context '{name.strip()}' added.")
            return True

        elif command == '/resetcontext':
            self.ai_handler.reset_chat()
            await event.reply("Chat reset with current context.")
            return True

        return False  # Not a context command

if __name__ == '__main__':
    userbot = UserBot()
    asyncio.run(userbot.start())
