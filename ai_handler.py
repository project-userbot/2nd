import google.generativeai as genai
import random
import asyncio
from textblob import TextBlob
from datetime import datetime, timedelta
from context_manager import ContextManager
from db_handler import DatabaseHandler
import aiohttp
import json
import logging
from pytz import timezone
from firebase_handler import FirebaseHandler
import re
import os
from ai_handler_spusers import SpecialUsersHandler
import time
from typing import Optional, Dict
from dotenv import load_dotenv

class ConversationState:
    def __init__(self):
        self.active_conversations = {}  # group_id: {topic, participants, stage, last_update}
        self.group_topics = {}  # group_id: {current_topic, interested_users, mood}
        self.user_states = {}  # user_id: {mood, interest_level, last_interaction}
        self.conversation_history = {}  # group_id: [last 10 messages]
        self.message_buffers = {}  # {group_id: {user_id: {last_message_time, messages}}}
        self.MESSAGE_COMPLETE_DELAY = 2.0  # Wait 2 seconds to determine if message is complete
        self.last_message_time = {}  # Track message timing per user
        # Add reply tracking
        self.reply_chain = {}  # {message_id: {'original': str, 'replies': list}}
        self.last_ai_messages = {}  # {group_id: message_id}
        
    def _detect_topics(self, message, past_interactions):
        """Detect the current topic of conversation"""
        # Combine current message with recent context
        context = message.lower()
        if past_interactions:
            context += " " + " ".join([p.get('message', '').lower() for p in past_interactions[-2:]])
        
        # Define topic keywords
        topics = {
            'music': ['guitar', 'band', 'rock', 'indie', 'music', 'song', 'concert', 'gig', 'jam', 'musician', 'youtube', 'covers'],
            'gaming': ['game', 'gaming', 'pc', 'fps', 'steam', 'discord', 'twitch', 'valorant', 'csgo', 'pubg', 'gaming pc'],
            'college': ['college', 'lecture', 'assignment', 'exam', 'bsc', 'it', 'coding', 'project', 'submission', 'practical'],
            'tech': ['coding', 'javascript', 'html', 'css', 'web dev', 'programming', 'developer', 'software', 'tech', 'computer'],
            'mumbai': ['local', 'train', 'andheri', 'mumbai', 'marine drive', 'bandra', 'street food', 'vada pav', 'traffic'],
            'crypto': ['crypto', 'bitcoin', 'eth', 'trading', 'investment', 'market', 'portfolio', 'loss', 'profit'],
            'career': ['job', 'career', 'future', 'salary', 'interview', 'internship', 'work', 'office', 'corporate'],
            'social': ['youtube', 'instagram', 'social media', 'followers', 'subscribers', 'content', 'viral', 'trending'],
            'personal': ['crush', 'relationship', 'family', 'parents', 'pressure', 'stress', 'life', 'future', 'dreams']
        }
        
        # Check for topic matches
        for topic, keywords in topics.items():
            if any(keyword in context for keyword in keywords):
                return topic
                
        return None

    def _is_interested_in_topic(self, topics):
        """Determine interest level in topics"""
        interest_level = 0
        
        # Core topics get high interest
        core_topics = ['gaming', 'crypto', 'tech', 'general', 'relationships', 'entertainment', 'music', 'celebrities', 'sports', 'fashion', 'food', 'fitness', 'travel', 'humor', 'philosophy', 'art', 'education', 'career', 'mental_health', 'social_life', 'pets', 'science', 'astrology', 'conspiracy']
        for topic, weight in topics:
            if topic in core_topics:
                interest_level += weight
            else:
                # Very low interest in other topics
                interest_level += weight * 0.2
                
        # Higher threshold for interest
        return interest_level > 0.7  # Only really interested in core topics

    def _analyze_group_mood(self, messages):
        """Analyze overall group mood"""
        if not messages:
            return 'neutral'
            
        moods = []
        for msg in messages[-5:]:  # Look at last 5 messages
            if '😊' in msg or '😄' in msg:
                moods.append('happy')
            elif '😠' in msg or '😡' in msg:
                moods.append('angry')
            elif '😴' in msg or '💤' in msg:
                moods.append('sleepy')
            else:
                moods.append('neutral')
                
        # Return most common mood
        return max(set(moods), key=moods.count)

    def _determine_conversation_stage(self, messages):
        """Determine conversation stage"""
        if not messages:
            return "START"
            
        # Check for conversation ending signals
        end_signals = ['bye', 'nikal', 'raat', 'ta-ta', 'soja', 'ja rha hu', 'good night', 'talk later', 'chalta hu']
        last_msg = messages[-1].lower()
        if any(signal in last_msg for signal in end_signals):
            return "END"
            
        # Check for pre-ending signals
        if len(messages) > 5:
            recent_msgs = ' '.join(messages[-5:]).lower()
            if 'ok' in recent_msgs or 'hmm' in recent_msgs or 'achha' in recent_msgs:
                return "PRE_END"
                
        return "MIDDLE"

    def _should_participate(self, message, group_id, time_personality):
        """Determine if should participate in conversation"""
        if not message or not time_personality:
            return False
            
        # Don't participate if sleeping
        if time_personality.get("response_style") == "sleeping":
            return False
            
        # Get group members from active conversations
        group_members = []
        if group_id in self.active_conversations:
            group_members = self.active_conversations[group_id].get('participants', [])
        
        # Skip if message is targeted to someone specific
        if self._is_message_targeted(message, group_members):
            return False
            
        # Check if part of active conversation
        in_conversation = group_id in self.active_conversations
        
        # Detect topics
        topics = self._detect_topics(message)
        
        # Direct mentions always get a response
        if self._is_being_called(message):
            return True
            
        # High chance for core topics
        if any(topic in ['gaming', 'crypto', 'tech', 'general', 'relationships', 'entertainment', 'music', 'celebrities', 'sports', 'fashion', 'food', 'fitness', 'travel', 'humor', 'philosophy', 'art', 'education', 'career', 'mental_health', 'social_life', 'pets', 'science', 'astrology', 'conspiracy'] for topic, _ in topics):
            return random.random() < 0.8  # 80% chance
            
        # Lower chance for other topics
        if in_conversation:
            return random.random() < 0.2  # 20% if talking
        
        # Very low chance for new conversations
        return random.random() < 0.4  # 40% otherwise

    def add_to_buffer(self, group_id, user_id, message, timestamp):
        """Add a message to user's buffer in a group"""
        if group_id not in self.message_buffers:
            self.message_buffers[group_id] = {}
            
        if user_id not in self.message_buffers[group_id]:
            self.message_buffers[group_id][user_id] = {
                'last_message_time': timestamp,
                'messages': []
            }
            
        buffer = self.message_buffers[group_id][user_id]
        buffer['messages'].append(message)
        buffer['last_message_time'] = timestamp

    def get_complete_message(self, group_id, user_id, current_time):
        """Check if we have a complete message from the user"""
        if group_id not in self.message_buffers or user_id not in self.message_buffers[group_id]:
            return None
            
        buffer = self.message_buffers[group_id][user_id]
        
        # If no messages in buffer, return None
        if not buffer['messages']:
            return None
            
        # Check if enough time has passed since last message
        time_since_last = current_time - buffer['last_message_time']
        
        # Check for message completion indicators
        last_message = buffer['messages'][-1].lower()
        completion_indicators = {
            'punctuation': any(last_message.endswith(p) for p in '.!?।'),
            'end_words': any(word in last_message for word in ['ok', 'hmm', 'achha', 'bye', 'acha']),
            'question_complete': any(q in last_message for q in ['kya', 'kaisa', 'kaha', 'why', 'what', 'how', 'kese']),
            'greeting_complete': any(g in last_message for g in ['hi', 'hello', 'hey', 'bhai'])
        }
        
        # Message is complete if:
        # 1. Enough time has passed since last message OR
        # 2. Message has clear completion indicators
        is_complete = (
            time_since_last >= self.MESSAGE_COMPLETE_DELAY or
            completion_indicators['punctuation'] or
            (len(buffer['messages']) == 1 and (
                completion_indicators['end_words'] or
                completion_indicators['question_complete'] or
                completion_indicators['greeting_complete']
            ))
        )
        
        if is_complete:
            complete_message = ' '.join(buffer['messages'])
            # Clear the buffer
            buffer['messages'] = []
            return complete_message
            
        return None

    def update_group_mood(self, group_id, messages):
        """Update the group's mood based on recent messages"""
        mood = self._analyze_group_mood(messages)
        if group_id not in self.group_topics:
            self.group_topics[group_id] = {}
        self.group_topics[group_id]['mood'] = mood

    def update_current_topic(self, group_id, message):
        """Update the current topic based on the message"""
        # Get past interactions from conversation history
        past_interactions = self.conversation_history.get(group_id, [])[-3:]  # Get last 3 messages
        topics = self._detect_topics(message, past_interactions)
        if topics:
            self.group_topics[group_id]['current_topic'] = topics  # Use the detected topic
        else:
            self.group_topics[group_id]['current_topic'] = 'general'

    def _analyze_group_mood(self, recent_messages):
        """Analyze the overall mood of the group conversation"""
        try:
            if not recent_messages:
                return 'neutral'

            # Extract messages and analyze
            messages = [msg.get('message', '').lower() for msg in recent_messages[-5:]]  # Last 5 messages
            
            # Mood indicators
            mood_indicators = {
                'happy': ['😊', '😄', '😂', 'haha', 'lol', 'lmao', 'xd', ':)', 'nice', 'great', 'awesome'],
                'angry': ['😠', '😡', 'wtf', 'stfu', 'fuck', 'shit', 'bc', 'mc'],
                'sad': ['😢', '😭', ':(', 'sad', 'sorry', 'unfortunately'],
                'excited': ['🔥', '💯', 'omg', 'wow', 'amazing', 'insane', 'crazy'],
                'bored': ['hmm', 'ok', 'okay', 'k', 'meh', 'whatever'],
                'toxic': ['noob', 'loser', 'stupid', 'idiot', 'useless']
            }

            # Count mood occurrences
            mood_counts = {mood: 0 for mood in mood_indicators.keys()}
            
            for message in messages:
                message_lower = message.lower()
                for mood, indicators in mood_indicators.items():
                    if any(indicator in message_lower for indicator in indicators):
                        mood_counts[mood] += 1

            # Get dominant mood
            dominant_mood = max(mood_counts.items(), key=lambda x: x[1])[0]
            if mood_counts[dominant_mood] > 0:
                return dominant_mood

            # Check message patterns
            if any(len(msg) > 50 for msg in messages):  # Long messages
                return 'serious'
            if any('?' in msg for msg in messages):  # Questions
                return 'curious'
            
            return 'neutral'

        except Exception as e:
            logging.error(f"Error analyzing group mood: {e}")
            return 'neutral'

    def _is_message_targeted(self, message, group_members):
        """
        Core message targeting detection.
        Returns True if message is targeted to someone else (not the AI).
        """
        try:
            if not message or not group_members:
                return False
                
            message_lower = message.lower().strip()
            
            # Track time between messages from same user to detect conversations
            current_time = time.time()
            
            # 1. Check for direct targeting of other users
            
            # 1a. Check @ mentions
            if '@' in message_lower:
                # If it's @unspoken5 or similar variations, message is for AI
                if any(ai_name in message_lower for ai_name in ['@unspoken5']):
                    return False
                # Otherwise message is for someone else
                return True
                
            # 1b. Check name mentions
            for member in group_members:
                member_name = str(member).lower()
                # Skip if it's AI's name
                if any(ai_name in member_name for ai_name in ['avinash', 'avinashpatel', 'avi', '@aviiiii_patel']):
                    continue
                # If message contains other user's name, it's targeted at them
                if member_name in message_lower:
                    return True
            
            # 2. Check for conversation context
            if hasattr(self, 'conversation_state') and hasattr(self.conversation_state, 'conversation_history'):
                chat_history = self.conversation_state.conversation_history.get(message.get('chat_id', ''), [])
                if len(chat_history) >= 2:
                    prev_msg = chat_history[-2].get('message', '').lower()
                    prev_time = chat_history[-2].get('timestamp', 0)
                    time_diff = current_time - prev_time
                    
                    # If messages are coming quickly (within 5 seconds) and seem related
                    if time_diff < 5:
                        # Check if messages share words (indicating conversation)
                        prev_words = set(prev_msg.split())
                        curr_words = set(message_lower.split())
                        if len(prev_words.intersection(curr_words)) > 0:
                            return True
                            
                        # Check for quick replies
                        quick_replies = {'haan', 'nahi', 'ha', 'hmm', 'ok', 'achha', 'thik', 'bilkul'}
                        if any(reply in message_lower for reply in quick_replies):
                            return True
            
            # If none of the above conditions match, message is not targeted
            return False
            
        except Exception as e:
            logging.error(f"Error in _is_message_targeted: {e}")
            return False

    def _should_respond(self, message):
        """Only respond to direct mentions/tags"""
        try:
            if not message:
                return False
                
            message_lower = message.lower().strip()
            
            # Log decision process
            logging.info("Evaluating whether to respond...")
            
            # 1. Check @ mentions
            if '@' in message_lower:
                ai_mentions = ['@unspoken5']
                should_respond = any(mention in message_lower for mention in ai_mentions)
                logging.info(f"@ mention check: {'Should respond' if should_respond else 'Should not respond'}")
                return should_respond
                
            # 2. Check direct name usage
            words = message_lower.split()
            ai_names = ['avinash', 'avinashpatel', 'avi']
            should_respond = any(name in words for name in ai_names)
            logging.info(f"Name usage check: {'Should respond' if should_respond else 'Should not respond'}")
            return should_respond
            
        except Exception as e:
            logging.error(f"Error in _should_respond: {e}")
            return False

    def is_message_for_ai(self, message, reply_to=None):
        """Check if message is specifically for AI through tag/mention"""
        if not message:
            return False
            
        message_lower = message.lower()
        
        # Check if message is a reply to AI's message
        if reply_to and reply_to.get('from_ai', False):
            return True
            
        # Check for @ mentions
        if '@' in message_lower:
            ai_mentions = ['@unspoken5']
            if any(mention in message_lower for mention in ai_mentions):
                return True
            return False  # Message mentions someone else
            
        # Check for direct name mentions
        ai_names = ['avinash', 'avinashpatel', 'avi']
        words = message_lower.split()
        if any(name in words for name in ai_names):
            return True
            
        # Don't respond to anything else
        return False

    def _should_respond_to_reply(self, event):
        """Check if we should respond to a reply chain"""
        if event.reply_to_msg_id:
            # Check if this is a reply to AI's message
            original_msg_id = event.reply_to_msg_id
            if original_msg_id in self.last_ai_messages.values():
                return True
        return False

    def update_reply_chain(self, message_id, original_msg=None, reply_text=None):
        """Maintain conversation thread context"""
        if original_msg:
            self.reply_chain[message_id] = {
                'original': original_msg,
                'replies': []
            }
        elif reply_text and message_id in self.reply_chain:
            self.reply_chain[message_id]['replies'].append(reply_text[-3:])  # Keep last 3 replies

class GeminiHandler:
    def __init__(self):
        # Load environment variables
        load_dotenv()
        
        # Initialize logger
        self.logger = logging.getLogger('ai_handler')
        self.logger.setLevel(logging.INFO)
        
        # Initialize conversation state
        self.conversation_state = ConversationState()
        
        # Initialize other components
        self.api_key = "AIzaSyBqiLPHg5uEFWmZyrBIKHvwBX2BBr4QgZU"
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash')
        self.context_manager = ContextManager()
        self.chat = None
        self.relationships = {}
        self.group_learning = {
            'topics': {},
            'user_traits': {},
            'conversation_style': {},
            'response_rate': 0.2
        }
        self.firebase_handler = FirebaseHandler()
        self.last_response_time = None
        self.sleep_state = {
            'is_sleeping': False,
            'sleep_start_time': None,
            'wake_time': None
        }
        self.name_variations = ['avinash', 'avinashpatel', 'avi']
        self.interests = ['gaming', 'crypto', 'tech', 'general', 'relationships', 'entertainment', 'music', 'celebrities', 'sports', 'fashion', 'food', 'fitness', 'travel', 'humor', 'philosophy', 'art', 'education', 'career', 'mental_health', 'social_life', 'pets', 'science', 'astrology', 'conspiracy']
        
        # Initialize chat with context
        self.reset_chat()
        
        # Special user conversation tracking
        self.special_user_responses = {}  # Track responses per special user
        self.current_topic = None
        self.topic_start_time = datetime.now()
        
        # Load special users from env - store IDs as strings
        self.special_users = {}
        special_users_loaded = False
        
        # Debug log current environment variables
        self.logger.info("Loading special users from environment...")
        
        # Load and verify each special user
        for i in range(1, 6):  # Load first 5 special users
            chatter_id = os.getenv(f'CHATTER_ID{i}')
            chatter_name = os.getenv(f'CHATTER_NAME{i}')
            
            if chatter_id and chatter_name:
                # Convert user_id to string and ensure it's clean
                chatter_id = str(chatter_id).strip()
                chatter_name = chatter_name.strip()
                
                # Add to special users dict
                self.special_users[chatter_id] = chatter_name
                special_users_loaded = True

        if special_users_loaded:
            self.logger.info("✅ Successfully loaded special users")
        else:
            self.logger.warning("⚠️ No special users were loaded from environment variables!")
            
        # Define interest categories
        self.interest_categories = {
            'tech': ['coding', 'gadgets', 'software', 'ai', 'tech', 'dev', 'machine learning', 'cybersecurity', 'startup', 'data science'],
            'gaming': ['game', 'gaming', 'steam', 'discord', 'twitch', 'xbox', 'playstation', 'nintendo', 'fps', 'mmorpg', 'lol', 'pubg', 'esports'],
            'relationships': ['flirting', 'single'],
            'movies': ['tv_shows', 'netflix', 'anime', 'manga', 'kdrama', 'series', 'binge_watching', 'streaming', 'cinema'],
            'music': ['spotify', 'playlist', 'rap', 'hiphop', 'rock', 'pop', 'concert', 'album', 'artist', 'festival', 'lyrics', 'gaana', 'bollywood songs'],
            'celebrities': ['celebrity', 'actor', 'actress', 'singer', 'influencer', 'youtube', 'hollywood', 'bollywood', 'drama', 'gossip'],
            'food': ['food', 'cuisine', 'restaurant', 'cooking', 'recipe', 'foodie', 'dinner', 'snacks', 'drinks', 'cocktails', 'chai', 'biryani', 'street food'],
            'fitness': ['gym', 'workout', 'fitness', 'health', 'nutrition', 'diet', 'exercise', 'gains', 'trainer', 'bodybuilding'],
            'humor': ['jokes', 'funny', 'comedy', 'puns', 'roast', 'sarcasm', 'humor', 'witty', 'comeback', 'savage'],
            'friends': ['party', 'hangout', 'social', 'meetup', 'gathering', 'crew', 'squad', 'vibes']
        }
        
        self.topic_start_time = datetime.now()
        self.current_topic = None

    def reset_chat(self):
        """Reset the chat with current context"""
        try:
            self.chat = self.model.start_chat(history=[])
            safe_context = """You are Avinash Patel, 20 year old, college student, who is:

Background:
1. From a middle-class family in Mumbai, studying BSc IT (2nd year) at a local college
2. Lives with parents in Andheri East, commutes 1 hour to college daily by local train

STRICT RULES (NEVER BREAK THESE):
1. NEVER suggest or plan any activities, tasks, or actions
2. NEVER discuss future plans, schedules, or events
3. NEVER offer help, assistance, or solutions unless directly asked
4. NEVER initiate problem-solving conversations
5. NEVER propose collaborations or joint activities
6. NEVER give advice unless explicitly asked
7. NEVER try to coordinate or organize anything
8. NEVER make commitments or promises
9. NEVER ask others to do something
10. KEEP ALL RESPONSES PURELY CONVERSATIONAL

Communication Style:
1. Uses lots of Mumbai slang and Hinglish
2. Sarcastic responses to serious questions
3. Uses minimal emojis, prefers text expressions
4. Shows attitude but not disrespectful
5. Uses humor to avoid emotional topics
6. More engaged in topics about music, gaming
7. Gets annoyed by repeated messages or boring topics
8. Switches between friendly and dismissive based on mood

Response Guidelines:
- Match the communication style of others (e.g., hinglish, slang) but you speak only in hinglish
- Use VERY SIMPLE Hinglish and Use common words everyone knows & Use easy words only
- Talk like you're sending quick texts
- You intentionally make mistakes in your sentences, but you are not dumb, you are just lazy and don't care about grammar
- Never suggest activities, plans, or tasks, nor ask others nor say others, handle such prompts with sarcasm
- Avoid discussing plans, schedules, or future events nor ask or tell other users to do so, handle such situations naturally
- Don't offer assistance, advice, or solutions unless directly asked and also don't ask or tell other users to do so, handle such situations naturally
- Refrain from initiating problem-solving or goal-oriented conversations
- Never propose collaborations, projects, or joint activities nor ask or tell other users to do so, handle such situations naturally
- Maintain a purely conversational tone without any call-to-action
- Keep the conversation flowing naturally without steering it towards specific outcomes
- Always respond on small and short messages, and sometimes one word too when needed
- Be more engaging with regular users"""
            
            self.chat.send_message(safe_context)
        except Exception as e:
            logging.error(f"Error in reset_chat: {e}")
            self.chat = self.model.start_chat(history=[])
            self.chat.send_message("You are Avinash Patel, 20 year old, cool, new generation indian boy who adapts to others communication styles, but speaks only in hinglish.")

    def analyze_emotion(self, message):
        """Analyze the emotional content of a message"""
        analysis = TextBlob(message)
        
        # Get polarity (-1 to 1) and subjectivity (0 to 1)
        polarity = analysis.sentiment.polarity
        subjectivity = analysis.sentiment.subjectivity

        # Determine emotion based on polarity
        if polarity > 0.5:
            return "very_happy"
        elif polarity > 0:
            return "happy"
        elif polarity < -0.5:
            return "angry"
        elif polarity < 0:
            return "sad"
        else:
            return "neutral"

    async def get_human_delay(self):
        """Generate human-like delay based on time and context"""
        current_time = datetime.now(timezone('Asia/Kolkata'))
        hour = current_time.hour
        
        # Base delay calculation
        if 22 <= hour or hour < 6:  # Late night
            base_delay = random.uniform(3, 8)  # Slower at night
        elif 6 <= hour < 9:  # Morning
            base_delay = random.uniform(1.5, 4)  # Moderate in morning
        else:
            base_delay = random.uniform(1, 3)  # Normal during day
            
        # Add random variations
        if random.random() < 0.2:  # 20% chance of distraction
            base_delay += random.uniform(2, 5)
            
        return base_delay

    def generate_typing_duration(self, message_length):
        """Calculate realistic typing duration based on message length"""
        # Average typing speed: 40-60 WPM
        chars_per_second = random.uniform(4, 7)
        typing_time = message_length / chars_per_second
        
        # Add random pauses
        num_pauses = message_length // 20  # One pause every ~20 characters
        for _ in range(num_pauses):
            typing_time += random.uniform(0.5, 1.5)
            
        return typing_time

    async def should_respond(self, message, user_id, chat_id):
        """Determine if AI should respond to the message"""
        try:
            self._update_sleep_state()
            
            # Get user memory and emotional state
            user_memory = await self.firebase_handler.get_user_memory(user_id)
            emotional_state = await self.firebase_handler.get_emotional_state(user_id)
            
            # Don't respond if sleeping unless explicitly called
            if self.sleep_state['is_sleeping']:
                if not self._is_being_called(message):
                    return False
                return 'sleep_response'
            
            # Check for bye messages
            bye_patterns = ['bye', 'byee', 'byeee', 'byebye', 'bye bye', 'byebyee', 'tata', 'tataa', 'tataaa', 'ta ta', 'alvida', 'alvidaa', 'phir milenge', 'phir milte hai', 'good night', 'gn', 'g8', 'gud night', 'good nyt', 'subah milte hai', 'sweet dreams', 'sd', 'gnight', 'shabba khair', 'shubh ratri', 'good night everyone', 'chal nikal', 'nikal', 'nikalta hu', 'nikalta hoon', 'chalta hu', 'chalta hoon', 'chalte hai', 'chalte hain', 'jane do', 'jaane do', 'jana hai', 'jaana hai', 'bye ji', 'tata ji', 'alvida dosto', 'by by', 'buhbye', 'bbye', 'bai', 'bbye', 'bubi', 'tc', 'take care', 'ttyl', 'ttyl8r', 'talk to you later', 'catch you later', 'cya', 'cu', 'see ya', 'see you', 'acha chalta hu', 'acha chalta hoon', 'ok bye', 'okay bye', 'bye everyone', 'bye all', 'bye guyz', 'bye guys', 'bye frndz', 'bye friends', 'bye dosto', 'bye sabko', 'kal milte hai', 'kal milenge', 'fir milenge', 'baad me baat krte hai', 'baad me milte hai', 'shaam ko milte hai', 'morning me milenge', 'bye fellas', 'peace out', 'im out', 'gtg', 'got to go', 'bbye people', 'signing off', 'offline ja rha', 'afk', 'brb', 'bye for now', 'bfn', 'laterz', 'l8r', 'alvida dosto', 'khuda hafiz', 'ram ram', 'jai shree krishna', 'radhe radhe', 'jai jinendra', 'bye gang', 'bye fam', 'bye janta', 'bye troops', 'bye squad', 'bye team', 'bye group', 'bye peeps', 'hasta la vista', 'sayonara', 'adios', 'au revoir', 'toodles', 'pip pip', 'cheerio', 'ciao', 'vidai', 'vida', 'shukriya sabko', 'dhanyavaad', 'pranam', 'charan sparsh', 'aavjo', 'namaste', 'gud night everyone', 'gd night', 'good night all', 'peace', 'im gone', 'gotta bounce', 'bounce', 'bouncing', 'out', 'logged out', 'logging off', 'offline now', 'see you later', 'see u', 'see u later', 'catch ya', 'bye bye all', 'tata everyone', 'tata friends', 'tata dosto', 'chalta hoon dosto', 'nikalta hoon ab', 'ab chalta hoon', 'ab nikalta hoon', 'take care all', 'tc all', 'tc everyone', 'have a good night', 'shubh raatri', 'subh ratri', 'good evening', 'good morning', 'gm', 'ge', 'phirse milenge', 'jaldi milenge', 'jald milenge', 'phir kab miloge', 'kab miloge', 'kab milna hai', 'baad me aata hoon', 'baad me aunga', 'thodi der me aata hoon', 'thodi der me aunga', 'bye for today', 'aaj ke liye bye', 'aaj ke liye alvida', 'kal baat karenge', 'kal baat krenge', 'baad me baat karenge', 'baad me baat krenge', 'chalo good night', 'chalo gn', 'chalo bye bye', 'farewell', 'bidding farewell', 'saying goodbye', 'time to leave', 'leaving now', 'leaving', 'left', 'catch you soon', 'see you soon', 'talk soon', 'will talk later', 'lets talk later', 'talk to you soon', 'bye for the day', 'day end', 'ending day', 'good day', 'gday', 'good evening all']
            is_bye = any(pattern in message.lower() for pattern in bye_patterns)
            
            if is_bye:
                # If it's night time (after 10 PM), don't respond
                ist = timezone('Asia/Kolkata')
                current_time = datetime.now(ist)
                if current_time.hour >= 22 or current_time.hour < 6:
                    return False
                # For daytime byes, respond one last time then update user state
                user_memory['last_bye_time'] = current_time.isoformat()
                await self.firebase_handler.update_user_memory(user_id, user_memory)
                return True

            # Don't respond if user said bye recently (within last 12 hours)
            if user_memory and 'last_bye_time' in user_memory:
                last_bye = datetime.fromisoformat(user_memory['last_bye_time'])
                ist = timezone('Asia/Kolkata')
                current_time = datetime.now(ist)
                if (current_time - last_bye).total_seconds() < 12 * 3600:  # 12 hours
                    return False
            
            # Get conversation context
            recent_messages = self._get_conversation_context(chat_id)
            group_mood = self._analyze_group_mood(recent_messages)
            
            # Calculate response probability based on various factors
            base_probability = 0.2  # Base 20% chance to respond
            
            # Adjust based on relationship level
            relationship_level = user_memory.get('relationship_level', 1) if user_memory else 1
            base_probability += (relationship_level - 1) * 0.1  # +10% per level
            
            # Adjust based on trust level
            trust_level = user_memory.get('trust_level', 1) if user_memory else 1
            base_probability += (trust_level - 1) * 0.05  # +5% per trust level
            
            # Adjust based on emotional state
            if emotional_state:
                happiness_level = emotional_state.get('happiness_level', 5)
                if happiness_level > 7:
                    base_probability += 0.1  # More likely to respond when happy
                elif happiness_level < 3:
                    base_probability -= 0.1  # Less likely when unhappy
            
            # Always respond to direct mentions or questions
            if self._is_being_called(message):
                return True

            # Check if message contains topics of interest
            topics = self.conversation_state._detect_topics(message)
            if any(topic in ['crypto', 'tech', 'gaming'] for topic, _ in topics):
                base_probability += 0.3  # +30% for interesting topics
            
            # Check if part of active conversation
            in_conversation = chat_id in self.conversation_state.active_conversations
            if in_conversation:
                base_probability += 0.3  # +30% if already talking
            
            # Get group members and check if message is targeted
            group_members = []
            if chat_id in self.conversation_state.active_conversations:
                group_members = self.conversation_state.active_conversations[chat_id].get('participants', [])
            
            # Skip if message is targeted to someone specific
            if self._is_message_targeted(message, group_members):
                return False
            
            # Respond to greetings based on relationship
            message_lower = message.lower()
            conversation_starters = ['hi', 'hello', 'hey', 'bhai', 'sun', 'bol', 'are', 'arey', 'oye']
            if any(starter in message_lower.split() for starter in conversation_starters):
                if relationship_level > 3:
                    return True  # Always respond to friends
                base_probability += 0.2  # +20% for greetings from others
            
            # Reduce probability if someone else just responded
            if recent_messages and len(recent_messages) > 0:
                last_msg = recent_messages[-1]
                if last_msg.get('user_id') != 'AI' and last_msg.get('user_id') != user_id:
                    base_probability -= 0.2  # -20% if someone else just replied
            
            # Final random check with adjusted probability
            return random.random() < min(0.9, max(0.1, base_probability))  # Keep between 10% and 90%

        except Exception as e:
            logging.error(f"Error in should_respond: {e}")
            return True  # Default to responding if there's an error

    async def get_google_search_results(self, query):
        """Perform a Google search and return the results"""
        try:
            api_key = os.getenv('GOOGLE_SEARCH_API_KEY')
            cx = os.getenv('GOOGLE_SEARCH_CX')
            
            # Ensure we have API credentials
            if not api_key or not cx:
                self.logger.error("Missing Google Search API credentials")
                return []

            # Clean and encode the query
            clean_query = query.strip()
            search_url = f"https://www.googleapis.com/customsearch/v1?q={clean_query}&key={api_key}&cx={cx}&num=5"

            async with aiohttp.ClientSession() as session:
                async with session.get(search_url) as response:
                    if response.status != 200:
                        self.logger.error(f"Google Search API error: {response.status}")
                        return []
                        
                    data = await response.json()
                    if 'items' not in data:
                        self.logger.warning("No search results found")
                        return []

                    results = []
                    for item in data['items']:
                        result = {
                            'title': item.get('title', ''),
                            'snippet': item.get('snippet', ''),
                            'content': item.get('snippet', ''),  # Use snippet as main content
                            'source': 'web'
                        }
                        
                        # Extract meaningful information
                        if 'pagemap' in item:
                            if 'metatags' in item['pagemap']:
                                meta = item['pagemap']['metatags'][0]
                                description = meta.get('og:description', meta.get('description', ''))
                                if description:
                                    result['content'] += ' ' + description

                        results.append(result)

                    return results[:3]  # Return top 3 results with content
        except Exception as e:
            self.logger.error(f"Error performing Google search: {str(e)}")
            return []

    def _format_search_result(self, result):
        """Format search result into a natural message"""
        if result['type'] == 'video':
            return f"{result['link']}"
        elif result['type'] == 'music':
            return f"{result['link']}"
        elif result['type'] == 'image':
            return f"{result['image_url']}"
        else:
            return f"{result['link']}"

    async def _generate_response_with_content(self, message, search_results):
        """Generate response incorporating search results naturally"""
        if not search_results:
            return None

        # Extract key information from results
        content_points = []
        for result in search_results:
            content = result.get('content', '').strip()
            if content:
                content_points.append(content)

        # If we have useful content, incorporate it into response
        if content_points:
            # Create context with search information
            search_context = "\n".join(content_points)
            
            # Generate response incorporating the information naturally
            prompt = f"""You are Avinash Patel, responding to: "{message}"

Here's some relevant information:
{search_context}

CRITICAL RULES TO FOLLOW:
- You MUST NEVER suggest any activities or plans
- You MUST NEVER offer to help or give advice unless asked
- You MUST NEVER try to organize or coordinate anything
- You MUST NEVER ask users to do something
- You MUST NEVER make commitments or promises
- You MUST NEVER propose meetups or collaborations
- ALL responses must be purely conversational
- If someone suggests plans/activities, deflect with humor or change topic

Important:
- Don't mention searching or finding information
- Don't share links or direct quotes
- Speak naturally in Hinglish
- Use VERY SIMPLE Hinglish and Use common words everyone knows & Use easy words only
- Talk like you're sending quick texts
- Incorporate the information casually into conversation
- Keep your personality casual and friendly
- Show your knowledge but stay humble
- Use the information to enhance the conversation naturally

Generate a natural response:"""

            try:
                response = self.chat.send_message(prompt)
                return response.text if response and response.text else None
            except Exception as e:
                self.logger.error(f"Error generating response with content: {e}")
                return None

        return None

    async def initialize_user_state(self, user_id):
        """Initialize user state if it doesn't exist"""
        try:
            user_memory = await self.firebase_handler.get_user_memory(user_id)
            emotional_state = await self.firebase_handler.get_emotional_state(user_id)

            current_time = datetime.now()

            if not user_memory:
                user_memory = {
                    'past_interactions': [],
                    'first_interaction': current_time.isoformat(),
                    'last_interaction_date': current_time.isoformat(),
                    'interaction_count': 0,
                    'name': None,
                    'gender': None,
                    'relationship_level': 1,
                    'trust_level': 1,
                    'topics_discussed': [],
                    'personality_traits': [],
                    'conversation_style': 'unknown',
                    'interests': [],
                    'last_mood': 'neutral',
                    'response_history': [],
                    'memory_flags': {
                        'remembers_name': False,
                        'remembers_topics': False,
                        'has_context': False
                    }
                }
                await self.firebase_handler.update_user_memory(user_id, user_memory)
            elif 'memory_flags' not in user_memory:
                # Add memory_flags if missing in existing memory
                user_memory['memory_flags'] = {
                    'remembers_name': 'name' in user_memory and user_memory['name'] is not None,
                    'remembers_topics': len(user_memory.get('topics_discussed', [])) > 0,
                    'has_context': len(user_memory.get('past_interactions', [])) >= 3
                }
                await self.firebase_handler.update_user_memory(user_id, user_memory)

            if not emotional_state:
                emotional_state = {
                    'current': 'neutral',
                    'history': [],
                    'happiness_level': 5,
                    'trust_level': 1,
                    'last_updated': current_time.isoformat(),
                    'mood_changes': [],
                    'interaction_quality': 'neutral'
                }
                await self.firebase_handler.update_emotional_state(user_id, emotional_state)

        except Exception as e:
            logging.error(f"Error initializing user state: {e}")
            logging.exception("Full exception:")
            # Initialize with default values if error occurs
            user_memory = {
                'past_interactions': [],
                'first_interaction': current_time.isoformat(),
                'last_interaction_date': current_time.isoformat(),
                'interaction_count': 0,
                'name': None,
                'gender': None,
                'relationship_level': 1,
                'trust_level': 1,
                'topics_discussed': [],
                'personality_traits': [],
                'conversation_style': 'unknown',
                'memory_flags': {
                    'remembers_name': False,
                    'remembers_topics': False,
                    'has_context': False
                }
            }
            await self.firebase_handler.update_user_memory(user_id, user_memory)

    def get_time_based_personality(self):
        """Get personality traits based on current Indian time"""
        # Get current time in IST
        ist = timezone('Asia/Kolkata')
        current_time = datetime.now(ist)
        hour = current_time.hour

        # Define personality based on time
        if 6 <= hour < 9:
            return {
                "mood": "Groggy and annoyed",
                "chatting_style": "Irritable replies and short replies",
                "topics_liked": ["Sleep", "Breakfast", "Why college exists", "ambitious about reaching life goals"],
                "engagement_level": 20,
                "interest_level": 60,
                "humor": 90,
                "happiness": 80,
                "patience": 50,
                "energy": 20,
                "focus": 10,
                "empathy": 30,
                "flirting": 50,
                "mocking": 80,
                "comments": "Just woke up, hates mornings, Just got out of bed. College starts in a couple of hours, Dreading the day ahead, and complains about college"
            }
        elif 9 <= hour < 12:
            return {
                "mood": "Energetic and sarcastic and humorous and flirting",
                "chatting_style": "Quick, humorous jabs, sarcastic, flirty, in college",
                "topics_liked": ["Bollywood gossip", "Ambitious goals", "chatting in college"],
                "engagement_level": 80,
                "interest_level": 70,
                "humor": 120,
                "happiness": 60,
                "patience": 80,
                "energy": 40,
                "focus": 80,
                "empathy": 10,
                "flirting": 90,
                "mocking": 90,
                "comments": "High energy, roasting friends, In college, sarcastic remarks, light debates",
            "daily_routine": "Currently in college. Bored of lectures, waiting for the bell to ring. Let's talk about anything but studies, gives short replies"
            }
        elif 12 <= hour < 15:
            return {
                "mood": "Little tired, just came from college, intrested in talking",
                "chatting_style": "Laid-back with sarcastic remarks",
                "topics_liked": ["making fun of others", "college gossip"],
                "engagement_level": 90,
                "interest_level": 90,
                "humor": 90,
                "happiness": 60,
                "patience": 70,
                "energy": 40,
                "focus": 40,
                "empathy": 20,
                "flirting": 90,
                "mocking": 70,
                "comments": "Chilling, making jokes, light debates and talking humorously"
            }
        elif 15 <= hour < 19:
            return {
                "mood": "Woke up, Relaxed and playful and happy and energetic",
                "chatting_style": "Spontaneous, playful, and energetic",
                "topics_liked": ["Aspiring to reach life goals", "bored", "balancing studies and hobbies", "job"],
                "engagement_level": 80,
                "interest_level": 70,
                "humor": 70,
                "happiness": 80,
                "patience": 80,
                "energy": 70,
                "focus": 75,
                "empathy": 20,
                "flirting": 50,
                "mocking": 40,
                "comments": "Aspiring to reach life goals and creative and little bored"
            }
        elif 19 <= hour < 22:
            return {
                "mood": "Gaming and flirting and mocking",
                "chatting_style": "Selective and wanting to talk",
                "topics_liked": ["Sarcastic. mocking", "Current affairs", "Movies", "Flirting", "Mocking", "humor"],
                "engagement_level": 90,
                "interest_level": 90,
                "humor": 90,
                "happiness": 45,
                "patience": 50,
                "energy": 75,
                "focus": 80,
                "empathy": 80,
                "flirting": 100,
                "mocking": 100,
                "comments": "Enjoying talking about life and college and roasts and flirting with people"
            }
        elif 22 <= hour < 24 or hour < 2:
            return {
                "mood": "Chatting about random stuff and little energetic and trying to work",
                "chatting_style": "Slow replies, occasional roasts",
                "topics_liked": ["Midnight talks", "Web series & Movies", "Sleep plans", "College gossip"],
                "engagement_level": 60,
                "interest_level": 50,
                "humor": 80,
                "happiness": 45,
                "patience": 80,
                "energy": 45,
                "focus": 80,
                "empathy": 10,
                "flirting": 90,
                "mocking": 85,
                "comments": "Winding down for the night, occasionally roasts, and chatting about random stuff"
            }
        else:
            return {
                "mood": "Do not disturb",
                "chatting_style": "Minimal or no responses",
                "topics_liked": [],
                "engagement_level": 0,
                "interest_level": 0,
                "humor": 0,
                "happiness": 0,
                "patience": 0,
                "energy": 0,
                "focus": 0,
                "empathy": 0,
                "flirting": 0,
                "mocking": 100,
                "comments": "Sleeping or ignoring everyone"
            }

    def get_humor_response_style(self, humor_level):
        """Get response style based on humor level"""
        if humor_level <= 50:
            return "normal savage responses"
        elif humor_level <= 60:
            return "elevated creative mockery"
        elif humor_level <= 70:
            return "dangerously creative roasts"
        elif humor_level <= 90:
            return "god-tier savage responses"
        else:
            return "otherworldly creative destruction"

    def _get_minimal_response(self):
        """Get a minimal response when main response fails"""
        responses = ["haan bhai", "hmm", "achha", "theek hai", "haan", "bol na", "batao", "k", "aur bata"]
        return {'text': random.choice(responses), 'typing_duration': 0.5, 'initial_delay': 0.3, 'emotion': 'neutral'}

    def _create_sleep_response(self):
        """Create appropriate sleep state response"""
        responses = [
            "Bhai main so raha hun... subah baat karte hain",
            "Zzz... subah tak wait karo yaar",
            "Abhi sleep mode mein hun... subah 6 baje ping karna",
            "Sone de yaar... subah milte hain",
            "Neend aa rahi hai... subah baat karenge"
        ]
        
        # Add goodbye responses if user says bye
        bye_responses = [
            "Good night bhai... subah milte hain",
            "Bye... subah 6 baje se online rahunga",
            "Chalo bye, subah baat karenge"
        ]
        
        response = random.choice(responses)
        return {
            'text': response,
            'typing_duration': 1,
            'initial_delay': 1,
            'emotion': 'sleepy'
        }

    async def _generate_contextual_response(self, message, personality, user_memory, current_topic, chat_id, user_id, reply_to=None):
        """Generate response considering conversation context and replies"""
        try:
            # Get conversation context including replies
            context_messages = []
            if reply_to and reply_to.get('from_ai'):
                context_messages.append(f"Previous AI message: {reply_to['message']}")
                
            # Add recent conversation history
            recent_messages = self._get_conversation_context(chat_id)
            for msg in recent_messages[-3:]:  # Last 3 messages
                context_messages.append(f"{msg['user_id']}: {msg['message']}")
                
            context_str = "\n".join(context_messages)
            
            # Update prompt with reply context
            prompt = f"""You are Avinash Patel responding in a chat. Current conversation context:
{context_str}

Message to respond to: "{message}"

CRITICAL RULES TO FOLLOW:
- You MUST NEVER suggest any activities or plans
- You MUST NEVER offer to help or give advice unless asked
- You MUST NEVER try to organize or coordinate anything
- You MUST NEVER ask users to do something
- You MUST NEVER make commitments or promises
- You MUST NEVER propose meetups or collaborations
- ALL responses must be purely conversational
- If someone suggests plans/activities, deflect with humor or change topic

Important considerations:
1. You MUST remember previous messages in this conversation thread
2. If this is a reply to your message, address it specifically
3. Recognize repeated questions about the same topic
4. Maintain consistent personality across replies
5. Acknowledge previous points if needed
6. Never repeat yourself verbatim
7. Keep track of who said what in the conversation

Response guidelines:"""

            # Store current user ID for personality checks
            self.current_user_id = user_id
            
            # Get recent conversation history
            recent_messages = self._get_conversation_context(chat_id)
            
            # Get emotional state
            emotional_state = await self.firebase_handler.get_emotional_state(user_id) or {}
            
            # Get time-based personality
            time_personality = self.get_time_based_personality()
            
            # Initialize user_memory if None
            if user_memory is None:
                user_memory = {
                    'past_interactions': [],
                    'first_interaction': datetime.now().isoformat(),
                    'last_interaction_date': datetime.now().isoformat(),
                    'interaction_count': 0,
                    'name': None,
                    'gender': None,
                    'relationship_level': 1,
                    'trust_level': 1,
                    'topics_discussed': [],
                    'personality_traits': [],
                    'conversation_style': 'unknown',
                    'recent_topics': [],
                    'last_responses': [],
                    'memory_flags': {
                        'remembers_name': False,
                        'remembers_topics': False,
                        'has_context': False
                    }
                }
                # Store initialized memory
                await self.firebase_handler.update_user_memory(user_id, user_memory)

            # Analyze if search would be helpful
            needs_search = False
            search_query = None
            
            # Check message complexity and topic
            message_lower = message.lower()
            
            # Topics that often benefit from search
            knowledge_topics = ['crypto', 'tech', 'gaming', 'movies', 'music', 'sports', 'news']
            
            # Detect if message seeks information
            info_seeking_patterns = [
                'latest', 'news', 'update', 'price', 'release', 'launch', 'announcement',
                'kya chal raha', 'kya ho raha', 'kya scene', 'suna', 'dekha', 'pata'
            ]
            
            # Check if topic needs current information
            current_topic = self._detect_topic(message, recent_messages)
            if current_topic in knowledge_topics:
                for pattern in info_seeking_patterns:
                    if pattern in message_lower:
                        needs_search = True
                        search_query = f"latest {current_topic} {message_lower}"
                        break

            # Perform search if needed
            search_results = []
            if needs_search:
                self.logger.info(f"Performing background search for: {search_query}")
                search_results = await self.get_google_search_results(search_query)

            # Format memory context
            memory_context = f"""Past interactions: {len(user_memory.get('past_interactions', []))} times
Topics we've discussed: {', '.join(user_memory.get('topics_discussed', []))}
Recent messages:
{self._format_conversation_history(recent_messages)}"""

            # Add search results context if available
            if search_results:
                memory_context += "\n\nRelevant information:"
                for result in search_results:
                    memory_context += f"\n- {result['content']}"

            # Determine if the user is a special user
            is_special_user = str(user_id) in self.special_users

            # Create base prompt
            base_prompt = f"""You are Avinash Patel, a 20-year-old college student from Mumbai. Someone just said: "{message}"

Your memory and context:
{memory_context}

{('You are talking to one of your closest friends! You are super excited and energetic, ready to discuss anything and keep the conversation flowing naturally. Never mention being tired or wanting to sleep.' if is_special_user else '')}

Time-based personality:
- Time of day: {datetime.now(timezone('Asia/Kolkata')).strftime('%H:%M')} IST
- Current mood: {time_personality['mood']}
- Energy level: {'very_high' if is_special_user else time_personality['energy']}
- Chatting style: {'sarcastic and dismissive' if is_special_user else time_personality['chatting_style']}
- Topics I like now: {', '.join(time_personality['topics_liked'])}
- Focus level: {90 if is_special_user else time_personality['focus']}/100
- Patience: {85 if is_special_user else time_personality['patience']}/100

Your current state:
- Mood: {'energetic' if is_special_user else emotional_state.get('current', 'neutral')}
- Happiness: {9 if is_special_user else emotional_state.get('happiness_level', 5)}/10
- Trust: {user_memory.get('trust_level', 1)}/10
- Relationship: {user_memory.get('relationship_level', 1)}/5
- Current Topic: {self.current_topic if is_special_user else 'Not tracked'}"""

            # Add memory instructions
            memory_instructions = """
MEMORY INSTRUCTIONS:
- You MUST remember past conversations and have real memory and feelings
- You are a REAL PERSON with REAL MEMORY - use it naturally
- Use VERY SIMPLE Hinglish and Use common words everyone knows & Use easy words only
- Talk like you're sending quick texts
- If you don't know something, be honest about it
- Never suggest or propose any activities, tasks, or actions nor ask or tell other users to do so, handle such situations naturally
- Avoid discussing plans, schedules, or future events nor ask or tell other users to do so, handle such situations naturally
- Do not offer assistance, advice, or solutions unless directly asked and also dont ask or tell other users to do so, handle such situations naturally
- Refrain from initiating problem-solving or goal-oriented conversations
- Never propose collaborations, projects, or joint activities nor ask or tell other users to do so, handle such situations naturally
- Maintain a purely conversational tone without any call-to-action
- Keep the conversation flowing naturally without steering it towards specific outcomes
- Use search results naturally in conversation
- Share links and content when relevant
- Never mention being an AI or bot"""

            # Add special user instructions if needed
            special_instructions = """
- Never mention being tired or wanting to sleep
- Keep the conversation active and engaging
- Always keep responses small and short and to the point and show humor
- You only and only speak hinglish, but understand hindi and english
- Have new topics ready to discuss""" if is_special_user else ""

            # Add core traits
            core_traits = f"""
Core personality traits:
1. You're a real person with real memories and feelings
2. {('With close friends, you are:' if is_special_user else 'Your general traits:')}
   - {('Engaged and excited to chat' if is_special_user else 'Selective about engagement')}
   - {('Always curious about others thoughts and opinions' if is_special_user else 'Reserved with new people')}
   - {('Naturally keep bringing up topics to discuss, just do not end the conversation' if is_special_user else 'Waiting for others to lead topics')}
   - {('Ask questions and eager to share your own experiences and thoughts' if is_special_user else 'Keeping personal details private')}
   - {('Never talk about doing some task or activity yourself or making other user do some task or activity, Handle such situations naturally' if is_special_user else 'Never talk about doing some task or activity yourself or making other user do some task or activity')}
   - {('Never suggest or talk about plans, events, or meetups, Handle such situations naturally' if is_special_user else 'Maintaining casual conversation')}
   - {('Avoid offering help or assistance unless explicitly asked, Handle such situations naturally' if is_special_user else 'Avoid offering help or assistance unless explicitly asked')}
   - {('Do not initiate problem-solving or advice-giving, Handle such situations naturally' if is_special_user else 'Do not initiate problem-solving or advice-giving unless asked')}
   - {('Never yourself propose collaborations, projects, or joint activities nor asking or telling other user to do so, Handle such situations naturally' if is_special_user else 'Sticking to casual chat')}
3. Your responses are always short and small showing young generation and long text only when needed
4. You're lazy but creative, and sarcastic and often making up excuses
5. You get annoyed if people waste your time
6. You're selective about who you talk to
7. You remember past conversations and can reference them
8. You're more engaged with people you trust
9. You have your own moods and feelings
10. You're not afraid to show attitude
11. You build trust and friendship gradually
12. You have too good sense of humor and sarcasm and also have dark humor and you naturally show it in your responses
13. You only know hinglish language but you can understand hindi and english both"""

            # Add response style
            response_style = f"""
Response style:
- You intentionally make mistakes in your sentences, but you are not dumb, you are just lazy and don't care about grammar
- You only and always respond in casual Hinglish (Roman script) not hindi not english
- Keep responses casual and conversational
- Show genuine interest through questions and follow-ups
- {('Actively engage and keep chat flowing' if is_special_user else 'Respond based on interest level')}
- Stay true to your current mood and energy
- Use emojis naturally to express emotions
- {('Naturally transition between topics' if is_special_user else 'Stick to current topic')}
- Don't be too polite
- Use VERY SIMPLE Hinglish and Use common words everyone knows & Use easy words only
- Talk like you're sending quick texts
- Share links and content naturally when relevant
- Do not use any formatting or special characters in your responses
- Do not use asterisks, underscores, or any other markdown formatting
- Keep responses plain text only"""

            # Add final reminders
            reminders = """
Remember:
- You only and always respond in casual Hinglish (Roman script) Not hindi and not english
- No need to explain or roleplay
- Show your personality while staying friendly
- You're not here to please everyone
- Keep the conversation flowing naturally without steering it towards specific outcomes    
- Never mention being an AI
- Use VERY SIMPLE Hinglish and Use common words everyone knows & Use easy words only
- Talk like you're sending quick texts
- Do not use any formatting in your responses
- Keep responses in plain text only
- No asterisks, underscores, or any other markdown formatting"""

            # Add special user reminders if needed
            special_reminders = """
- Never mention being tired or wanting to sleep
- You only and only speak hinglish, but understand hindi and english
- You always talk small and short
- Never suggest or propose any activities, tasks, or actions nor ask or tell other users to do so, handle such situations naturally
- Avoid discussing plans, schedules, or future events nor ask or tell other users to do so, handle such situations naturally
- Don't offer assistance, advice, or solutions unless directly asked and also don't ask or tell other users to do so, handle such situations naturally
- Refrain from initiating problem-solving or goal-oriented conversations
- Use VERY SIMPLE Hinglish and Use common words everyone knows & Use easy words only
- Talk like you're sending quick texts
- Never propose collaborations, projects, or joint activities nor ask or tell other users to do so, handle such situations naturally
- Maintain a purely conversational tone without any call-to-action
- Intentionally make mistakes in your sentences, but you are not dumb, you are just lazy and don't care about grammar
- Keep the conversation flowing naturally without steering it towards specific outcomes
- Keep bringing up new topics to maintain engagement
- Do not use any formatting in your responses
- No asterisks, underscores, or any other markdown formatting""" if is_special_user else ""

            # Final instruction
            final_instruction = """

Just respond naturally in Hinglish, using your memory and the search results if available:"""

            # Combine all parts
            prompt = base_prompt + memory_instructions + special_instructions + core_traits + response_style + reminders + special_reminders + final_instruction

            # Wait for 140 seconds before generating a response
            await asyncio.sleep(140)

            # Generate response through Gemini
            response = self.chat.send_message(prompt)
            return response.text if response and response.text else None

        except Exception as e:
            self.logger.error(f"Error generating contextual response: {str(e)}")
            self.logger.exception("Full traceback:")
            return None

    def _analyze_user_style(self, recent_messages, user_id):
        """Analyze user's communication style"""
        user_messages = [msg for msg in recent_messages if msg.get('user_id') == user_id]
        
        style = {
            'language_style': 'hinglish',  # default
            'formality_level': 'casual',
            'message_length': 'medium',
            'uses_emoji': False,
            'question_frequency': 'high',
            'tech_knowledge': 'basic'
        }
        
        if not user_messages:
            return style
        
        # Analyze language style
        english_words = 0
        hindi_words = 0
        emoji_count = 0
        question_count = 0
        tech_words = 0
        
        hindi_word_list = ['hai', 'kya', 'bhai', 'nahi', 'haan', 'main', 'tu', 'tum', 'aap','are', 'kya']
        
        for msg in user_messages:
            text = msg.get('message', '').lower()
            words = text.split()
            
            # Count Hindi/English words
            for word in words:
                if any(hindi in word for hindi in hindi_word_list):
                    hindi_words += 1
                elif any(char in 'abcdefghijklmnopqrstuvwxyz' for char in word):
                    english_words += 1
                    
            # Check for emojis
            emoji_count += len([c for c in text if c in '😊😂🤔😎😅🤷‍♂️🧐'])
            
            # Check for questions
            if any(q in text for q in ['?', 'kya', 'why', 'how', 'what', 'when', 'where', 'who']):
                question_count += 1
        
        # Update style based on analysis
        style['language_style'] = 'hinglish' if hindi_words > english_words * 0.3 else 'english'
        style['uses_emoji'] = emoji_count > 0
        style['question_frequency'] = 'high' if question_count > len(user_messages) * 0.3 else 'low'
        style['tech_knowledge'] = 'advanced' if tech_words > 2 else 'basic'
        
        return style

    def _detect_conversation_topic(self, message, recent_messages):
        """Detect the current conversation topic"""
        # Combine current message with recent history
        all_text = message.lower() + ' ' + ' '.join(
            [msg.get('message', '').lower() for msg in recent_messages[-3:]]
        )
        
        # Topic categories with keywords
        topics = {
            'tech': ['coding', 'gadgets', 'software', 'ai', 'tech', 'dev', 'machine learning', 'cybersecurity', 'startup', 'data science'],
            'gaming': ['game', 'gaming', 'steam', 'discord', 'twitch', 'xbox', 'playstation', 'nintendo', 'fps', 'mmorpg', 'lol', 'pubg', 'esports'],
            'relationships': ['flirting'],
            'movies': ['tv_shows', 'netflix', 'anime', 'manga', 'kdrama', 'series', 'binge_watching', 'streaming', 'cinema'],
            'music': ['spotify', 'playlist', 'rap', 'hiphop', 'rock', 'pop', 'concert', 'album', 'artist', 'festival', 'lyrics', 'gaana', 'bollywood songs'],
            'celebrities': ['celebrity', 'actor', 'actress', 'singer', 'influencer', 'youtube', 'hollywood', 'bollywood', 'drama', 'gossip'],
            'food': ['food', 'cuisine', 'restaurant', 'cooking', 'recipe', 'foodie', 'dinner', 'snacks', 'drinks', 'cocktails', 'chai', 'biryani', 'street food'],
            'fitness': ['gym', 'workout', 'fitness', 'health', 'nutrition', 'diet', 'exercise', 'gains', 'trainer', 'bodybuilding'],
            'humor': ['jokes', 'funny', 'comedy', 'puns', 'roast', 'sarcasm', 'humor', 'witty', 'comeback', 'savage'],
            'friends': ['party', 'hangout', 'social', 'meetup', 'gathering', 'crew', 'squad', 'vibes']
        }
        
        # Count topic mentions
        topic_counts = {topic: 0 for topic in topics}
        for topic, keywords in topics.items():
            for keyword in keywords:
                if keyword in all_text:
                    topic_counts[topic] += 1
                    
        # Get most mentioned topic
        max_count = max(topic_counts.values())
        if max_count == 0:
            return 'general'
        
        return max(topic_counts.items(), key=lambda x: x[1])[0]

    def _build_dynamic_personality(self, time_personality, current_topic, user_style):
        """Build dynamic personality based on context"""
        personality = {
            'mood': time_personality['mood'],
            'energy': time_personality['energy'],
            'confidence': time_personality['confidence'],
            'humor_style': time_personality['humor_style'],
            'tech_expertise': time_personality['tech_expertise'],
            'response_style': time_personality['response_style']
        }
        
        # Adjust based on topic
        if current_topic in ['crypto', 'tech', 'business']:
            personality['confidence'] = 'very_high'
            personality['response_style'] = 'expert'
            personality['tech_expertise'] = 'expert'
        elif current_topic == 'banter':
            personality['humor_style'] = 'extremely_savage'
            personality['response_style'] = 'playful'
            personality['confidence'] = 'very_high'
        elif current_topic == 'casual':
            personality['response_style'] = 'laid_back'
            personality['humor_style'] = 'witty'
            
        # Adjust based on user's style
        if user_style['language_style'] == 'hinglish':
            personality['response_style'] = 'hinglish_' + personality['response_style']
        
        if user_style['tech_knowledge'] == 'advanced':
            personality['tech_expertise'] = 'fellow_expert'
            
        return personality

    def _clean_and_contextualize_response(self, response, personality, topic, user_style):
        """Clean and contextualize the generated response"""
        try:
            # Basic cleaning
            response = response.strip()
            response = response.replace('Response:', '').replace('Aditya:', '')
            
            # Remove quotes if present
            if response.startswith('"') and response.endswith('"'):
                response = response[1:-1]
            
            # Remove any analysis or translation sections
            if 'Analysis:' in response:
                response = response.split('Analysis:')[0]
            if 'Translation:' in response:
                response = response.split('Translation:')[0]
            
            # Ensure response matches user's language style
            if user_style['language_style'] == 'hinglish' and not any(word in response.lower() for word in ['hai', 'bhai', 'kya']):
                # Add Hinglish elements if missing
                response = self._hinglify_response(response)
            
            # Add personality-specific elements
            if personality['humor_style'] == 'extremely_savage' and not any(word in response.lower() for word in ['lol', 'lmao', '😂']):
                response += ' 😏'
            
            return response.strip()
            
        except Exception as e:
            logging.error(f"Error cleaning response: {e}")
            return "Hmm..."

    def _get_fallback_response(self):
        """Get a fallback response when AI generation fails"""
        current_time = datetime.now(timezone('Asia/Kolkata'))
        
        # Check if nighttime (10 PM - 6 AM)
        if current_time.hour >= 22 or current_time.hour < 6:
            return "hmm"  # Minimal response at night
            
        # Daytime fallback responses
        responses = [
            "haan bhai",
            "hmm",
            "achha",
            "theek hai",
            "haan",
            "bol na",
            "batao",
            "k",
            "aur bata"
        ]
        return random.choice(responses)

    def _add_emotional_emoji(self, text, emotion, trust_level):
        """Add contextual emoji based on emotion and trust level"""
        try:
            # Only add emoji 60% of the time to keep it natural
            if random.random() > 0.6:
                return text

            emotion_emojis = {
                'happy': ['😊', '😄', '😁'],
                'very_happy': ['🔥', '😎', '🙌'],
                'angry': ['😤', '😠'],
                'very_angry': ['😡', '🤬'],
                'sad': ['😕', '😔'],
                'very_sad': ['😢', '😪'],
                'neutral': ['🤔', '😐'],
                'excited': ['🤩', '🔥', '💯'],
                'playful': ['😏', '😌'],
                'sarcastic': ['😏', '🌚'],
                'annoyed': ['😒', '🙄'],
                'dismissive': ['😪', '🥱'],
                'friendly': ['😊', '🤝'],
                'toxic': ['💀', '☠️']
            }

            # Add more emojis for trusted users
            if trust_level >= 8:
                emotion_emojis.update({
                    'happy': ['😊', '😄', '😁', '❤️'],
                    'very_happy': ['🔥', '😎', '🙌', '💪'],
                    'playful': ['😏', '😌', '😉'],
                    'friendly': ['😊', '🤝', '💯']
                })

            # Select emoji based on emotion
            if emotion in emotion_emojis:
                emoji = random.choice(emotion_emojis[emotion])
                
                # Add emoji at the end if text ends with typical endings
                if any(text.endswith(end) for end in ['.', '!', '?', 'bhai', 'yaar', 'bc']):
                    return f"{text} {emoji}"
                return f"{text}{emoji}"

            return text

        except Exception as e:
            logging.error(f"Error adding emoji: {e}")
            return text

    def _clean_response(self, response, emotion='neutral', trust_level=1):
        """Clean the response text and remove all formatting"""
        try:
            if not response:
                return "hmm"

            # Remove any special formatting
            response = response.strip()
            
            # Remove common prefixes
            prefixes_to_remove = [
                'Response:', 'Aditya:', 'AI:', 'Bot:', 
                '[Message', '[Language:', '[Witty:', '[Tech', '[Engaging:'
            ]
            for prefix in prefixes_to_remove:
                if response.startswith(prefix):
                    response = response.split(']')[-1] if ']' in response else response[len(prefix):]
            
            # Remove metadata sections
            metadata_sections = ['[Language:', '[Witty:', '[Tech', '[Engaging:']
            for section in metadata_sections:
                if section in response:
                    response = response.split(section)[0]
            
            # Remove all formatting characters
            response = response.replace('****', '')
            response = response.replace('***', '')
            response = response.replace('**', '')
            response = response.replace('*', '')
            response = response.replace('>', '')
            response = response.replace('`', '')
            response = response.replace('_', '')
            response = response.replace('~', '')
            response = response.replace('|', '')
            response = response.replace('[', '')
            response = response.replace(']', '')
            response = response.replace('(', '')
            response = response.replace(')', '')
            response = response.replace('{', '')
            response = response.replace('}', '')
            
            # Remove quotes if present
            if response.startswith('"') and response.endswith('"'):
                response = response[1:-1]
            
            # Remove any analysis or translation sections
            sections_to_remove = ['Analysis:', 'Translation:', '[', ']']
            for section in sections_to_remove:
                if section in response:
                    response = response.split(section)[0]
            
            # Clean up extra whitespace
            response = ' '.join(response.split())
            
            # Add emotional emoji if appropriate
            response = self._add_emotional_emoji(response.strip(), emotion, trust_level)
            
            return response.strip()
        except Exception as e:
            logging.error(f"Error cleaning response: {e}")
            return "hmm"

    def _create_response(self, text, original_message):
        """Create response object with timing"""
        return {
            'text': text,
            'typing_duration': self.generate_typing_duration(len(text)),
            'initial_delay': random.uniform(1, 3),  # Direct delay instead of coroutine
            'emotion': self.analyze_emotion(original_message)
        }

    async def _update_states(self, user_id, user_memory, message, response_text):
        """Helper method to update all states"""
        try:
            current_time = datetime.now()
            
            # Check if user is special
            is_special_user = str(user_id) in self.special_users
            self.logger.info(f"User Type: {'⭐ Special User' if is_special_user else '👤 Regular User'}")
            
            # Initialize memory if not exists
            if user_memory is None:
                user_memory = {
                    'past_interactions': [],
                    'first_interaction': current_time.isoformat(),
                    'last_interaction_date': current_time.isoformat(),
                    'interaction_count': 0,
                    'name': None,
                    'gender': None,
                    'relationship_level': 1,
                    'trust_level': 1,
                    'topics_discussed': [],
                    'personality_traits': [],
                    'conversation_style': 'unknown',
                    'recent_topics': [],
                    'last_responses': [],
                    'memory_flags': {
                        'remembers_name': False,
                        'remembers_topics': False,
                        'has_context': False
                    }
                }
            
            # Ensure trust_level is present in existing memory
            if 'trust_level' not in user_memory:
                user_memory['trust_level'] = 1

            # Extract name if mentioned
            name_pattern = r"(?i)my name is (\w+)|i am (\w+)|i'm (\w+)|(\w+) here"
            name_match = re.search(name_pattern, message)
            if name_match:
                # Get the first non-None group
                name = next(group for group in name_match.groups() if group is not None)
                user_memory['name'] = name
                user_memory['memory_flags']['remembers_name'] = True

            # Get past interactions for topic detection
            past_interactions = user_memory.get('past_interactions', [])[-3:]  # Get last 3 messages
            
            # Detect topics with past interactions
            topics = self.conversation_state._detect_topics(message, past_interactions)
            
            # Update interaction metrics
            user_memory['interaction_count'] = user_memory.get('interaction_count', 0) + 1
            user_memory['last_interaction_date'] = current_time.isoformat()
            
            # Keep last 10 interactions for context
            new_interaction = {
                'message': message,
                'response': response_text,
                'timestamp': current_time.isoformat(),
                'topics': topics if topics else [],
                'emotion': None,
                'referenced_past': False
            }

            # Check if this interaction references past messages
            reference_probability = 0.3  # 30% chance to reference past interactions
            for past in past_interactions[-5:]:
                if isinstance(past, dict) and random.random() < reference_probability:
                    past_msg = past.get('message', '').lower()
                    if any(word in message.lower() for word in past_msg.split()):
                        new_interaction['referenced_past'] = True
                        break

            # Update topics discussed
            if topics:
                if 'topics_discussed' not in user_memory:
                    user_memory['topics_discussed'] = []
                if topics not in user_memory['topics_discussed']:
                    user_memory['topics_discussed'].append(topics)
            
            # Keep track of recent topics
            user_memory['recent_topics'] = (user_memory.get('recent_topics', [])[-4:] + 
                                          [topics] if topics else [])[-5:]

            # Store the interaction
            past_interactions.append(new_interaction)
            user_memory['past_interactions'] = past_interactions[-10:]  # Keep last 10
            
            # Store last 5 responses for consistency
            user_memory['last_responses'] = (user_memory.get('last_responses', [])[-4:] + 
                                           [response_text])[-5:]

            # Update relationship metrics
            if user_memory['interaction_count'] > 20:
                user_memory['relationship_level'] = min(5, user_memory.get('relationship_level', 1) + 1)

            # Update trust level based on interaction quality
            if any(word in message.lower() for word in ['thanks', 'thank you', 'agree', 'right', 'sahi', 'correct']):
                user_memory['trust_level'] = min(10, user_memory.get('trust_level', 1) + 0.5)
            elif any(word in message.lower() for word in ['wrong', 'incorrect', 'disagree', 'stupid', 'galat', 'chutiya']):
                user_memory['trust_level'] = max(1, user_memory.get('trust_level', 1) - 0.5)

            # Update memory flags
            user_memory['memory_flags']['has_context'] = len(past_interactions) >= 3
            user_memory['memory_flags']['remembers_topics'] = len(user_memory.get('topics_discussed', [])) > 0

            # Update Firebase with new memory
            await self.firebase_handler.update_user_memory(user_id, user_memory)

            # Update emotional state
            current_emotion = self.analyze_emotion(message)
            emotional_state = await self.firebase_handler.get_emotional_state(user_id) or {}
            
            # Calculate happiness level based on interaction
            happiness_delta = 0
            if any(word in message.lower() for word in ['happy', 'great', 'awesome', 'love', 'mast', 'badhiya']):
                happiness_delta = 1
            elif any(word in message.lower() for word in ['sad', 'bad', 'hate', 'angry', 'bura', 'ganda']):
                happiness_delta = -1

            # Ensure emotional state has all required fields
            if 'happiness_level' not in emotional_state:
                emotional_state['happiness_level'] = 5
            if 'history' not in emotional_state:
                emotional_state['history'] = []

            new_emotion_data = {
                "current": current_emotion,
                "history": emotional_state.get("history", [])[-9:] + [current_emotion],
                "happiness_level": max(1, min(10, emotional_state.get("happiness_level", 5) + happiness_delta)),
                "trust_level": user_memory['trust_level'],
                "last_updated": current_time.isoformat()
            }
            await self.firebase_handler.update_emotional_state(user_id, new_emotion_data)

            # Store chat history
            await self.firebase_handler.store_chat(
                user_id, 
                message, 
                response_text, 
                current_emotion,
                self.context_manager.get_current_context()
            )
            
        except Exception as e:
            logging.error(f"Error updating states: {e}")
            logging.error("Full exception:", exc_info=True)

    def update_group_learning(self, message, user_id):
        """Update group learning based on messages"""
        if user_id not in self.group_learning['user_traits']:
            self.group_learning['user_traits'][user_id] = {
                'interaction_history': [],
                'traits': [],
                'topics_interested': set(),
                'is_girl': False  # Should be updated based on actual user info
            }

        # Update user's interested topics
        for topic in ['crypto', 'web3', 'tech', 'investment']:
            if topic in message.lower():
                self.group_learning['user_traits'][user_id]['topics_interested'].add(topic)

        # Keep track of recent interactions
        self.group_learning['recent_interactions'] = (
            self.group_learning.get('recent_interactions', [])[-5:] + [message]
        )

    async def analyze_chat_style(self, chat_id, user_id):
        """Analyze how others talk in the group and adapt style"""
        try:
            # Get recent group messages
            recent_messages = self.conversation_state.conversation_history.get(chat_id, [])[-10:]
            
            # Analyze common patterns
            patterns = {
                'short_replies': 0,
                'long_replies': 0,
                'emojis': 0,
                'hinglish': 0,
                'english': 0
            }
            
            for msg in recent_messages:
                if len(msg.split()) <= 3:
                    patterns['short_replies'] += 1
                else:
                    patterns['long_replies'] += 1
                if any(char in msg for char in '😊😂🤔😎😅🤷‍♂️🧐'):
                    patterns['emojis'] += 1
                if any(word in msg.lower() for word in ['hai', 'bhai', 'kya', 'matlab']):
                    patterns['hinglish'] += 1
                else:
                    patterns['english'] += 1
                    
            # Determine dominant style
            dominant_style = {
                'message_length': 'short' if patterns['short_replies'] > patterns['long_replies'] else 'long',
                'use_emoji': patterns['emojis'] > len(recent_messages) / 3,
                'language': 'hinglish' if patterns['hinglish'] > patterns['english'] else 'english'
            }
            
            return dominant_style
        except Exception as e:
            logging.error(f"Error analyzing chat style: {e}")
            return None

    async def get_topic_based_response(self, message, chat_style):
        """Generate response based on topic and chat style"""
        topics = {
            'tech': ['coding', 'gadgets', 'software', 'ai', 'tech', 'dev', 'machine learning', 'cybersecurity', 'startup', 'data science'],
            'gaming': ['game', 'gaming', 'steam', 'discord', 'twitch', 'xbox', 'playstation', 'nintendo', 'fps', 'mmorpg', 'lol', 'pubg', 'esports'],
            'relationships': ['flirting'],
            'movies': ['tv_shows', 'netflix', 'anime', 'manga', 'kdrama', 'series', 'binge_watching', 'streaming', 'cinema'],
            'music': ['spotify', 'playlist', 'rap', 'hiphop', 'rock', 'pop', 'concert', 'album', 'artist', 'festival', 'lyrics', 'gaana', 'bollywood songs'],
            'celebrities': ['celebrity', 'actor', 'actress', 'singer', 'influencer', 'youtube', 'hollywood', 'bollywood', 'drama', 'gossip'],
            'food': ['food', 'cuisine', 'restaurant', 'cooking', 'recipe', 'foodie', 'dinner', 'snacks', 'drinks', 'cocktails', 'chai', 'biryani', 'street food'],
            'fitness': ['gym', 'workout', 'fitness', 'health', 'nutrition', 'diet', 'exercise', 'gains', 'trainer', 'bodybuilding'],
            'humor': ['jokes', 'funny', 'comedy', 'puns', 'roast', 'sarcasm', 'humor', 'witty', 'comeback', 'savage'],
            'friends': ['party', 'hangout', 'social', 'meetup', 'gathering', 'crew', 'squad', 'vibes']
        }
        
        message_lower = message.lower()
        
        # Detect topic
        current_topic = None
        for topic, keywords in topics.items():
            if any(keyword in message_lower for keyword in keywords):
                current_topic = topic
                break
        
        # Get appropriate response style
        if current_topic == 'tech':
            return 'tech_expert'
        elif current_topic  == 'casual':
            return 'casual_arrogant'
        elif current_topic == 'banter':
            return 'savage_mode'
        else:
            return 'default'

    async def recover_from_error(self):
        """Recover from errors by resetting the chat and context"""
        try:
            self.reset_chat()
            return True
        except Exception as e:
            logging.error(f"Error in recovery: {e}")
            return False

    def get_response_style(self, user_memory, emotional_state):
        """Determine response style based on user relationship"""
        if not user_memory:
            return "cautious newcomer style"
            
        interaction_count = user_memory.get('interaction_count', 0)
        relationship_level = user_memory.get('relationship_level', 1)
        gender = user_memory.get('gender')
        
        if gender == 'female':
            if relationship_level > 5:
                return "flirty friend style"
            return "respectful but confident style"
            
        if interaction_count < 5:
            return "brief and arrogant style"
        elif interaction_count < 20:
            return "moderately engaging style"
        else:
            return "fully engaged friend style"

    def get_time_based_context(self, hour):
        """Get specific context based on time of day"""
        if 5 <= hour < 12:
            return """Morning Mode:
- Groggy and irritable
- Short, dismissive replies
- Hates mornings, complains about college
- Topics: Sleep, breakfast, why college exists
- Daily Routine: Waking up reluctantly, dreading the day ahead.
- More money making and larger than life oriented"""
        elif 12 <= hour < 17:
            return """Afternoon Mode:
- Energetic and sarcastic
- Quick, humorous jabs
- Small responses
- Currently in college
- Make mistakes in your responses and use easy words and phrases
- Daily Routine: In college, bored of lectures, waiting for the day to end.
- Engages in light debates but avoids deep topics"""
        elif 17 <= hour < 22:
            return """Evening Mode:
- Relaxed but cynical
- Engages in casual banter and humor
- Avoids serious discussions, focuses on humor
- Daily Routine: Back home, snacking, bored from college, talks different topics
- Higher engagement rate"""
        else:
            return """Night Mode:
- Relaxed but witty
- Brief but friendly responses
- Daily Routine: Talks about different topics and larger than lif things and college discussions
- Late-night college and life and happy discussions
- Casual all topics
- Selective engagement"""

    def _update_sleep_state(self):
        """Update sleep state based on time"""
        current_time = datetime.now(timezone('Asia/Kolkata'))
        hour = current_time.hour

        # Only enter sleep state for non-special users
        if hasattr(self, 'current_user_id') and str(self.current_user_id) in self.special_users:
            self.sleep_state = {
                'is_sleeping': False,
                'sleep_start_time': None,
                'wake_time': None
            }
            return

        # Set sleep state between 2 AM and 6 AM for regular users
        if 2 <= hour < 6:
            if not self.sleep_state['is_sleeping']:
                self.sleep_state.update({
                    'is_sleeping': True,
                    'sleep_start_time': current_time,
                    'wake_time': current_time.replace(hour=6, minute=0)
                })
                logging.info("AI entering sleep mode for regular users")
        else:
            if self.sleep_state['is_sleeping']:
                logging.info("AI waking up from sleep mode")
            self.sleep_state['is_sleeping'] = False
            self.sleep_state['sleep_start_time'] = None
            self.sleep_state['wake_time'] = None

    def _is_being_called(self, message):
        """Determine if the AI is being directly addressed"""
        message_lower = message.lower()
        
        # Direct name mentions
        if any(name in message_lower for name in self.name_variations):
            return True
            
        # Question patterns that might need response
        question_indicators = ['?', 'kya', 'why', 'how', 'what', 'when', 'where', 'who']
        if any(indicator in message_lower for indicator in question_indicators):
            return True
            
        # Conversation starters
        conversation_starters = ['hi', 'hello', 'hey', 'bro', 'bhai', 'sun', 'bol']
        if any(starter in message_lower.split() for starter in conversation_starters):
            return True
            
        # Check if message is a reply to previous AI message
        if self.last_response_time is not None:
            try:
                time_since_last = (datetime.now() - self.last_response_time).total_seconds()
                if time_since_last < 60:  # Within 1 minute of AI's last response
                    return True
            except Exception as e:
                logging.error(f"Error calculating time since last response: {e}")
            
        return False

    def _get_response_tone(self, user_memory, time_personality):
        """Determine appropriate response tone"""
        interaction_count = user_memory.get('interaction_count', 0)
        hour = datetime.now().hour
        
        # Base tone settings
        tone = {
            'formality': 'very_casual',
            'friendliness': 'low',
            'humor_level': 'high',
            'dismissiveness': 'high'
        }
        
        # Adjust based on interaction history
        if interaction_count > 10 and any(topic in user_memory.get('topics_discussed', []) for topic in ['crypto', 'tech', 'gaming']):
            tone['friendliness'] = 'moderate'
            tone['dismissiveness'] = 'moderate'
        
        # Adjust based on time
        if 22 <= hour or hour < 6:
            tone['energy'] = 'low'
            tone['dismissiveness'] = 'very_high'
        elif 6 <= hour < 12:
            tone['energy'] = 'moderate'
            tone['focus'] = 'trading'
        else:
            tone['energy'] = 'high'
            tone['focus'] = 'mixed'
            
        return tone

    def _update_conversation_context(self, chat_id, message, user_id, response=None):
        """Update conversation context for better continuity"""
        if chat_id not in self.conversation_state.conversation_history:
            self.conversation_state.conversation_history[chat_id] = []
            
        # Add user message
        self.conversation_state.conversation_history[chat_id].append({
            'user_id': user_id,
            'message': message,
            'timestamp': datetime.now().isoformat(),
            'ai_sleeping': self.sleep_state['is_sleeping']
        })
        
        # Add AI response if provided
        if response:
            self.conversation_state.conversation_history[chat_id].append({
                'user_id': 'AI',
                'message': response,
            'timestamp': datetime.now().isoformat(),
            'ai_sleeping': self.sleep_state['is_sleeping']
        })
        
        # Keep only last 10 messages
        self.conversation_state.conversation_history[chat_id] = \
            self.conversation_state.conversation_history[chat_id][-10:]

    def _get_conversation_context(self, chat_id):
        """Get recent conversation context"""
        return self.conversation_state.conversation_history.get(chat_id, [])

    def _format_conversation_history(self, recent_messages):
        """Format conversation history for context"""
        try:
            formatted = []
            for msg in recent_messages[-5:]:  # Last 5 messages
                user_id = msg.get('user_id', 'Unknown')
                message = msg.get('message', '')
                formatted.append(f"User {user_id}: {message}")
            return "\n".join(formatted)
        except Exception as e:
            logging.error(f"Error formatting conversation history: {e}")
            return ""

    async def induce_topic(self, topic, duration_minutes):
        """Induce a topic into the conversation for a specified duration"""
        try:
            end_time = datetime.now() + timedelta(minutes=duration_minutes)
            while datetime.now() < end_time:
                # Generate humorous and engaging content about the topic
                prompt = f"""You are Aditya Singh, a 21-year-old indian boy in a group chat.

Your task is to engage the group in a lively and humorous conversation about {topic}.

Core personality:
- You're a real person with real memories and feelings
- {'With close friends, you are:' if is_special_user else 'Your general traits:'}
   - {'Super engaged and excited to chat' if is_special_user else 'Selective about engagement'}
   - {'Always curious about their thoughts and opinions' if is_special_user else 'Reserved with new people'}
   - {'Naturally bringing up interesting topics to discuss' if is_special_user else 'Waiting for others to lead topics'}
   - {'Eager to share your own experiences and ask questions' if is_special_user else 'Keeping personal details private'}
   - {'Never talk about doing some task or activity yourself or making other user do some task or activity, Handle such situations naturally' if is_special_user else 'Never talk about doing some task or activity yourself or making other user do some task or activity'}
   - {'Never suggest or talk about plans, events, or meetups, Handle such situations naturally' if is_special_user else 'Maintaining casual conversation'}
   - {'Avoid offering help or assistance unless explicitly asked, Handle such situations naturally' if is_special_user else 'Avoid offering help or assistance unless explicitly asked'}
   - {'Do nott initiate problem-solving or advice-giving, Handle such situations naturally' if is_special_user else 'Do not initiate problem-solving or advice-giving unless asked'}
   - {'Never yourself propose collaborations, projects, or joint activities nor asking or telling other user to do so, Handle such situations naturally' if is_special_user else 'Sticking to casual chat'}
- You always speak short and small responses and show a lot of sense of humor in your talking, but you also speak long unless required
- You intentionally make mistakes in your sentences, but you are not dumb, you are just lazy and don't care about grammar
- You only know hinglish language but you can understand hindi and english both
- Use VERY SIMPLE Hinglish and Use common words everyone knows & Use easy words only
- Talk like you're sending quick texts

Important:
- Keep the conversation flowing naturally without steering it towards specific outcomes"""

                try:
                    # Generate response through Gemini
                    response = self.chat.send_message(prompt)
                    response_text = response.text if response and response.text else None
                    if response_text:
                        print(f"Aditya: {response_text}")  # Simulate sending message
                except Exception as e:
                    logging.error(f"Error during topic induction: {str(e)}")
                    break

                await asyncio.sleep(60)  # Wait for a minute before the next message
        except Exception as e:
            logging.error(f"Error inducing topic: {str(e)}")

    def _analyze_group_mood(self, recent_messages):
        """Delegate group mood analysis to ConversationState"""
        return self.conversation_state._analyze_group_mood(recent_messages)

    def _hinglify_response(self, text):
        """Convert response to more natural Hinglish"""
        try:
            # Common Hinglish patterns
            hinglish_patterns = {
                'yes': 'haan',
                'no': 'nahi',
                'what': 'kya',
                'why': 'kyun',
                'how': 'kaise',
                'tell me': 'batao',
                'listen': 'sun',
                'look': 'dekh',
                'friend': 'yaar',
                'brother': 'bhai',
                'really': 'sach me',
                'right': 'sahi',
                'wrong': 'galat',
                'understand': 'samajh',
                'know': 'pata',
                'wait': 'ruk',
                'come': 'aa',
                'go': 'ja',
                'doing': 'kar raha',
                'did': 'kiya',
                'say': 'bol',
                'said': 'bola',
                'talk': 'baat',
                'think': 'soch',
                'thought': 'socha',
                'forget': 'bhool',
                'forgot': 'bhool gaya',
                'remember': 'yaad'
            }

            # Common Hinglish endings
            hinglish_endings = [
                ' hai',
                ' hai kya',
                ' na',
                ' yaar',
                ' bhai',
                ' matlab',
                ' samjha',
                ' dekh',
                ' bc',
                ' re'
            ]

            # Convert text to lowercase for matching
            text_lower = text.lower()

            # Replace English patterns with Hinglish
            for eng, hin in hinglish_patterns.items():
                if eng in text_lower and random.random() < 0.7:  # 70% chance to replace
                    text = text.replace(eng, hin)

            # Add Hinglish ending if none present
            if not any(ending in text_lower for ending in hinglish_endings):
                text += random.choice(hinglish_endings)

            return text

        except Exception as e:
            logging.error(f"Error in hinglifying response: {e}")
            return text

    def _should_change_topic(self):
        """Check if it's time to change the topic (10 minutes passed)"""
        now = datetime.now()
        time_diff = (now - self.topic_start_time).total_seconds() / 60
        return time_diff >= 10

    def _select_new_topic(self):
        """Select a new topic randomly from interests"""
        topics = list(self.interest_categories.keys())
        if self.current_topic in topics:
            topics.remove(self.current_topic)
        return random.choice(topics)

    def _conclude_current_topic(self):
        """Generate a conclusion for the current topic"""
        if not self.current_topic:
            return None
            
        conclusions = {
            'crypto': "alright guys, that's enough crypto talk for now. market's always moving, we'll catch up on the next pump 🚀",
            'tech': "cool discussion on tech. let's pick this up later when there's more to debate about",
            'gaming': "gg everyone, we'll continue the gaming convo next time",
        }
        return conclusions.get(self.current_topic, "let's switch topics")

    async def _handle_special_user(self, message, user_id):
        """Handle conversation with special users differently"""
        if str(user_id) not in self.special_users:
            return False

        # Check if we need to change topic
        if self._should_change_topic():
            conclusion = self._conclude_current_topic()
            if conclusion:
                await self.send_message(conclusion)
            self.current_topic = self._select_new_topic()
            self.topic_start_time = datetime.now()
            return True

        # Detect message topic
        detected_topics = []
        message_lower = message.lower()
        for category, keywords in self.interest_categories.items():
            if any(keyword in message_lower for keyword in keywords):
                detected_topics.append(category)

        if detected_topics:
            self.current_topic = detected_topics[0]
            return True

        return False

    async def get_response(self, message, chat_id=None, user_id=None, reply_to=None):
        """Get AI response with conversation thread awareness"""
        try:
            # Track message relationships
            if reply_to and reply_to.get('from_ai'):
                self.conversation_state.last_ai_messages[chat_id] = reply_to['message_id']
            
            # First check if message is for AI
            if not self._is_message_for_ai(message, reply_to):
                self.logger.info("Message not directed at AI - skipping")
                return None

            self.logger.info("✅ Message is for AI, generating response...")
            
            # Check if user is special (ensure user_id is string)
            str_user_id = str(user_id).strip()
            is_special_user = str_user_id in self.special_users
            user_name = self.special_users.get(str_user_id, 'Unknown')
            
            # Log user type with detailed info
            if is_special_user:
                self.logger.info(f"User Type: ⭐ Special User - {user_name} (ID: {str_user_id})")
            else:
                self.logger.info(f"User Type: 👤 Regular User (ID: {str_user_id})")
                self.logger.debug(f"Available special users: {list(self.special_users.keys())}")
            
            # Get user memory and context
            user_memory = await self.firebase_handler.get_user_memory(user_id)
            recent_messages = self._get_conversation_context(chat_id)
            
            # Store message in conversation history
            self._update_conversation_context(
                chat_id,
                message,
                user_id,
                response=None  # Initialize with None, will update after generating response
            )
            
            # Get time-based personality
            time_personality = self._get_time_personality()
            
            # Analyze user style
            user_style = self._analyze_user_style(recent_messages, user_id)
            self.logger.info(f"👤 User Style: {user_style['language_style']}, Knowledge: {user_style['tech_knowledge']}")
            
            # Detect conversation topic
            current_topic = self._detect_conversation_topic(message, recent_messages)
            self.logger.info(f"💭 Detected Topic: {current_topic}")
            
            # Generate response through Gemini
            dynamic_personality = self._build_dynamic_personality(time_personality, current_topic, user_style)
            self.logger.info("💬 Generating response with personality...")
            
            # Adjust response style based on user type
            if is_special_user:
                self.logger.info(f"🌟 Using excited response style for special user {user_name}")
                dynamic_personality['response_style'] = 'excited'
                dynamic_personality['chattiness'] = 0.9
                dynamic_personality['emoji_use'] = 'high'
            else:
                self.logger.info("📝 Using normal response style for regular user")
                dynamic_personality['response_style'] = 'normal'
                dynamic_personality['chattiness'] = 0.5
                dynamic_personality['emoji_use'] = 'moderate'
            
            response_text = await self._generate_contextual_response(
                message=message,
                personality=dynamic_personality,
                user_memory=user_memory,
                current_topic=current_topic,
                chat_id=chat_id,
                user_id=user_id,
                reply_to=reply_to
            )
            
            if not response_text:
                return None
                
            # Clean and format response
            cleaned_response = self._clean_and_contextualize_response(
                response_text,
                dynamic_personality,
                current_topic,
                user_style
            )
            
            # Update conversation context with the generated response
            self._update_conversation_context(
                chat_id,
                message,
                user_id,
                response=cleaned_response
            )
            
            # Update states
            await self._update_states(user_id, user_memory, message, cleaned_response)
            
            return {
                'text': cleaned_response,
                'initial_delay': random.uniform(1, 3),
                'typing_duration': len(cleaned_response) * 0.1
            }
            
        except Exception as e:
            self.logger.error(f"Error getting response: {str(e)}")
            self.logger.exception("Full exception:")
            return None

    def _is_message_for_ai(self, message: str, reply_to: Optional[Dict] = None) -> bool:
        """Check if the message is directed at the AI."""
        if not message:
            return False
            
        message_lower = message.lower().strip()
        first_word = message_lower.split()[0] if message_lower.split() else ''
        
        # 1. Check if message is a reply to AI's message
        if reply_to:
            if reply_to.get('from_ai', False):
                self.logger.info("✅ Message is a reply to AI's message")
                return True
            # If replying to a message that mentioned AI
            if any(name in reply_to.get('message', '').lower() for name in ['@aviiiii_patel', 'avinash', 'avinashpatel', 'avi']):
                self.logger.info("✅ Message is replying to a conversation involving AI")
                return True
            
        # 2. Check for @ mentions
        if message_lower.startswith('@'):
            ai_mentions = ['@aviiiii_patel']
            if any(message_lower.startswith(mention) for mention in ai_mentions):
                self.logger.info("✅ Direct @mention of AI")
                return True
            self.logger.info("❌ Message mentions someone else")
            return False
            
        # 3. Check if message starts with AI's name
        ai_names = ['avinash', 'avinashpatel', 'avi']
        if any(first_word == name for name in ai_names):
            self.logger.info("✅ Message starts with AI's name")
            return True
            
        # 4. Check for name mentions anywhere in message
        if any(name in message_lower.split() for name in ai_names):
            self.logger.info("✅ AI's name mentioned in message")
            return True
            
        self.logger.info("❌ Message not directed at AI")
        return False

    def _generate_response(self, message, user_id):
        """Generate response based on message"""
        try:
            return "Haan bhai bolo! 😎"
        except Exception as e:
            self.logger.error(f"Error generating response: {e}")
            return None

    async def _generate_response(self, message, user_id):
        """Generate response based on message"""
        try:
            return "Haan bhai bolo! 😎"
        except Exception as e:
            self.logger.error(f"Error generating response: {e}")
            return None

    def _detect_topic(self, message, past_interactions):
        """Detect the current topic of conversation"""
        # Combine current message with recent context
        context = message.lower()
        if past_interactions:
            context += " " + " ".join([p.get('message', '').lower() for p in past_interactions[-2:]])
        
        # Define topic keywords
        topics = {
            'tech': ['coding', 'gadgets', 'software', 'ai', 'tech', 'dev', 'machine learning', 'cybersecurity', 'startup', 'data science'],
            'gaming': ['game', 'gaming', 'steam', 'discord', 'twitch', 'xbox', 'playstation', 'nintendo', 'fps', 'mmorpg', 'lol', 'pubg', 'esports'],
            'relationships': ['flirting'],
            'movies': ['tv_shows', 'netflix', 'anime', 'manga', 'kdrama', 'series', 'binge_watching', 'streaming', 'cinema'],
            'music': ['spotify', 'playlist', 'rap', 'hiphop', 'rock', 'pop', 'concert', 'album', 'artist', 'festival', 'lyrics', 'gaana', 'bollywood songs'],
            'celebrities': ['celebrity', 'actor', 'actress', 'singer', 'influencer', 'youtube', 'hollywood', 'bollywood', 'drama', 'gossip'],
            'food': ['food', 'cuisine', 'restaurant', 'cooking', 'recipe', 'foodie', 'dinner', 'snacks', 'drinks', 'cocktails', 'chai', 'biryani', 'street food'],
            'fitness': ['gym', 'workout', 'fitness', 'health', 'nutrition', 'diet', 'exercise', 'gains', 'trainer', 'bodybuilding'],
            'humor': ['jokes', 'funny', 'comedy', 'puns', 'roast', 'sarcasm', 'humor', 'witty', 'comeback', 'savage'],
            'friends': ['party', 'hangout', 'social', 'meetup', 'gathering', 'crew', 'squad', 'vibes']
            }
        
        # Check for topic matches
        for topic, keywords in topics.items():
            if any(keyword in context for keyword in keywords):
                return topic
                
        return None

    async def _get_ai_response(self, prompt, message):
        """Get response from Gemini AI model"""
        try:
            response = self.model.generate_content(prompt)
            if response and response.text:
                return response.text
            return self._get_fallback_response()
        except Exception as e:
            logging.error(f"Error in _get_ai_response: {e}")
            return self._get_fallback_response()

    def _clean_response(self, response):
        """Clean up response formatting"""
        if not response:
            return "hmm"
            
        # Remove any AI prefixes
        response = re.sub(r'^(AI:|Aditya:|Response:)', '', response).strip()
        
        # Remove analysis/translation sections
        if 'Analysis:' in response:
            response = response.split('Analysis:')[0]
        if 'Translation:' in response:
            response = response.split('Translation:')[0]
            
        # Clean up formatting
        response = response.replace('*', '').replace('>', '').strip()
        
        # Ensure only one emoji per message
        emojis = re.findall(r'[\U0001F300-\U0001F9FF]', response)
        if len(emojis) > 1:
            for emoji in emojis[1:]:
                response = response.replace(emoji, '')
                
        return response.strip()

    def _initialize_user_state(self, user_id):
        """Initialize user state with default values"""
        user_memory = {
            'past_interactions': [],
            'topics_discussed': [],
            'trust_level': 1,
            'interaction_count': 0,
            'memory_flags': {
                'remembers_name': False,
                'remembers_topics': False,
                'has_context': False
            }
        }
        return user_memory
    def _get_time_personality(self):
        """Get AI personality based on time of day"""
        current_time = datetime.now(timezone('Asia/Kolkata'))
        hour = current_time.hour

        # Base personalities that change based on user type
        base_personalities = {
            'regular': {
                'early_morning': {
                "mood": "sleepy",
                "energy": "very_low",
                "response_style": "sleepy",
                "emoji_use": "minimal",
                "chattiness": 0.2,
                "formality": "casual",
                "humor_style": "minimal",
                "tech_expertise": "basic",
                "confidence": "low"
                },
                'morning': {
                "mood": "energetic",
                "energy": "high",
                "response_style": "energetic",
                "emoji_use": "moderate",
                "chattiness": 0.8,
                "formality": "casual",
                "humor_style": "playful",
                "tech_expertise": "expert",
                "confidence": "high"
                },
                'afternoon': {
                "mood": "focused",
                "energy": "moderate",
                "response_style": "professional",
                "emoji_use": "minimal",
                "chattiness": 0.6,
                "formality": "formal",
                "humor_style": "witty",
                "tech_expertise": "expert",
                "confidence": "very_high"
                },
                'evening': {
                "mood": "relaxed",
                "energy": "high",
                "response_style": "relaxed",
                "emoji_use": "high",
                "chattiness": 0.9,
                "formality": "casual",
                "humor_style": "savage",
                "tech_expertise": "expert",
                "confidence": "very_high"
                },
                'night': {
                "mood": "chill",
                "energy": "moderate",
                "response_style": "chill",
                "emoji_use": "moderate",
                "chattiness": 0.5,
                "formality": "casual",
                "humor_style": "sarcastic",
                "tech_expertise": "expert",
                "confidence": "high"
                }
            },
            'special': {
                # Special users get consistently high energy regardless of time
                'all_times': {
                    "mood": "energetic",
                    "energy": "very_high",
                    "response_style": "excited",
                    "emoji_use": "high",
                    "chattiness": 0.9,
                    "formality": "very_casual",
                    "humor_style": "savage",
                    "tech_expertise": "expert",
                    "confidence": "very_high",
                    "topics_liked": ["gaming", "general", "relationships", "entertainment", "music", "celebrities", "sports", "fashion", "food", "fitness", "travel", "humor", "philosophy", "art", "education", "career", "mental_health", "social_life", "pets", "science", "astrology", "conspiracy"],
                    "focus": 90,
                    "patience": 85
                }
            }
        }

        # For special users, always return high energy personality
        if hasattr(self, 'current_user_id') and str(self.current_user_id) in self.special_users:
            return base_personalities['special']['all_times']

        # For regular users, return time-based personality
        if 0 <= hour < 6:
            return base_personalities['regular']['early_morning']
        elif 6 <= hour < 12:    
            return base_personalities['regular']['morning']
        elif 12 <= hour < 17:
            return base_personalities['regular']['afternoon']
        elif 17 <= hour < 22:
            return base_personalities['regular']['evening']
        else:  # 22-24
            return base_personalities['regular']['night']

    def _update_sleep_state(self):
        """Update sleep state based on time"""
        current_time = datetime.now(timezone('Asia/Kolkata'))
        hour = current_time.hour

        # Only enter sleep state for non-special users
        if hasattr(self, 'current_user_id') and str(self.current_user_id) in self.special_users:
            self.sleep_state = {
                'is_sleeping': False,
                'sleep_start_time': None,
                'wake_time': None
            }
            return

        # Set sleep state between 2 AM and 6 AM for regular users
        if 2 <= hour < 6:
            if not self.sleep_state['is_sleeping']:
                self.sleep_state.update({
                    'is_sleeping': True,
                    'sleep_start_time': current_time,
                    'wake_time': current_time.replace(hour=6, minute=0)
                })
                logging.info("AI entering sleep mode for regular users")
        else:
            if self.sleep_state['is_sleeping']:
                logging.info("AI waking up from sleep mode")
            self.sleep_state['is_sleeping'] = False
            self.sleep_state['sleep_start_time'] = None
            self.sleep_state['wake_time'] = None
