from typing import Dict, Any, Optional
import time
import random
import logging

class SpecialUsersHandler:
    """Handler for managing special user interactions and permissions."""
    
    def __init__(self):
        self._special_users = {}
        self._current_topic = None
        self._topic_start_time = None
        self._last_message_time = None
        self._conversation_active = False
        self._message_count = 0  # Track number of messages in current topic
        self._conversation_stage = "START"  # Track conversation stage
        self._consecutive_dry_messages = 0  # Track boring/dry messages
        self._last_user_messages = []  # Track recent messages for context
        self._topics = {
            'politics': ['modi', 'rahul', 'gandhi', 'election', 'government'],
            'crypto': ['crypto', 'bitcoin', 'eth', 'blockchain', 'token', 'nft', 'defi', 'web3', 'trading', 'mining', 'hodl', 'altcoin'],
            'tech': ['coding', 'programming', 'software', 'ai', 'tech', 'dev', 'machine learning', 'cybersecurity', 'startup', 'data science'],
            'gaming': ['game', 'gaming', 'steam', 'discord', 'twitch', 'esports', 'xbox', 'playstation', 'nintendo', 'fps', 'mmorpg'],
            'memes': ['meme', 'troll', 'lol', 'lmao', 'kek', 'based', 'chad', 'copypasta', 'ratio', 'cringe', 'sus'],
            'relationships': ['dating', 'crush', 'love', 'relationship', 'breakup', 'wedding', 'marriage', 'romance', 'flirting', 'dating_advice'],
            'entertainment': ['movies', 'tv_shows', 'netflix', 'anime', 'manga', 'kdrama', 'series', 'binge_watching', 'streaming', 'cinema'],
            'music': ['spotify', 'playlist', 'rap', 'hiphop', 'rock', 'pop', 'concert', 'album', 'artist', 'festival', 'lyrics'],
            'celebrities': ['celebrity', 'actor', 'actress', 'singer', 'influencer', 'youtube', 'hollywood', 'bollywood', 'drama', 'gossip'],
            'sports': ['football', 'basketball', 'soccer', 'nba', 'fifa', 'athlete', 'sports', 'match', 'championship', 'tournament'],
            'fashion': ['fashion', 'style', 'outfit', 'brands', 'streetwear', 'luxury', 'sneakers', 'accessories', 'shopping', 'trend'],
            'food': ['food', 'cuisine', 'restaurant', 'cooking', 'recipe', 'foodie', 'dinner', 'snacks', 'drinks', 'cocktails'],
            'fitness': ['gym', 'workout', 'fitness', 'health', 'nutrition', 'diet', 'exercise', 'gains', 'trainer', 'bodybuilding'],
            'travel': ['travel', 'vacation', 'adventure', 'destination', 'wanderlust', 'backpacking', 'tourism', 'explore', 'trip'],
            'humor': ['jokes', 'funny', 'comedy', 'puns', 'roast', 'sarcasm', 'humor', 'witty', 'comeback', 'savage'],
            'philosophy': ['philosophy', 'deep_thoughts', 'meaning', 'wisdom', 'consciousness', 'reality', 'existence', 'universe'],
            'current_events': ['news', 'politics', 'trending', 'viral', 'controversy', 'debate', 'discussion', 'opinion', 'headlines'],
            'art': ['art', 'design', 'photography', 'drawing', 'digital_art', 'creative', 'illustration', 'artist', 'artwork'],
            'education': ['study', 'college', 'university', 'academics', 'learning', 'student', 'exams', 'courses', 'knowledge'],
            'career': ['job', 'work', 'career', 'business', 'entrepreneurship', 'success', 'motivation', 'goals', 'networking'],
            'mental_health': ['mental_health', 'therapy', 'anxiety', 'depression', 'self_care', 'mindfulness', 'stress', 'healing'],
            'social_life': ['friends', 'party', 'hangout', 'social', 'meetup', 'gathering', 'crew', 'squad', 'vibes'],
            'pets': ['pets', 'dogs', 'cats', 'animals', 'cute', 'wholesome', 'rescue', 'adoption', 'veterinary'],
            'science': ['science', 'space', 'physics', 'biology', 'chemistry', 'research', 'discovery', 'innovation'],
            'astrology': ['horoscope', 'zodiac', 'astrology', 'mercury_retrograde', 'stars', 'cosmic', 'energy', 'vibes'],
            'conspiracy': ['conspiracy', 'theories', 'mysterious', 'paranormal', 'aliens', 'unexplained', 'truth', 'secrets']
        }
        
        # Add conversation end indicators
        self._end_indicators = {
            'direct': [
                # English direct endings
                'bye', 'goodbye', 'cya', 'see you', 'gtg', 'got to go', 'gotta go', 'talk later', 'ttyl', 'catch you later',
                'peace out', 'im out', "i'm out", 'heading out', 'leaving now', 'good night', 'gn', 'night', 'later',
                'until next time', 'take care', 'farewell', 'signing off', 'brb', 'be right back', 'afk',
                
                # Hinglish/Hindi direct endings
                'chalta hu', 'chalti hu', 'alvida', 'phir milenge', 'milte hai', 'baad me baat karte', 'baad me baat karenge',
                'bye bye', 'tata', 'khuda hafiz', 'allah hafiz', 'ram ram', 'jai shree krishna', 'namaste', 'chalo bye',
                'nikalta hu', 'nikalti hu', 'jaata hu', 'jaati hu', 'jaana hai', 'sona hai', 'so raha hu', 'so rahi hu'
            ],
            
            'indirect': [
                # Short/minimal responses
                'hmm', 'hmmmm', 'hm', 'mhm', 'k', 'kk', 'ok', 'okay', 'achha', 'acha', 'thik', 'thik hai', 'cool',
                'fine', 'whatever', 'alright', 'sure', 'right', 'got it', 'understood', 'i see', 'ic', 'oh',
                
                # Disinterested responses
                'busy now', 'not now', 'some other time', 'baad me', 'abhi nahi', 'thoda busy hu', 'kaam hai',
                'let me think', 'will see', 'dekhta hu', 'dekhti hu', 'sochke batata hu', 'sochke batati hu',
                'maybe later', 'shayad', 'pata nahi', 'dekha jayega', 'dekhenge',
                
                # Tired/sleepy responses
                'getting sleepy', 'neend aa rahi', 'thak gaya', 'thak gayi', 'tired', 'exhausted',
                'need to sleep', 'sona hai', 'rest karna hai', 'thoda rest kar leta hu', 'thoda rest kar leti hu'
            ],
            
            'dry_responses': [
                # Single word answers
                'yes', 'no', 'maybe', 'idk', 'dunno', 'perhaps', 'possibly', 'nah', 'nope', 'yep', 'yeah',
                'ha', 'nahi', 'shayad', 'haan', 'na', 'bilkul', 'never', 'kabhi nahi', 'dont know', 'pata nahi',
                
                # Very short phrases
                'cant say', 'who knows', 'no idea', 'not sure', 'keh nahi sakte', 'ho sakta hai',
                'kya pata', 'kaun jaane', 'lets see', 'dekhte hai', 'time batayega', 'jaane do',
                
                # Dismissive responses
                'whatever', 'jo bhi', 'kuch bhi', 'as you say', 'jaisa tum kaho', 'theek hai', 'chalo theek hai',
                'if you say so', 'your choice', 'your wish', 'tumhari marzi', 'tum jaano'
            ]
        }
        
        # Add topic change triggers
        self._topic_change_triggers = {
            'boredom': [
                # Direct boredom
                'boring', 'bored', 'bore ho gaya', 'bore ho raha', 'not interesting', 'meh', 'whatever', 'bakwas',
                'faltu', 'bekar', 'time waste', 'same old', 'purani baat', 'kuch naya batao', 'topic change karo',
                'kuch aur baat karte', 'something else', 'move on', 'next topic', 'aage badho',
                
                # Indirect boredom
                'lets talk about something else', 'kuch aur discuss karte', 'ye topic chodo',
                'heard enough about this', 'bahut ho gaya', 'kitna discuss karoge', 'kuch naya sunao',
                'purani baatein chodo', 'fresh topic', 'naya topic', 'change the subject'
            ],
            
            'interest': [
                # Direct interest
                'wow', 'interesting', 'tell me more', 'aur batao', 'nice', 'amazing', 'awesome', 'cool story',
                'fascinating', 'mind blowing', 'unbelievable', 'seriously?', 'no way!', 'really?', 'sachi?',
                
                # Follow-up interest
                'then what happened?', 'fir kya hua?', 'aage kya hua?', 'and then?', 'uske baad?',
                'tell me everything', 'puri story batao', 'full details do', 'elaborate please',
                'explain more', 'samjhao zara', 'thoda detail me batao'
            ],
            
            'disagreement': [
                # Direct disagreement
                'disagree', 'nah', 'no way', 'galat', 'wrong', 'incorrect', 'not true', 'false', 'fake',
                'dont agree', 'i differ', 'not correct', 'mistake', 'error', 'misunderstanding',
                
                # Strong disagreement
                'bilkul galat', 'ekdum wrong', 'totally incorrect', 'completely wrong', 'absolutely not',
                'never possible', 'impossible', 'no chance', 'kabhi nahi', 'bilkul nahi',
                
                # Polite disagreement
                'i think differently', 'mere hisab se', 'mere khayal se', 'mera opinion alag hai',
                'i have different view', 'i see it differently', 'not necessarily', 'not always'
            ],
            
            'confusion': [
                # Question markers
                'what?', 'huh?', 'kya?', 'samajh nahi aaya', 'come again?', 'pardon?', 'excuse me?',
                'what do you mean?', 'matlab?', 'meaning?', 'explain please', 'clarity please',
                
                # Confusion expressions
                'confused', 'confusing', 'complex', 'complicated', 'difficult to understand',
                'samajh ke bahar', 'sir ke upar se', 'kuch samajh nahi aa raha', 'thoda confuse hu',
                
                # Clarification requests
                'can you explain?', 'please clarify', 'detail me batao', 'thoda clear karo',
                'better explain karo', 'dubara samjhao', 'ek baar fir se', 'once more please'
            ],
            
            'topic_shift': [
                # Natural transitions
                'speaking of which', 'by the way', 'that reminds me', 'now that you mention it',
                'iska matlab yaad aaya', 'is silsile me', 'baat baat me', 'khayal aaya',
                
                # Deliberate shifts
                'changing topics', 'different note', 'switching gears', 'moving on to',
                'dusri baat', 'ek aur baat', 'waise suno', 'ek topic aur hai',
                
                # Interruption shifts
                'before i forget', 'yaad aaya', 'oh wait', 'ek minute', 'suddenly remembered',
                'achanak se yaad aaya', 'important baat', 'zaruri topic'
            ]
        }
        
        # Configure logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger('SpecialUsersHandler')
    
    def is_special_user(self, user_id: str) -> bool:
        """Check if a user is designated as special."""
        is_special = user_id in self._special_users
        self.logger.info(f"User {user_id} identified as: {'Special User' if is_special else 'Normal User'}")
        return is_special
    
    def add_special_user(self, user_id: str, privileges: Dict[str, Any]) -> None:
        """Add or update a special user with specific privileges."""
        self._special_users[user_id] = privileges
        
    def remove_special_user(self, user_id: str) -> None:
        """Remove a user from special users list."""
        self._special_users.pop(user_id, None)
        
    def get_user_privileges(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve special privileges for a user if they exist."""
        return self._special_users.get(user_id)
    
    def _log_state(self, user_id: str):
        """Log current conversation state and topic."""
        self.logger.info(f"USER: {user_id} | STAGE: {self._conversation_stage} | TOPIC: {self._current_topic} | MSG COUNT: {self._message_count}")
    
    def _detect_conversation_end(self, message: str) -> bool:
        """Detect if conversation should end based on message patterns."""
        message_lower = message.lower()
        
        # Direct end indicators
        if any(end in message_lower for end in self._end_indicators['direct']):
            return True
            
        # Check for consecutive dry responses
        if any(dry in message_lower for dry in self._end_indicators['dry_responses']):
            self._consecutive_dry_messages += 1
        else:
            self._consecutive_dry_messages = 0
            
        # End if too many dry responses
        if self._consecutive_dry_messages >= 3:
            return True
            
        # Check for indirect endings with context
        if any(end in message_lower for end in self._end_indicators['indirect']):
            if len(self._last_user_messages) >= 2:
                # If previous messages were also indirect/dry, likely end of conversation
                prev_msgs = ' '.join(self._last_user_messages[-2:]).lower()
                if any(end in prev_msgs for end in self._end_indicators['indirect']):
                    return True
        
        return False

    def _should_change_topic(self, message: str) -> bool:
        """Determine if we should change the topic based on conversation flow."""
        message_lower = message.lower()
        
        # Direct topic change triggers
        for trigger_type, triggers in self._topic_change_triggers.items():
            if any(trigger in message_lower for trigger in triggers):
                return True
        
        # Change topic if the conversation is stale (short responses)
        if len(message.split()) <= 3 and self._message_count > 5:
            self._consecutive_dry_messages += 1
            if self._consecutive_dry_messages >= 2:
                return True
        
        # Random topic change to keep things interesting (20% chance after 8 messages)
        if self._message_count > 8 and random.random() < 0.2:
            return True
            
        return False

    def _is_message_targeted(self, message: str, group_members: list) -> bool:
        """Check if message is targeted to someone else (improved reply handling)"""
        if not message or not group_members:
            return False
            
        message_lower = message.lower().strip()
        
        # Track time between messages from same user to detect conversations
        current_time = time.time()
        
        # 1. Check for direct targeting of other users
        
        # 1a. Check @ mentions
        if '@' in message_lower:
            # If it's @AadityaSingh or similar variations, message is for AI
            if any(ai_name in message_lower for ai_name in ['@aditya', '@aaditya', '@adityasingh', '@aadityasingh']):
                return False
            # Otherwise message is for someone else
            return True
            
        # 1b. Check name mentions
        for member in group_members:
            member_name = str(member).lower()
            # Skip if it's AI's name
            if any(ai_name in member_name for ai_name in ['aditya', 'aaditya', 'adityasingh', 'aadityasingh']):
                continue
            # If message contains other user's name, it's targeted at them
            if member_name in message_lower:
                return True
                
        # 2. Check for conversation context
        if len(self._last_user_messages) >= 2:
            prev_msg = self._last_user_messages[-2].lower()
            prev_time = getattr(self, '_last_message_time', 0)
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
        
        # 3. Check if message is a reply to AI's message
        if hasattr(self, 'last_ai_message') and self.last_ai_message:
            if message.startswith('@') or message.lower().startswith(('re:', 'replying to')):
                # Check if replying to AI's last message
                ai_mentions = ['@aditya', '@aaditya', '@adityasingh']
                if any(mention in message.lower() for mention in ai_mentions):
                    return False  # Message is for AI
        
        # Update last message time
        self._last_message_time = current_time
        
        # If none of the above conditions match, message is not targeted
        return False

    def handle_message(self, message: str, user_id: str, group_members: list = None) -> str:
        """Handle incoming messages with improved flow control and targeting detection."""
        current_time = time.time()
        
        # Skip if message is targeted to someone else
        if group_members and self._is_message_targeted(message, group_members):
            self.logger.info(f"Message from {user_id} appears to be targeted to another user, skipping response")
            return None
        
        # Update message history
        self._last_user_messages.append(message)
        if len(self._last_user_messages) > 5:
            self._last_user_messages.pop(0)
        
        # Skip if message is targeted to someone else or part of a user conversation
        if group_members and self._is_message_targeted(message, group_members):
            self.logger.info(f"Message from {user_id} appears to be part of a user conversation, skipping response")
            return None
        
        # Check for conversation end
        if self._detect_conversation_end(message):
            self._conversation_active = False
            self.logger.info(f"CONVERSATION ENDED with user {user_id}")
            return "Alright then, catch you later! Was fun chatting 😎"
        
        # Initialize conversation if not active
        if not self._conversation_active:
            self._conversation_active = True
            self._last_message_time = current_time
            self._conversation_stage = "START"
            self._message_count = 0
            self._consecutive_dry_messages = 0
            self._log_state(user_id)
            return self.initiate_topic_discussion(user_id)
        
        # Increment message count
        self._message_count += 1
        
        # Check for topic change
        if self._should_change_topic(message) or self._message_count >= 15:
            self._conversation_stage = "CONCLUSION"
            self._log_state(user_id)
            
            conclusion = self.conclude_topic_discussion()
            
            self._conversation_stage = "SHIFTING"
            self._log_state(user_id)
            
            new_topic = self.initiate_topic_discussion(user_id)
            self._message_count = 0
            self._consecutive_dry_messages = 0
            
            # Add some personality to topic changes
            transitions = [
                "Ye topic boring ho gaya, let's talk about something more interesting!",
                "Arre wait, you know what's even cooler?",
                "Speaking of that, I just remembered something epic!",
                "Bro check this out, you'll love this topic!",
                "That reminds me of something way more fun!"
            ]
            
            return f"{conclusion}\n\n{random.choice(transitions)}\n\n{new_topic}"
        
        # Update stage to ONGOING after START
        if self._conversation_stage == "START" and self._message_count > 1:
            self._conversation_stage = "ONGOING"
        
        self._log_state(user_id)
        self._last_message_time = current_time
        return None
    
    def initiate_topic_discussion(self, user_id: str) -> str:
        """Initiate a topic discussion with more engaging hooks."""
        available_topics = [topic for topic in self._topics.keys() if topic != self._current_topic]
        self._current_topic = random.choice(available_topics)
        self._topic_start_time = time.time()
        self._conversation_stage = "START"
        self._message_count = 0
        
        # More engaging topic starters
        topic_intros = [
            f"Bro you won't believe what's happening in {self._current_topic} these days!",
            f"Aye check this out - what's your take on {self._current_topic}?",
            f"Speaking of epic stuff, let's talk about {self._current_topic}! You into this?",
            f"Yooo, {self._current_topic} is absolutely wild right now! What do you think?",
            f"Been meaning to ask - what's your opinion on {self._current_topic}?"
        ]
        
        self.logger.info(f"NEW TOPIC CREATED: {self._current_topic} for user {user_id}")
        return random.choice(topic_intros)
    
    def conclude_topic_discussion(self) -> str:
        """Conclude topic with personality."""
        if self._current_topic is not None:
            topic = self._current_topic
            self._current_topic = None
            
            # Fun conclusions
            conclusions = [
                f"That's enough about {topic} for now, getting kinda stale",
                f"Damn we really went deep into {topic} huh? Time for something fresh!",
                f"Aight, {topic} was fun but I got something even better",
                f"Not gonna lie, {topic} is cool but wait till you hear this",
                f"Bro you clearly know your {topic} stuff! But check this out"
            ]
            
            self.logger.info(f"CONCLUDING TOPIC: {topic}")
            return random.choice(conclusions)
        return None
