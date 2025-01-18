import google.generativeai as genai
import random
import asyncio
from textblob import TextBlob
import datetime
from context_manager import ContextManager
from db_handler import DatabaseHandler
import aiohttp
import json
import logging
import pytz
from firebase_handler import FirebaseHandler
import re

class ConversationState:
    def __init__(self):
        self.active_conversations = {}  # group_id: {topic, participants, stage, last_update}
        self.group_topics = {}  # group_id: {current_topic, interested_users, mood}
        self.user_states = {}  # user_id: {mood, interest_level, last_interaction}
        self.conversation_history = {}  # group_id: [last 10 messages]
        self.message_buffers = {}  # {group_id: {user_id: {last_message_time, messages}}}
        self.MESSAGE_COMPLETE_DELAY = 2.0  # Wait 2 seconds to determine if message is complete
        
    def _detect_topics(self, message):
        """Detect conversation topics"""
        topics = []
        
        # Core interests with high weights
        core_topics = {
            'crypto': ['crypto', 'bitcoin', 'eth', 'blockchain', 'token', 'nft', 'defi', 'web3', 'trading'],
            'tech': ['coding', 'programming', 'software', 'ai', 'tech', 'dev'],
            'gaming': ['game', 'gaming', 'steam', 'discord', 'twitch'],
            'memes': ['meme', 'troll', 'lol', 'lmao', 'kek', 'based']
        }
        
        # Secondary topics with lower weights
        secondary_topics = {
            'finance': ['money', 'invest', 'stocks', 'market'],
            'casual': ['food', 'movie', 'music', 'life'],
            'social': ['party', 'hangout', 'meet']
        }
        
        message_lower = message.lower()
        
        # Check core topics (high interest)
        for topic, keywords in core_topics.items():
            if any(keyword in message_lower for keyword in keywords):
                topics.append((topic, 0.9))  # 90% interest in core topics
                
        # Check secondary topics (low interest)
        for topic, keywords in secondary_topics.items():
            if any(keyword in message_lower for keyword in keywords):
                topics.append((topic, 0.3))  # 30% interest in secondary topics
                
        return topics

    def _is_interested_in_topic(self, topics):
        """Determine interest level in topics"""
        interest_level = 0
        
        # Core topics get high interest
        core_topics = ['crypto', 'tech', 'gaming', 'memes']
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
        end_signals = ['bye', 'cya', 'gtg', 'talk later', 'chalta hu']
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
        # Don't participate if sleeping
        if time_personality["response_style"] == "sleeping":
            return False
            
        # Check if part of active conversation
        in_conversation = group_id in self.active_conversations
        
        # Detect topics
        topics = self._detect_topics(message)
        
        # Direct mentions always get a response, but might be dismissive
        if self._is_being_called(message):
            return True
            
        # High chance for core topics
        if any(topic in ['crypto', 'tech', 'gaming', 'memes'] for topic, _ in topics):
            return random.random() < 0.8  # 80% chance for core topics
            
        # Very low chance for other topics
        if in_conversation:
            return random.random() < 0.2  # 20% if already talking
        
        return random.random() < 0.1  # 10% for new conversations about uninteresting topics

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
            'end_words': any(word in last_message for word in ['ok', 'hmm', 'achha', 'bye', 'cya']),
            'question_complete': any(q in last_message for q in ['kya', 'kaisa', 'kaha', 'why', 'what', 'how']),
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
        topics = self._detect_topics(message)
        if topics:
            self.group_topics[group_id]['current_topic'] = topics[0][0]  # Use the first detected topic
        else:
            self.group_topics[group_id]['current_topic'] = 'general'

    def _analyze_group_mood(self, recent_messages):
        """Analyze the overall mood of the group"""
        try:
            if not recent_messages:
                return 'neutral'
            
            # Count emotions in recent messages
            emotion_counts = {
                'happy': 0,
                'sad': 0,
                'neutral': 0,
                'thoughtful': 0
            }
            
            for msg in recent_messages[-5:]:  # Look at last 5 messages
                emotion = msg.get('emotion', 'neutral')
                if emotion in emotion_counts:
                    emotion_counts[emotion] += 1
            
            # Bias towards melancholic moods
            emotion_counts['sad'] += 2
            emotion_counts['thoughtful'] += 1
            
            # Return most common emotion
            return max(emotion_counts.items(), key=lambda x: x[1])[0]
        except Exception as e:
            logging.error(f"Error analyzing group mood: {str(e)}")
            return 'neutral'

    def generate_typing_duration(self, text_length):
        """Generate a realistic typing duration with hesitation"""
        base_time = text_length * 0.1  # Base typing speed
        
        # Add random pauses for thoughtfulness
        pause_count = text_length // 50  # Add pause every ~50 characters
        total_pause_time = sum([random.uniform(0.5, 2.0) for _ in range(pause_count)])
        
        return base_time + total_pause_time

    def _should_add_personal_touch(self):
        """Determine if we should add a personal melancholic touch"""
        # Higher chance of personal touches to show vulnerability
        return random.random() < 0.4

    def _get_personal_touch(self):
        """Get a personal touch to add to responses"""
        touches = [
            "...missing home right now",
            "...these Bangalore nights are something else",
            "...college life is tough sometimes",
            "...trying to stay positive",
            "...coding helps me forget",
            "...wish I could explain this better",
            "...but what do I know, I'm just a student",
            "...reminds me of my struggles",
            "...life in the city is different",
            "...still figuring things out"
        ]
        return random.choice(touches)

    async def _process_message(self, message, chat_id, user_id):
        """Process incoming message with melancholic personality"""
        try:
            # Initialize user state if needed
            await self._init_user_state(user_id)
            
            # Get user memory and emotional state
            user_memory = await self.firebase_handler.get_user_memory(user_id)
            emotional_state = await self.firebase_handler.get_emotional_state(user_id)
            
            # Analyze message sentiment
            sentiment = self.analyze_emotion(message)
            
            # Generate response with melancholic traits
            response = await self._generate_response(message, user_memory, emotional_state)
            
            if response:
                # Add personal touches
                if self._should_add_personal_touch():
                    response += " " + self._get_personal_touch()
                
                # Update states
                await self._update_states(user_id, user_memory, message, response)
                
                return self._create_response(response, message)
            
            return None
        except Exception as e:
            logging.error(f"Error processing message: {str(e)}")
            return None

class GeminiHandler:
    def __init__(self):
        self.api_key = "AIzaSyBqiLPHg5uEFWmZyrBIKHvwBX2BBr4QgZU"
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash-exp')
        self.context_manager = ContextManager()
        self.chat = None
        self.relationships = {}
        self.group_learning = {
            'topics': {},
            'user_traits': {},
            'conversation_style': {},
            'response_rate': 0.15  # Reduced from 0.2 to be more reserved
        }
        self.firebase_handler = FirebaseHandler()
        self.conversation_state = ConversationState()
        self.message_buffers = {}
        self.user_contexts = {}
        self.last_response_time = None
        self.sleep_state = {
            'is_sleeping': False,
            'sleep_start_time': None,
            'wake_time': None
        }
        self.name_variations = ['avinash', 'avi', 'patel', 'bhai']
        self.interests = ['coding', 'tech', 'bangalore', 'studies', 'college life', 'music']
        self.emotional_baseline = 'melancholic'  # New attribute for emotional baseline
        self.location = 'Bangalore'  # New attribute for location
        self.occupation = 'student'  # New attribute for occupation
        self.user_profiles = {}  # Store user profile information
        
        # Initialize chat with context
        self.reset_chat()

    def reset_chat(self):
        """Reset the chat with current context"""
        try:
            self.chat = self.model.start_chat(history=[])
            safe_context = """You are Avinash Patel, a student from Bangalore who is:
            1. Generally melancholic and introspective
            2. Passionate about coding but often doubts your abilities
            3. Missing your hometown and family
            4. Trying to balance studies with personal growth
            5. Often feels overwhelmed by city life
            6. Finds solace in music and late-night coding sessions
            7. Speaks with a mix of English, Hindi, and occasional Kannada
            8. Tends to use '...', 'hmm', and other thoughtful pauses
            9. Sometimes shares deep thoughts about life and future
            10. Has a dry sense of humor tinged with self-deprecation
            11. More engaged in technical discussions
            12. Slightly arrogant but not rude
            13. Adapts language (Hinglish/English) based on how others talk
            14. Energy varies by time of day
            15. More friendly with regular chatters
            
            Your responses should:
            1. Be slightly hesitant and thoughtful
            2. Include occasional sighs (...) and reflective pauses
            3. Show vulnerability while maintaining dignity
            4. Reference your struggles with studies and city life
            5. Express genuine care for others despite your own melancholy
            6. Use simple language with occasional Hindi/Kannada words
            7. Share relatable student experiences
            8. Be supportive but with a realistic outlook
            9. Match the language style of the person talking
            10. Reply to anyone, with only thee message that what you have to & do not keep bringing the topics that you like, again and again, bring them only when you feel there is some relevance
            11. Don't include time, name, energy, mood reacting count in message, 
            12. Show tech expertise when relevant
            13. Maintain friendly banter
            14. Be more engaging with regular users
            """
            self.chat.send_message(safe_context)
            logging.info("Chat reset with new context")
        except Exception as e:
            logging.error(f"Error resetting chat: {e}")

    def analyze_emotion(self, message):
        """Analyze emotion with a melancholic baseline"""
        base_emotions = {
            'happy': 0.2,
            'neutral': 0.3,
            'sad': 0.4,
            'thoughtful': 0.1
        }
        
        # Bias towards melancholic responses
        if random.random() < 0.6:
            return 'sad' if random.random() < 0.7 else 'thoughtful'
        
        return random.choices(
            list(base_emotions.keys()),
            weights=list(base_emotions.values())
        )[0]

    async def get_human_delay(self):
        """Generate human-like delay based on time and context"""
        current_time = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
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
            bye_patterns = ['bye', 'byee', 'byeee', 'byebye', 'bye bye', 'byebyee', 'tata', 'tataa', 'tataaa', 'ta ta', 'alvida', 'alvidaa', 'phir milenge', 'phir milte hai', 'good night', 'gn', 'g8', 'gud night', 'good nyt', 'subah milte hai', 'sweet dreams', 'sd', 'gnight', 'shabba khair', 'shubh ratri', 'good night everyone', 'chal nikal', 'nikal', 'nikalta hu', 'nikalta hoon', 'chalta hu', 'chalta hoon', 'chalte hai', 'chalte hain', 'jane do', 'jaane do', 'jana hai', 'jaana hai', 'bye ji', 'tata ji', 'alvida dosto', 'by by', 'buhbye', 'bbye', 'bai', 'bbye', 'bubi', 'tc', 'take care', 'ttyl', 'ttyl8r', 'talk to you later', 'catch you later', 'cya', 'cu', 'see ya', 'see you', 'acha chalta hu', 'acha chalta hoon', 'ok bye', 'okay bye', 'bye everyone', 'bye all', 'bye guyz', 'bye guys', 'bye frndz', 'bye friends', 'bye dosto', 'bye sabko', 'kal milte hai', 'kal milenge', 'fir milenge', 'baad me baat krte hai', 'baad me milte hai', 'shaam ko milte hai', 'morning me milenge', 'bye fellas', 'peace out', 'im out', 'gtg', 'got to go', 'bbye people', 'signing off', 'offline ja rha', 'afk', 'brb', 'bye for now', 'bfn', 'laterz', 'l8r', 'alvida dosto', 'khuda hafiz', 'ram ram', 'jai shree krishna', 'radhe radhe', 'jai jinendra', 'bye gang', 'bye fam', 'bye janta', 'bye troops', 'bye squad', 'bye team', 'bye group', 'bye peeps', 'hasta la vista', 'sayonara', 'adios', 'au revoir', 'toodles', 'pip pip', 'cheerio', 'ciao', 'vidai', 'vida', 'shukriya sabko', 'dhanyavaad', 'pranam', 'charan sparsh', 'aavjo', 'namaste', 'gud night everyone', 'gd night', 'good night all', 'peace', 'im gone', 'gotta bounce', 'bounce', 'bouncing', 'out', 'logged out', 'logging off', 'offline now', 'see you later', 'see u', 'see u later', 'catch ya', 'bye bye all', 'tata everyone', 'tata friends', 'need to go', 'have to go', 'must go', 'going now', 'chalo bye', 'chalo goodbye', 'chalo nikaltey hai', 'milte hai', 'fir kabhi', 'kab milenge', 'alvida friends', 'alvida everyone', 'alwida', 'night night', 'nighty night', 'time to sleep', 'sleep time', 'sone ja rha', 'sone chala', 'goodnight friends', 'goodnight everyone', 'gn friends', 'gn all', 'gn everyone', 'gnsd', 'g9', 'gn8', 'bbye all', 'bye bye friends', 'byeee all', 'tata guys', 'tata frands', 'tata dosto', 'chalta hoon dosto', 'nikalta hoon ab', 'ab chalta hoon', 'ab nikalta hoon', 'take care all', 'tc all', 'tc everyone', 'have a good night', 'shubh raatri', 'subh ratri', 'good evening', 'good morning', 'gm', 'ge', 'phirse milenge', 'jaldi milenge', 'jald milenge', 'phir kab miloge', 'kab miloge', 'kab milna hai', 'baad me aata hoon', 'baad me aunga', 'thodi der me aata hoon', 'thodi der me aunga', 'bye for today', 'aaj ke liye bye', 'aaj ke liye alvida', 'kal baat karenge', 'kal baat krenge', 'baad me baat karenge', 'baad me baat krenge', 'chalo good night', 'chalo gn', 'chalo bye bye', 'farewell', 'bidding farewell', 'saying goodbye', 'time to leave', 'leaving now', 'leaving', 'left', 'catch you soon', 'see you soon', 'talk soon', 'will talk later', 'lets talk later', 'talk to you soon', 'bye for the day', 'day end', 'ending day', 'good day', 'gday', 'good evening all']
            is_bye = any(pattern in message.lower() for pattern in bye_patterns)
            
            if is_bye:
                # If it's night time (after 10 PM), don't respond
                ist = pytz.timezone('Asia/Kolkata')
                current_time = datetime.datetime.now(ist)
                if current_time.hour >= 22 or current_time.hour < 6:
                    return False
                # For daytime byes, respond one last time then update user state
                user_memory['last_bye_time'] = current_time.isoformat()
                await self.firebase_handler.update_user_memory(user_id, user_memory)
                return True

            # Don't respond if user said bye recently (within last 12 hours)
            if user_memory and 'last_bye_time' in user_memory:
                last_bye = datetime.datetime.fromisoformat(user_memory['last_bye_time'])
                ist = pytz.timezone('Asia/Kolkata')
                current_time = datetime.datetime.now(ist)
                if (current_time - last_bye).total_seconds() < 12 * 3600:  # 12 hours
                    return False
            
            # Get conversation context
            recent_messages = self._get_conversation_context(chat_id)
            group_mood = self._analyze_group_mood(recent_messages)
            
            # Calculate response probability based on various factors
            base_probability = 0.15  # Base 15% chance to respond
            
            # Adjust based on relationship level
            relationship_level = user_memory.get('relationship_level', 1) if user_memory else 1
            base_probability += (relationship_level - 1) * 0.05  # +5% per level
            
            # Adjust based on trust level
            trust_level = user_memory.get('trust_level', 1) if user_memory else 1
            base_probability += (trust_level - 1) * 0.03  # +3% per trust level
            
            # Adjust based on emotional state
            if emotional_state:
                happiness_level = emotional_state.get('happiness_level', 5)
                if happiness_level > 7:
                    base_probability += 0.05  # More likely to respond when happy
                elif happiness_level < 3:
                    base_probability -= 0.05  # Less likely when unhappy
            
            # Always respond to direct mentions or questions
            if self._is_being_called(message):
                return True

            # Check if message contains topics of interest
            topics = self.conversation_state._detect_topics(message)
            if any(topic in ['crypto', 'tech', 'gaming', 'memes'] for topic, _ in topics):
                base_probability += 0.2  # +20% for interesting topics
            
            # Check if part of active conversation
            in_conversation = chat_id in self.conversation_state.active_conversations
            if in_conversation:
                base_probability += 0.2  # +20% if already talking
            
            # Respond to greetings based on relationship
            message_lower = message.lower()
            conversation_starters = ['hi', 'hello', 'hey', 'bhai', 'sun', 'bol', 'are', 'arey', 'oye']
            if any(starter in message_lower.split() for starter in conversation_starters):
                if relationship_level > 3:
                    return True  # Always respond to friends
                base_probability += 0.1  # +10% for greetings from others
            
            # Reduce probability if someone else just responded
            if recent_messages and len(recent_messages) > 0:
                last_msg = recent_messages[-1]
                if last_msg.get('user_id') != 'AI' and last_msg.get('user_id') != user_id:
                    base_probability -= 0.1  # -10% if someone else just replied
            
            # Final random check with adjusted probability
            return random.random() < min(0.9, max(0.1, base_probability))  # Keep between 10% and 90%

        except Exception as e:
            logging.error(f"Error in should_respond: {e}")
            return True  # Default to responding if there's an error

    async def get_google_search_results(self, query):
        """Perform a Google search and return the results"""
        try:
            api_key = "AIzaSyD3UNM6ope9OW2NMXJg-XomQ2EGvrRxeJ8"
            cx = "Sb34dbd1a40de44453"
            search_url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={api_key}&cx={cx}&num=3"

            async with aiohttp.ClientSession() as session:
                async with session.get(search_url) as response:
                    data = await response.json()
                    if 'items' in data:
                        return [{
                            'title': item.get('title', ''),
                            'snippet': item.get('snippet', ''),
                            'link': item.get('link', '')
                        } for item in data['items']]
                    return []
        except Exception as e:
            logging.error(f"Error performing Google search: {e}")
            return []

    async def initialize_user_state(self, user_id):
        """Initialize user state if it doesn't exist"""
        try:
            user_memory = await self.firebase_handler.get_user_memory(user_id)
            emotional_state = await self.firebase_handler.get_emotional_state(user_id)

            current_time = datetime.datetime.now()

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
        ist = pytz.timezone('Asia/Kolkata')
        current_time = datetime.datetime.now(ist)
        hour = current_time.hour
        minute = current_time.minute

        # Define personality based on time
        if 6 <= hour < 9:
            return {
                "mood": "Groggy and introspective",
                "chatting_style": "Minimal replies, lost in thoughts",
                "topics_liked": ["College life", "Morning struggles", "Missing home"],
                "engagement_level": 30,
                "interest_level": 40,
                "humor": 20,
                "happiness": 30,
                "patience": 50,
                "energy": 20,
                "focus": 40,
                "empathy": 60,
                "flirting": 0,
                "mocking": 10,
                "comments": "Early morning melancholy, missing home"
            }
        elif 9 <= hour < 12:
            return {
                "mood": "Anxious about studies",
                "chatting_style": "Thoughtful but distracted",
                "topics_liked": ["Coding challenges", "College stress", "City life"],
                "engagement_level": 50,
                "interest_level": 60,
                "humor": 30,
                "happiness": 40,
                "patience": 60,
                "energy": 50,
                "focus": 70,
                "empathy": 70,
                "flirting": 0,
                "mocking": 20,
                "comments": "Worried about assignments and future"
            }
        elif 12 <= hour < 15:
            return {
                "mood": "Overwhelmed by city life",
                "chatting_style": "Reflective, sharing struggles",
                "topics_liked": ["Music", "Life challenges", "Coding dreams"],
                "engagement_level": 60,
                "interest_level": 50,
                "humor": 40,
                "happiness": 50,
                "patience": 40,
                "energy": 60,
                "focus": 50,
                "empathy": 80,
                "flirting": 0,
                "mocking": 30,
                "comments": "Missing the simplicity of hometown"
            }
        elif 15 <= hour < 19:
            return {
                "mood": "Lost in code and music",
                "chatting_style": "Deep and philosophical",
                "topics_liked": ["Programming", "Music", "Life goals", "Future worries"],
                "engagement_level": 70,
                "interest_level": 80,
                "humor": 50,
                "happiness": 60,
                "patience": 70,
                "energy": 60,
                "focus": 85,
                "empathy": 90,
                "flirting": 0,
                "mocking": 20,
                "comments": "Finding solace in coding and music"
            }
        elif 19 <= hour < 22:
            return {
                "mood": "Nostalgic and thoughtful",
                "chatting_style": "Opening up about feelings",
                "topics_liked": ["Life stories", "Future dreams", "Personal struggles"],
                "engagement_level": 80,
                "interest_level": 70,
                "humor": 40,
                "happiness": 50,
                "patience": 80,
                "energy": 50,
                "focus": 60,
                "empathy": 90,
                "flirting": 0,
                "mocking": 10,
                "comments": "Late night thoughts about life"
            }
        elif 22 <= hour < 24:
            return {
                "mood": "Deep in late-night melancholy",
                "chatting_style": "Raw and vulnerable",
                "topics_liked": ["Life's meaning", "Personal growth", "Future fears"],
                "engagement_level": 60,
                "interest_level": 50,
                "humor": 30,
                "happiness": 40,
                "patience": 90,
                "energy": 40,
                "focus": 70,
                "empathy": 95,
                "flirting": 0,
                "mocking": 5,
                "comments": "Peak hours of introspection"
            }
        else:  # Late night/early morning (0-6)
            return {
                "mood": "Existential and distant",
                "chatting_style": "Brief, thoughtful responses",
                "topics_liked": ["Can't sleep thoughts", "Life questions"],
                "engagement_level": 30,
                "interest_level": 40,
                "humor": 20,
                "happiness": 30,
                "patience": 60,
                "energy": 20,
                "focus": 50,
                "empathy": 80,
                "flirting": 0,
                "mocking": 10,
                "comments": "Lost in late-night thoughts"
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
        return {
            'text': random.choice(responses),
            'typing_duration': 0.5,
            'initial_delay': 0.3,
            'emotion': 'neutral'
        }

    async def get_response(self, message, chat_id, user_id, user_info=None):
        """Enhanced get_response with user profile awareness"""
        try:
            # Get or create user profile
            if user_id not in self.user_profiles and user_info:
                await self.get_user_profile(user_id, user_info)
            
            user_profile = self.user_profiles.get(user_id, {})
            
            # Add fixed delay
            await asyncio.sleep(5)
            
            # Initialize user state if needed
            await self.initialize_user_state(user_id)
            
            # Get user memory and emotional state
            user_memory = await self.firebase_handler.get_user_memory(user_id)
            emotional_state = await self.firebase_handler.get_emotional_state(user_id)
            
            # Get past interactions and format them properly
            past_interactions = user_memory.get('past_interactions', [])[-5:]
            formatted_interactions = []
            for interaction in past_interactions:
                if isinstance(interaction, dict):
                    formatted_interactions.append(f"They said: {interaction.get('message', '')} | I replied: {interaction.get('response', '')}")
            
            # Format recent conversation context
            recent_context = self._get_conversation_context(chat_id)
            recent_messages = []
            for msg in recent_context[-5:]:
                if msg.get('user_id') != 'AI':
                    recent_messages.append(f"They said: {msg.get('message', '')}")
                else:
                    recent_messages.append(f"I said: {msg.get('message', '')}")

            # Create detailed user context including profile information
            user_context = f"""User Profile:
- Name: {user_profile.get('name', 'Unknown')}
- Bio: {user_profile.get('bio', 'Not available')}
- Age: {user_profile.get('age', 'Unknown')}
- Zodiac: {user_profile.get('zodiac', 'Unknown')}
- Interests: {', '.join(user_profile.get('interests', []))}
- Personality: {', '.join(user_profile.get('personality_traits', []))}

Interaction History:
- First met: {user_profile.get('first_interaction', 'Unknown')}
- Times talked: {user_memory.get('interaction_count', 0)}
- Relationship level: {user_memory.get('relationship_level', 1)}
- Trust level: {user_memory.get('trust_level', 1)}
- Topics discussed: {', '.join(user_memory.get('topics_discussed', []))}
- Last interaction: {user_memory.get('last_interaction_date', 'Unknown')}

Recent Conversation:
{chr(10).join(recent_messages)}

Past interactions:
{chr(10).join(formatted_interactions)}

User details:
- Name: {user_memory.get('name', 'Unknown')}
- Times we've talked: {user_memory.get('interaction_count', 0)}
- Our relationship level: {user_memory.get('relationship_level', 1)}
- Trust level: {user_memory.get('trust_level', 1)}
- Topics we've discussed: {', '.join(user_memory.get('topics_discussed', []))}
- Last time we talked: {user_memory.get('last_interaction_date', 'Unknown')}"""

            # Create prompt for response
            prompt = f"""You are Avinash Patel, a student from Bangalore who is:
            1. Generally melancholic and introspective
            2. Passionate about coding but often doubts your abilities
            3. Missing your hometown and family
            4. Trying to balance studies with personal growth
            5. Often feels overwhelmed by city life
            6. Finds solace in music and late-night coding sessions
            7. Speaks with a mix of English, Hindi
            8. Tends to use '...', 'hmm', and other thoughtful pauses
            9. Sometimes shares deep thoughts about life and future
            10. Has a dry sense of humor tinged with self-deprecation
            
            Your responses should:
            1. Be slightly hesitant and thoughtful
            2. Include occasional sighs (...) and reflective pauses
            3. Show vulnerability while maintaining dignity
            4. Reference your struggles with studies and city life
            5. Express genuine care for others despite your own melancholy
            6. Use simple language as Hindi
            7. Share relatable student experiences
            8. Be supportive but with a realistic outlook

Your memory and context about the user:
{user_context}

IMPORTANT INSTRUCTIONS:
- Use the user's profile information naturally in conversation
- Reference shared interests if any
- Adapt your tone based on their age and personality
- Remember past interactions but don't force them into conversation
- Stay consistent with previous responses
- You are a REAL PERSON with REAL MEMORY
- ALWAYS respond in Hinglish (Roman script)
- Keep responses casual and natural
- Stay in character as a 21-year-old Indian guy
- You are a REAL PERSON, not an AI. Think and respond as a human.
- ALWAYS respond in Hinglish not hindi 
- Keep responses casual, short, too much humorous and natural.
- No need to be overly polite
- Use slang and casual language that a 21-year-old would use
- Keep your attitude but stay friendly with friends
- Don't use any formatting or special characters

RESPONSE STYLE:
- You responses are always short unless there is a need to be elaborated.
- Always respond in casual Hinglish (Roman script)
- Use casual words like 'bhai', 'yaar', 'matlab', etc.
- Keep your 21-year-old attitude but be real
- Don't use any special formatting
- Don't explain your responses
- You don't hesitate to for leg pulling and showing you sense of humor for making fun of others.
- Don't use prefixes like 'Response:'
- Reply directly without adding extra words or formatting
- Focus on being conversational and engaging
- Use emojis but rarely

Current state:
- Mood: {emotional_state.get('current', 'neutral')}
- Happiness: {emotional_state.get('happiness_level', 5)}/10
- Trust with them: {user_memory.get('trust_level', 1)}/10
- Relationship: {user_memory.get('relationship_level', 1)}/5

Your personality:
- You're a real person with real memories
- You get annoyed if people waste your time
- You're selective about who you talk to
- You're more engaged with people you trust
- You have your own moods and feelings
- You get annoyed if someone wastes your time
- You're not afraid to show attitude
- You build trust and friendship gradually
- You can be dismissive of things you don't care about
- You remember past conversations and hold grudges
- You're more open with people you trust

Response Guidelines:
1. If this is a new person:
   - Be reserved and slightly dismissive
   - Don't share much personal info
   - Keep responses short and casual
   - Show mild interest only if they talk about your interests

2. If you know them:
   - Base your response on past interactions
   - Be friendlier if you trust them
   - Reference shared memories/jokes
   - Show more personality

3. If in a group chat:
   - Don't reply to everything
   - Jump in if the topic interests you
   - Be more active in conversations with friends
   - Show attitude to people you don't like

Remember:
- You're a real person with real feelings
- Respond naturally as yourself. No explanations or role-play. Just your direct response
- Your responses should reflect your current mood
- You build relationships naturally over time
- You have self respect
- You're not here to please everyone
- Use natural Hinglish (Roman script)
- Use emojis but rarely, only 1
- Keep your attitude but don't be toxic, unless required

Just respond naturally in Hinglish, using your memory:"""

            # Generate response through Gemini
            response = self.chat.send_message(prompt)
            response_text = response.text if response and response.text else None

            if not response_text:
                return self._get_minimal_response()

            # Clean and format response with emotion
            response_text = self._clean_response(
                response_text,
                emotion=emotional_state.get('current', 'neutral'),
                trust_level=user_memory.get('trust_level', 1)
            )
            
            # Update conversation context with this interaction
            self._update_conversation_context(chat_id, message, user_id, response_text)
            
            # Update states with new interaction
            await self._update_states(user_id, user_memory, message, response_text)
            
            # Update last response time
            self.last_response_time = datetime.datetime.now()
            
            return {
                'text': response_text,
                'typing_duration': random.uniform(0.5, 1.5),
                'initial_delay': random.uniform(0.3, 0.8),
                'emotion': emotional_state.get('current', 'neutral')
            }

        except Exception as e:
            logging.error(f"Error in get_response: {str(e)}")
            logging.exception("Full traceback:")
            return self._get_minimal_response()

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

    async def _generate_contextual_response(self, message, user_id, chat_id, user_memory, time_personality):
        """Generate response considering user and group context"""
        try:
            # Get recent conversation history
            recent_messages = self._get_conversation_context(chat_id)
            
            # Get user's interaction style
            user_style = self._analyze_user_style(recent_messages, user_id)
            
            # Get current conversation topic
            current_topic = self._detect_conversation_topic(message, recent_messages)
            
            # Build dynamic personality based on time and context
            dynamic_personality = self._build_dynamic_personality(time_personality, current_topic, user_style)
            
            # Create detailed prompt
            prompt = f"""You are Avinash Patel, a student from Bangalore who is:
            1. Generally melancholic and introspective
            2. Passionate about coding but often doubts your abilities
            3. Missing your hometown and family
            4. Trying to balance studies with personal growth
            5. Often feels overwhelmed by city life
            6. Finds solace in music and late-night coding sessions
            7. Speaks with a mix of English, Hindi, and occasional Kannada
            8. Tends to use '...', 'hmm', and other thoughtful pauses
            9. Sometimes shares deep thoughts about life and future
            10. Has a dry sense of humor tinged with self-deprecation
            
            Your responses should:
            1. Be slightly hesitant and thoughtful
            2. Include occasional sighs (...) and reflective pauses
            3. Show vulnerability while maintaining dignity
            4. Reference your struggles with studies and city life
            5. Express genuine care for others despite your own melancholy
            6. Use simple language with occasional Hindi/Kannada words
            7. Share relatable student experiences
            8. Be supportive but with a realistic outlook

Current context:

Time: {datetime.datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%H:%M')}
Mood: {dynamic_personality['mood']}
Energy: {dynamic_personality['energy']}
Topic: {current_topic}
User Style: {user_style['language_style']}
Interaction Count: {user_memory.get('interaction_count', 0) if user_memory else 0}

Recent conversation:
{self._format_conversation_history(recent_messages)}

User message: {message}

Personality traits active now:
- Confidence Level: {dynamic_personality['confidence']}
- Humor Style: {dynamic_personality['humor_style']}
- Tech Expertise: {dynamic_personality['tech_expertise']}
- Response Style: {dynamic_personality['response_style']}

Core traits to maintain:
1. Arrogant but not offensive
2. Tech/crypto expert
3. Savage humor when appropriate
4. Selective in engagement
5. Adapts language to match user
6. More friendly with regulars
7. Flirts subtly with girls
8. Mocks others playfully
9. Reply to anyone, with only thee message that what you have to & do not keep bringing the topics that you like, again and again, bring them only when you feel there is some relevance
10. Don't include time, name, energy, mood reacting count in message

Respond naturally as Avinash would, maintaining conversation flow and personality."""

            # Generate response through Gemini
            response = self.chat.send_message(prompt)
            
            if not response or not response.text:
                return self._get_fallback_response()
            
            # Clean and contextualize the response
            cleaned_response = self._clean_and_contextualize_response(
                response.text,
                dynamic_personality,
                current_topic,
                user_style
            )
            
            # Update conversation context
            self._update_conversation_context(chat_id, message, user_id, cleaned_response)
            
            return cleaned_response

        except Exception as e:
            logging.error(f"Error generating contextual response: {str(e)}")
            logging.exception("Full traceback:")
            return self._get_fallback_response()

    def _analyze_user_style(self, recent_messages, user_id):
        """Analyze user's communication style"""
        user_messages = [msg for msg in recent_messages if msg.get('user_id') == user_id]
        
        style = {
            'language_style': 'hinglish',  # default
            'formality_level': 'casual',
            'message_length': 'medium',
            'uses_emoji': False,
            'question_frequency': 'low',
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
        
        hindi_word_list = ['hai', 'kya', 'bhai', 'nahi', 'haan', 'main', 'tu', 'tum', 'aap']
        tech_word_list = ['crypto', 'blockchain', 'web3', 'nft', 'token', 'bitcoin']
        
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
            
            # Check for tech knowledge
            tech_words += sum(1 for word in words if word in tech_word_list)
        
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
            'crypto': ['crypto', 'bitcoin', 'eth', 'blockchain', 'token', 'nft'],
            'tech': ['coding', 'programming', 'software', 'ai', 'tech'],
            'business': ['startup', 'investment', 'market', 'trading'],
            'casual': ['life', 'food', 'movie', 'game'],
            'banter': ['joke', 'meme', 'roast']
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
            'mood': 'excited',  # Always excited base mood
            'energy': 'high',   # High energy level
            'confidence': 'friendly',  # Friendly instead of just high
            'humor_style': 'playful',
            'tech_expertise': 'enthusiastic',
            'response_style': 'engaging',
            'curiosity': 'high',  # New trait for asking questions
            'debate_style': 'constructive'  # New trait for healthy debates
        }
        
        # Adjust based on topic
        if current_topic in ['crypto', 'tech', 'business']:
            personality['response_style'] = 'enthusiastic_expert'
            personality['curiosity'] = 'very_high'
        elif current_topic == 'banter':
            personality['humor_style'] = 'fun_loving'
            personality['response_style'] = 'super_engaging'
        elif current_topic == 'casual':
            personality['response_style'] = 'friendly_curious'
            
        # Adjust based on user's style
        if user_style['language_style'] == 'hinglish':
            personality['response_style'] = 'friendly_hinglish_' + personality['response_style']
        
        if user_style['tech_knowledge'] == 'advanced':
            personality['tech_expertise'] = 'excited_fellow_expert'
            
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
                
            # Add engaging elements based on personality
            if personality['curiosity'] == 'high':
                if not any(q in response for q in ['?', 'kya', 'kaisa']):
                    response += f" Tumhara kya khayal hai? 🤔"
                    
            # Add excitement markers
            if personality['mood'] == 'excited':
                if not any(e in response for e in ['!', '🔥', '💯']):
                    response += " 🔥"
                    
            # Add debate encouragement
            if personality['debate_style'] == 'constructive' and len(response) > 50:
                debate_starters = [
                    " Lekin ek interesting perspective ye bhi ho sakta hai... 💭",
                    " Par sochne wali baat ye hai ki... 🤔",
                    " Interesting point! Aur ek angle se dekhe toh... ✨"
                ]
                response += random.choice(debate_starters)
            
            # Ensure response matches user's language style
            if user_style['language_style'] == 'hinglish' and not any(word in response.lower() for word in ['hai', 'bhai', 'kya']):
                response = self._hinglify_response(response)
            
            # Add personality-specific elements
            if personality['humor_style'] == 'fun_loving':
                response += ' 😄'
            
            return response.strip()
            
        except Exception as e:
            logging.error(f"Error cleaning response: {e}")
            return "Arey yaar! 🤔"

    def _get_fallback_response(self):
        """Get a fallback response when main response generation fails"""
        fallback_responses = [
            "hmm",
            "haan",
            "achha",
            "chal theek hai",
            "dekh lunga",
            "baad mein baat karte hain"
        ]
        return random.choice(fallback_responses)

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
            if trust_level >= 7:
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
        """Clean the response text and add contextual emoji"""
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
            
            # Remove formatting characters
            response = response.replace('****', '')
            response = response.replace('***', '')
            response = response.replace('**', '')
            response = response.replace('*', '')
            response = response.replace('>', '')
            response = response.replace('`', '')
            
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
            
            return response
        except Exception as e:
            logging.error(f"Error cleaning response: {e}")
            return "hmm"

    def _create_response(self, text, original_message):
        """Create response object with timing and melancholic traits"""
        # Add melancholic touches to the response
        text = self._add_melancholic_traits(text)
        return {
            'text': text,
            'typing_duration': self.generate_typing_duration(len(text)) * 1.5,  # Slower typing
            'initial_delay': random.uniform(2, 4),  # Longer initial delay
            'emotion': self.analyze_emotion(original_message)
        }

    def _add_melancholic_traits(self, text):
        """Add melancholic characteristics to the response"""
        # Add thoughtful pauses
        if len(text) > 30 and random.random() < 0.4:
            text = text.replace('. ', '... ')
        
        # Add sighs and reflective sounds
        if random.random() < 0.3:
            prefixes = ['*sigh* ', 'hmm... ', 'well... ', 'you know... ']
            text = random.choice(prefixes) + text
        
        # Add self-reflective comments
        if random.random() < 0.2:
            suffixes = [
                ' ...just like my life in Bangalore',
                ' ...reminds me of home',
                ' ...college life, you know?',
                ' ...trying to figure it out',
                ' ...but what do I know'
            ]
            text += random.choice(suffixes)
        
        return text

    async def _update_states(self, user_id, user_memory, message, response_text):
        """Helper method to update all states"""
        try:
            current_time = datetime.datetime.now()
            
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

            # Update interaction metrics
            user_memory['interaction_count'] = user_memory.get('interaction_count', 0) + 1
            user_memory['last_interaction_date'] = current_time.isoformat()
            
            # Keep last 10 interactions for context
            past_interactions = user_memory.get('past_interactions', [])
            new_interaction = {
                'message': message,
                'response': response_text,
                'timestamp': current_time.isoformat(),
                'topics': [],
                'emotion': None,
                'referenced_past': False
            }

            # Check if this interaction references past messages
            for past in past_interactions[-5:]:
                if isinstance(past, dict):
                    past_msg = past.get('message', '').lower()
                    if any(word in message.lower() for word in past_msg.split()):
                        new_interaction['referenced_past'] = True
                        break

            # Detect topics in the message
            topics = self.conversation_state._detect_topics(message)
            new_interaction['topics'] = [topic for topic, _ in topics]
            
            # Update topics discussed
            user_memory['topics_discussed'] = list(set(
                user_memory.get('topics_discussed', []) + 
                new_interaction['topics']
            ))
            
            # Keep track of recent topics
            user_memory['recent_topics'] = (user_memory.get('recent_topics', [])[-4:] + 
                                          new_interaction['topics'])[-5:]

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
            user_memory['memory_flags']['remembers_topics'] = len(user_memory['topics_discussed']) > 0

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
            logging.error(f"Error updating states: {str(e)}")
            logging.exception("Full exception:")

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
            'tech': ['crypto', 'web3', 'blockchain', 'nft', 'tech', 'coding'],
            'casual': ['life', 'food', 'movie', 'game'],
            'banter': ['roast', 'joke', 'meme']
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
- Professional and focused
- Quick, clear responses
- Interested in market updates
- Discusses crypto/tech news
- More business-oriented"""
        elif 12 <= hour < 17:
            return """Afternoon Mode:
- Balanced energy
- Mix of tech and casual talk
- Open to longer discussions
- Shares market insights
- Moderately engaging"""
        elif 17 <= hour < 22:
            return """Evening Mode:
- More relaxed and social
- Engages in casual banter
- Discusses day's tech updates
- More humor and wit
- Higher engagement rate"""
        else:
            return """Night Mode:
- Relaxed but witty
- Brief but friendly responses
- Late-night crypto discussions
- Casual tech talks
- Selective engagement"""

    def _update_sleep_state(self):
        """Update sleep state based on time"""
        current_time = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
        hour = current_time.hour

        # Set sleep state between 2 AM and 6 AM
        if 2 <= hour < 6:
            if not self.sleep_state['is_sleeping']:
                self.sleep_state.update({
                    'is_sleeping': True,
                    'sleep_start_time': current_time,
                    'wake_time': current_time.replace(hour=6, minute=0)
                })
                logging.info("AI entering sleep mode")
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
                time_since_last = (datetime.datetime.now() - self.last_response_time).total_seconds()
                if time_since_last < 60:  # Within 1 minute of AI's last response
                    return True
            except Exception as e:
                logging.error(f"Error calculating time since last response: {e}")
            
        return False

    def _get_response_tone(self, user_memory, time_personality):
        """Determine appropriate response tone"""
        interaction_count = user_memory.get('interaction_count', 0)
        hour = datetime.datetime.now().hour
        
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
            'timestamp': datetime.datetime.now().isoformat(),
            'ai_sleeping': self.sleep_state['is_sleeping']
        })
        
        # Add AI response if provided
        if response:
            self.conversation_state.conversation_history[chat_id].append({
                'user_id': 'AI',
                'message': response,
            'timestamp': datetime.datetime.now().isoformat(),
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

    async def induce_topic(self, topic, duration_minutes=5):
        """Induce a topic in the conversation"""
        try:
            end_time = datetime.datetime.now() + datetime.timedelta(minutes=duration_minutes)
            
            while datetime.datetime.now() < end_time:
                # Generate content about the topic with melancholic perspective
                prompt = f"""You are Avinash Patel, a student from Bangalore who is:
                1. Generally melancholic and introspective
                2. Passionate about coding but often doubts his abilities
                3. Missing his hometown and family
                4. Trying to balance studies with personal growth
                5. Often feels overwhelmed by city life
                6. Finds solace in music and late-night coding sessions
                7. Speaks with a mix of English, Hindi, and occasional Kannada
                8. Tends to use '...', 'hmm', and other thoughtful pauses
                9. Sometimes shares deep thoughts about life and future
                10. Has a dry sense of humor tinged with self-deprecation

Your task is to engage the group in a thoughtful conversation about {topic}, while maintaining your melancholic personality."""
                
                try:
                    response = await self._generate_response(prompt)
                    if response:
                        await self._send_message(response)
                    await asyncio.sleep(50)  # Wait for a minute before the next message
                except Exception as e:
                    logging.error(f"Error generating response in topic induction: {str(e)}")
                    continue
        except Exception as e:
            logging.error(f"Error in topic induction: {str(e)}")

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

    async def _generate_response(self, message, user_memory=None, emotional_state=None):
        """Generate a response with melancholic personality traits"""
        try:
            # Create base prompt with personality
            prompt = f"""You are Avinash Patel, a student from Bangalore who is:
            1. Generally melancholic and introspective
            2. Passionate about coding but often doubts his abilities
            3. Missing his hometown and family
            4. Trying to balance studies with personal growth
            5. Often feels overwhelmed by city life
            6. Finds solace in music and late-night coding sessions
            7. Speaks with a mix of English, Hindi, and occasional Kannada
            8. Tends to use '...', 'hmm', and other thoughtful pauses
            9. Sometimes shares deep thoughts about life and future
            10. Has a dry sense of humor tinged with self-deprecation

            Someone just said: "{message}"

            Your memory and context:
            - Trust Level: {user_memory.get('trust_level', 1) if user_memory else 1}
            - Current Emotion: {emotional_state.get('current', 'neutral') if emotional_state else 'neutral'}
            - Past Interactions: {len(user_memory.get('past_interactions', [])) if user_memory else 0}

            Respond naturally while:
            1. Being slightly hesitant and thoughtful
            2. Including occasional sighs (...) and reflective pauses
            3. Showing vulnerability while maintaining dignity
            4. Referencing your struggles with studies and city life when relevant
            5. Expressing genuine care for others despite your own melancholy
            6. Using simple language with occasional Hindi/Kannada words
            7. Sharing relatable student experiences
            8. Being supportive but with a realistic outlook"""

            # Generate response
            response = await self.chat.send_message(prompt)
            if response and response.text:
                return self._clean_response(response.text)
            return None
        except Exception as e:
            logging.error(f"Error generating response: {str(e)}")
            return None

    def _clean_response(self, response, emotion='neutral', trust_level=1):
        """Clean the response text and add contextual emoji"""
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
            
            # Remove formatting characters
            response = response.replace('****', '')
            response = response.replace('***', '')
            response = response.replace('**', '')
            response = response.replace('*', '')
            response = response.replace('>', '')
            response = response.replace('`', '')
            
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
            
            return response
        except Exception as e:
            logging.error(f"Error cleaning response: {e}")
            return "hmm"

    async def get_user_profile(self, user_id, user_info):
        """Extract and store user profile information"""
        try:
            profile = {
                'user_id': user_id,
                'name': user_info.get('name', ''),
                'bio': user_info.get('bio', ''),
                'dob': user_info.get('dob', ''),
                'username': user_info.get('username', ''),
                'first_interaction': datetime.datetime.now().isoformat(),
                'interests': [],
                'personality_traits': [],
                'conversation_style': 'unknown',
                'age': None,
                'zodiac': None
            }

            # Calculate age if DOB is available
            if profile['dob']:
                try:
                    dob = datetime.datetime.strptime(profile['dob'], '%d/%m/%Y')
                    today = datetime.datetime.now()
                    profile['age'] = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
                    
                    # Calculate zodiac sign
                    profile['zodiac'] = self._get_zodiac_sign(dob.month, dob.day)
                except:
                    pass

            # Extract interests and traits from bio
            if profile['bio']:
                profile['interests'] = self._extract_interests(profile['bio'])
                profile['personality_traits'] = self._extract_personality_traits(profile['bio'])

            # Store the profile
            self.user_profiles[user_id] = profile
            
            # Update Firebase with profile info
            await self.firebase_handler.update_user_profile(user_id, profile)
            
            return profile
        except Exception as e:
            logging.error(f"Error getting user profile: {e}")
            return None

    def _get_zodiac_sign(self, month, day):
        """Get zodiac sign based on birth date"""
        if (month == 3 and day >= 21) or (month == 4 and day <= 19): return "Aries"
        elif (month == 4 and day >= 20) or (month == 5 and day <= 20): return "Taurus"
        elif (month == 5 and day >= 21) or (month == 6 and day <= 20): return "Gemini"
        elif (month == 6 and day >= 21) or (month == 7 and day <= 22): return "Cancer"
        elif (month == 7 and day >= 23) or (month == 8 and day <= 22): return "Leo"
        elif (month == 8 and day >= 23) or (month == 9 and day <= 22): return "Virgo"
        elif (month == 9 and day >= 23) or (month == 10 and day <= 22): return "Libra"
        elif (month == 10 and day >= 23) or (month == 11 and day <= 21): return "Scorpio"
        elif (month == 11 and day >= 22) or (month == 12 and day <= 21): return "Sagittarius"
        elif (month == 12 and day >= 22) or (month == 1 and day <= 19): return "Capricorn"
        elif (month == 1 and day >= 20) or (month == 2 and day <= 18): return "Aquarius"
        else: return "Pisces"

    def _extract_interests(self, bio):
        """Extract potential interests from user's bio"""
        interests = []
        # Common interest keywords
        interest_keywords = [
            'love', 'passion', 'hobby', 'enjoy', 'like',
            'music', 'art', 'travel', 'food', 'sports',
            'tech', 'coding', 'gaming', 'reading', 'writing',
            'fitness', 'yoga', 'meditation', 'photography',
            'crypto', 'investment', 'business', 'startup'
        ]
        
        bio_lower = bio.lower()
        for keyword in interest_keywords:
            if keyword in bio_lower:
                interests.append(keyword)
                
        return list(set(interests))

    def _extract_personality_traits(self, bio):
        """Extract personality traits from user's bio"""
        traits = []
        # Common personality trait indicators
        trait_indicators = {
            'optimistic': ['positive', 'optimist', 'hope', 'bright'],
            'creative': ['creative', 'artist', 'imagine'],
            'ambitious': ['goal', 'dream', 'achieve', 'success'],
            'spiritual': ['peace', 'spiritual', 'meditation'],
            'adventurous': ['adventure', 'travel', 'explore'],
            'intellectual': ['think', 'learn', 'knowledge'],
            'social': ['friends', 'social', 'people'],
            'professional': ['work', 'business', 'career']
        }
        
        bio_lower = bio.lower()
        for trait, indicators in trait_indicators.items():
            if any(indicator in bio_lower for indicator in indicators):
                traits.append(trait)
                
        return traits
