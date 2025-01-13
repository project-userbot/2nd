from pymongo import MongoClient
from datetime import datetime
import logging

class DatabaseHandler:
    def __init__(self):
        self.uri = "mongodb+srv://adityasinghcompany2:Achiadi123@cluster0.dcsny.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
        self.db_name = "Cluster0"
        self.client = MongoClient(self.uri)
        self.db = self.client[self.db_name]
        
        # Collections
        self.user_memories = self.db.user_memories
        self.chat_history = self.db.chat_history
        self.emotional_states = self.db.emotional_states
        self.group_dynamics = self.db.group_dynamics

    async def update_user_memory(self, user_id, new_data):
        """Update or create user memory with enhanced tracking"""
        try:
            # Get existing memory to properly update friendship level
            existing_memory = await self.get_user_memory(user_id)
            current_friendship = existing_memory.get('friendship_level', 1) if existing_memory else 1
            
            # Calculate new friendship level based on interaction count
            interaction_count = new_data.get('interaction_count', 0)
            friendship_modifier = 0
            
            # Increase friendship based on interaction count milestones
            if interaction_count > 100:
                friendship_modifier = 4
            elif interaction_count > 50:
                friendship_modifier = 3
            elif interaction_count > 20:
                friendship_modifier = 2
            elif interaction_count > 5:
                friendship_modifier = 1

            # Update the document
            self.user_memories.update_one(
                {"user_id": user_id},
                {"$set": {
                    "last_updated": datetime.now(),
                    "friendship_level": max(1, min(10, current_friendship + friendship_modifier)),
                    "first_interaction_date": new_data.get('first_interaction', datetime.now()),
                    "total_interactions": interaction_count,
                    "past_interactions": new_data.get('past_interactions', []),
                    "topics_discussed": new_data.get('topics_discussed', []),
                    "last_interaction_date": datetime.now(),
                    "personality_traits": new_data.get('personality_traits', []),
                    "interests": new_data.get('interests', [])
                }},
                upsert=True
            )
        except Exception as e:
            logging.error(f"Error updating user memory: {e}")

    async def get_user_memory(self, user_id):
        """Get user memory with enhanced retrieval"""
        try:
            memory = self.user_memories.find_one({"user_id": user_id})
            if memory:
                # Calculate time-based metrics
                last_interaction = memory.get('last_interaction_date', datetime.now())
                time_diff = (datetime.now() - last_interaction).days
                
                # Adjust friendship level based on inactivity
                if time_diff > 30:  # Inactive for more than a month
                    friendship_level = max(1, memory.get('friendship_level', 1) - 1)
                    self.user_memories.update_one(
                        {"user_id": user_id},
                        {"$set": {"friendship_level": friendship_level}}
                    )
                    memory['friendship_level'] = friendship_level
                
            return memory
        except Exception as e:
            logging.error(f"Error getting user memory: {e}")
            return None

    async def store_chat(self, user_id, message, response, emotion, context):
        """Store chat history with enhanced emotional context"""
        try:
            # Get current emotional state
            emotional_state = await self.get_emotional_state(user_id)
            current_happiness = emotional_state.get('happiness_level', 5) if emotional_state else 5
            
            # Analyze message sentiment impact
            sentiment_impact = 1 if emotion in ['very_happy', 'happy'] else -1 if emotion in ['angry', 'sad'] else 0
            
            self.chat_history.insert_one({
                "user_id": user_id,
                "timestamp": datetime.now(),
                "message": message,
                "response": response,
                "emotion": emotion,
                "context": context,
                "happiness_impact": sentiment_impact,
                "interaction_length": len(message) + len(response)
            })
            
            # Update emotional state based on interaction
            await self.update_emotional_state(user_id, {
                "current": emotion,
                "history": emotional_state.get('emotion_history', [])[-10:] + [emotion] if emotional_state else [emotion],
                "happiness_level": max(1, min(10, current_happiness + sentiment_impact)),
                "trust_level": emotional_state.get('trust_level', 1) if emotional_state else 1
            })
            
        except Exception as e:
            logging.error(f"Error storing chat: {e}")

    async def update_emotional_state(self, user_id, emotion_data):
        """Update user's emotional state with enhanced tracking"""
        try:
            # Get current state for proper updates
            current_state = await self.get_emotional_state(user_id)
            
            # Calculate trust level based on interaction history
            trust_modifier = 0
            if current_state:
                positive_emotions = sum(1 for emotion in current_state.get('emotion_history', [])
                                     if emotion in ['very_happy', 'happy'])
                if len(current_state.get('emotion_history', [])) > 0:
                    positive_ratio = positive_emotions / len(current_state['emotion_history'])
                    trust_modifier = 1 if positive_ratio > 0.6 else -1 if positive_ratio < 0.3 else 0
            
            # Update emotional state
            self.emotional_states.update_one(
                {"user_id": user_id},
                {"$set": {
                    "last_updated": datetime.now(),
                    "current_emotion": emotion_data["current"],
                    "emotion_history": emotion_data["history"][-10:],
                    "happiness_level": emotion_data["happiness_level"],
                    "trust_level": max(1, min(10, emotion_data["trust_level"] + trust_modifier)),
                    "emotional_stability": self._calculate_emotional_stability(emotion_data["history"]),
                    "last_emotion_change": datetime.now()
                }},
                upsert=True
            )
        except Exception as e:
            logging.error(f"Error updating emotional state: {e}")

    def _calculate_emotional_stability(self, emotion_history):
        """Calculate emotional stability based on emotion changes"""
        if not emotion_history:
            return 5  # Default stability
            
        changes = sum(1 for i in range(1, len(emotion_history))
                     if emotion_history[i] != emotion_history[i-1])
        
        if len(emotion_history) <= 1:
            return 8  # High stability for new users
        
        change_ratio = changes / (len(emotion_history) - 1)
        return 10 - (change_ratio * 10)  # Higher score means more stable

    async def get_emotional_state(self, user_id):
        """Get user's emotional state with enhanced retrieval"""
        try:
            state = self.emotional_states.find_one({"user_id": user_id})
            if state:
                # Calculate emotional trend
                history = state.get('emotion_history', [])
                if len(history) >= 3:
                    recent_emotions = history[-3:]
                    if all(emotion in ['very_happy', 'happy'] for emotion in recent_emotions):
                        state['emotional_trend'] = 'improving'
                    elif all(emotion in ['angry', 'sad'] for emotion in recent_emotions):
                        state['emotional_trend'] = 'deteriorating'
                    else:
                        state['emotional_trend'] = 'stable'
                
            return state
        except Exception as e:
            logging.error(f"Error getting emotional state: {e}")
            return None

    async def get_response_length_factor(self, user_id):
        """Calculate appropriate response length factor based on friendship level"""
        try:
            memory = await self.get_user_memory(user_id)
            friendship_level = memory.get('friendship_level', 1) if memory else 1
            
            # Calculate base response length multiplier
            if friendship_level <= 2:
                return 0.3  # Very short responses
            elif friendship_level <= 4:
                return 0.5  # Short responses
            elif friendship_level <= 6:
                return 0.8  # Medium responses
            elif friendship_level <= 8:
                return 1.0  # Normal responses
            else:
                return 1.2  # Longer, more detailed responses
                
        except Exception as e:
            logging.error(f"Error calculating response length: {e}")
            return 0.5  # Default to short responses on error

    async def update_group_dynamics(self, group_id, dynamics_data):
        """Update group dynamics"""
        try:
            self.group_dynamics.update_one(
                {"group_id": group_id},
                {"$set": {
                    "last_updated": datetime.now(),
                    **dynamics_data
                }},
                upsert=True
            )
        except Exception as e:
            logging.error(f"Error updating group dynamics: {e}")