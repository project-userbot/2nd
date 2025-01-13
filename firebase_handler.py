import firebase_admin
from firebase_admin import credentials, firestore
import logging
from datetime import datetime
import os
from dotenv import load_dotenv
import json

load_dotenv()

class FirebaseHandler:
    def __init__(self):
        try:
            # Check if already initialized
            firebase_admin.get_app()
        except ValueError:
            try:
                # Check for service account JSON in environment variable
                service_account_json = os.getenv('FIREBASE_SERVICE_ACCOUNT')
                if service_account_json:
                    try:
                        # Parse the JSON string from environment variable
                        cred_dict = json.loads(service_account_json)
                        cred = credentials.Certificate(cred_dict)
                        firebase_admin.initialize_app(cred)
                        logging.info("Firebase initialized successfully with service account from environment")
                    except json.JSONDecodeError as je:
                        logging.error(f"Invalid JSON in FIREBASE_SERVICE_ACCOUNT: {je}")
                        raise
                else:
                    # Fallback to file-based credentials for local development
                    cred_path = r'project-2067513094095777077-firebase-adminsdk-e7qjk-98f7d17888.json'
                    if os.path.exists(cred_path):
                        cred = credentials.Certificate(cred_path)
                        firebase_admin.initialize_app(cred)
                        logging.info("Firebase initialized successfully with local service account file")
                    else:
                        logging.warning("No Firebase credentials found. Initializing without authentication.")
                        firebase_admin.initialize_app()
            except Exception as e:
                logging.error(f"Failed to initialize Firebase: {e}")
                raise
        
        self.db = firestore.client()
        logging.info("Firestore client initialized")

    async def update_user_memory(self, user_id, new_data):
        """Update or create user memory in Firestore"""
        try:
            # Ensure all datetime objects are converted to ISO format strings
            if isinstance(new_data.get('last_interaction_date'), datetime):
                new_data['last_interaction_date'] = new_data['last_interaction_date'].isoformat()
            if isinstance(new_data.get('first_interaction'), datetime):
                new_data['first_interaction'] = new_data['first_interaction'].isoformat()
            
            user_ref = self.db.collection('user_memories').document(str(user_id))
            user_ref.set(new_data, merge=True)
            logging.info(f"User memory updated for user_id: {user_id}")
        except Exception as e:
            logging.error(f"Error updating user memory: {e}")
            logging.exception("Full exception:")

    async def get_user_memory(self, user_id):
        """Get user memory from Firestore"""
        try:
            user_ref = self.db.collection('user_memories').document(str(user_id))
            doc = user_ref.get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            logging.error(f"Error getting user memory: {e}")
            return None

    async def store_chat(self, user_id, message, response, emotion, context):
        """Store chat history in Firestore"""
        try:
            current_time = datetime.now()
            chat_ref = self.db.collection('chat_history').document()
            chat_ref.set({
                "user_id": str(user_id),
                "timestamp": current_time.isoformat(),
                "message": message,
                "response": response,
                "emotion": emotion,
                "context": context
            })
        except Exception as e:
            logging.error(f"Error storing chat: {e}")
            logging.exception("Full exception:")

    async def update_emotional_state(self, user_id, emotion_data):
        """Update user's emotional state in Firestore"""
        try:
            # Ensure timestamp is in ISO format
            if isinstance(emotion_data.get('last_updated'), datetime):
                emotion_data['last_updated'] = emotion_data['last_updated'].isoformat()
            
            emotion_ref = self.db.collection('emotional_states').document(str(user_id))
            emotion_ref.set(emotion_data, merge=True)
        except Exception as e:
            logging.error(f"Error updating emotional state: {e}")
            logging.exception("Full exception:")

    async def get_emotional_state(self, user_id):
        """Get user's emotional state from Firestore"""
        try:
            emotion_ref = self.db.collection('emotional_states').document(str(user_id))
            doc = emotion_ref.get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            logging.error(f"Error getting emotional state: {e}")
            return None

    async def store_group_state(self, group_id, state_data):
        """Store group state in Firestore"""
        try:
            group_ref = self.db.collection('group_states').document(str(group_id))
            group_ref.set(state_data, merge=True)
        except Exception as e:
            logging.error(f"Error storing group state: {e}")

    async def get_group_state(self, group_id):
        """Get group state from Firestore"""
        try:
            group_ref = self.db.collection('group_states').document(str(group_id))
            doc = group_ref.get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            logging.error(f"Error getting group state: {e}")
            return None
