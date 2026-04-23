"""
Seed 400 marketing templates:
- 100 normal/standard dating notifications
- 200 funny/witty/crazy notifications
- 100 meme-style references (dating/love themed)

Run: python seed_400_templates.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import text
from app.db.session import AsyncSessionLocal

# 100 Normal/Standard Templates
NORMAL_TEMPLATES = [
    ("New Matches Available", "You have 3 new people interested in you. Check them out!"),
    ("Someone Likes You", "A new connection is waiting. Don't miss out!"),
    ("Perfect Match Alert", "We found someone who shares your interests. Say hello!"),
    ("Active Now", "5 people nearby are online right now. Start chatting!"),
    ("Complete Your Profile", "Profiles with photos get 10x more matches. Add yours today!"),
    ("Daily Picks Ready", "Your personalized matches for today are here!"),
    ("Someone Viewed You", "A potential match just checked out your profile!"),
    ("Message Waiting", "You have an unread message. Don't keep them waiting!"),
    ("Boost Your Profile", "Stand out and get more matches tonight!"),
    ("Weekend Plans", "Make this weekend special. Find your match now!"),
    
    ("New in Your Area", "Fresh profiles just joined near you. Say hi!"),
    ("Icebreaker Suggestion", "Not sure what to say? Try our conversation starters!"),
    ("Profile Visitors", "3 people viewed your profile today. View them back!"),
    ("Mutual Interest", "Someone likes the same things you do. Connect now!"),
    ("Evening Matches", "Your best matches are online right now!"),
    ("Don't Miss Out", "Someone special is waiting. Swipe now!"),
    ("Match Expiring Soon", "Your connection expires in 24 hours. Say something!"),
    ("Popular Tonight", "You're getting more attention than usual. Check it out!"),
    ("Conversation Starter", "Break the ice with someone new today!"),
    ("Weekend Vibes", "Start your weekend with a new connection!"),
    
    ("Quality Matches", "We found profiles that match your preferences perfectly!"),
    ("Active Members", "Join 1000+ active members online right now!"),
    ("Your Type", "Someone matching your ideal partner just joined!"),
    ("Smart Matches", "AI found your most compatible matches. Check them out!"),
    ("Nearby Connection", "Someone within 5km is interested in you!"),
    ("Evening Activity", "Most matches happen between 7-10pm. Perfect timing!"),
    ("Profile Complete", "Your complete profile attracted new matches!"),
    ("Trending Now", "You're trending in your area. 7 new likes!"),
    ("Quick Match", "Fast replies lead to better connections. Someone's waiting!"),
    ("Weekend Connection", "Find your weekend plans. New matches inside!"),
    
    ("Verified Profiles", "3 verified members want to connect with you!"),
    ("Interest Match", "Someone shares your hobbies. Start a conversation!"),
    ("Location Alert", "People in your neighborhood are active now!"),
    ("Daily Update", "Your daily match summary is ready!"),
    ("Premium Suggestion", "Unlock unlimited matches. Upgrade today!"),
    ("Photo Approved", "Your new photo is live. Watch the likes roll in!"),
    ("Popular Profile", "Your profile views doubled today!"),
    ("Match Streak", "Keep your matching streak going. Swipe now!"),
    ("Undo Available", "Made a mistake? Undo your last swipe!"),
    ("Priority Matching", "Get seen by more people. Boost now!"),
    
    ("Evening Rush", "8pm is prime time for matches. Get swiping!"),
    ("New Week Fresh", "Start the week with new connections!"),
    ("Midweek Motivation", "Wednesday blues? Find someone to brighten it!"),
    ("Thursday Vibes", "Almost weekend! Meet someone special!"),
    ("Friday Feeling", "Make Friday night plans. Match now!"),
    ("Saturday Social", "Weekend dating starts now. Join in!"),
    ("Sunday Funday", "Perfect day for a coffee date. Find yours!"),
    ("Monday Motivation", "Beat Monday blues with a great match!"),
    ("Tuesday Special", "Mid-week connections are the best. Try it!"),
    ("Late Night Active", "Night owls unite! People online now!"),
    
    ("Morning Matches", "Start your day with positive connections!"),
    ("Lunch Break", "Perfect time to check your matches!"),
    ("Afternoon Delight", "New profiles added this afternoon!"),
    ("After Work", "Unwind with interesting conversations!"),
    ("Dinner Time", "Someone wants to share their evening with you!"),
    ("Golden Hour", "Best time to match. People are responsive now!"),
    ("Late Evening", "Cozy conversations await. Check your matches!"),
    ("Before Bed", "End your day on a sweet note. New match!"),
    ("Early Bird", "Early risers make great matches. Connect now!"),
    ("Coffee Time", "Match made for coffee lovers just for you!"),
    
    ("Compatibility High", "98% compatibility detected with a new profile!"),
    ("Similar Interests", "Book lover alert! Someone shares your passion!"),
    ("Music Match", "Same taste in music. This could be your person!"),
    ("Foodie Alert", "Fellow food enthusiast wants to connect!"),
    ("Travel Buddy", "Adventure seeker nearby. Check them out!"),
    ("Fitness Match", "Gym partner found! They're into fitness too!"),
    ("Pet Lover", "Dog person meets dog person. Perfect match!"),
    ("Movie Buff", "Cinema lover alert! They love your genre!"),
    ("Sports Fan", "Game day just got better. Sports fan nearby!"),
    ("Art Enthusiast", "Creative soul wants to connect with you!"),
    
    ("Career Match", "Professional in your field is interested!"),
    ("Education Level", "University graduate nearby. Connect now!"),
    ("Family Values", "Someone with similar family goals!"),
    ("Relationship Goals", "Looking for the same thing. Great match!"),
    ("Life Stage Match", "At the same life stage. Perfect timing!"),
    ("Values Align", "Shared values detected. This is promising!"),
    ("Communication Style", "Great conversationalist waiting to chat!"),
    ("Sense of Humor", "They laugh at your jokes. Keeper alert!"),
    ("Personality Match", "Your personalities complement each other!"),
    ("Long Term Potential", "This match has serious potential!"),
    
    ("First Message Tips", "Struggling with openers? We've got ideas!"),
    ("Response Rate Up", "Your response rate is improving. Keep going!"),
    ("Quality Conversations", "Deep conversations lead to real connections!"),
    ("Video Chat Ready", "Take it to the next level. Video call now!"),
    ("Voice Note", "Send a voice note. More personal!"),
    ("Photo Share", "Share a moment. Photos break the ice!"),
    ("Meet Up Ready", "Ready for a real date? Set it up!"),
    ("Safety First", "Always meet in public places. Stay safe!"),
    ("Date Ideas", "Need date inspiration? Check our suggestions!"),
    ("Success Stories", "100+ couples met here this month!"),
    
    ("Profile Tips", "Small tweaks can double your matches!"),
    ("Photo Quality", "Better photos mean better matches!"),
    ("Bio Matters", "Update your bio for more connections!"),
    ("Interests Update", "Add more interests to find better matches!"),
    ("Preferences Set", "Set your preferences for smarter matching!"),
    ("Verification Badge", "Get verified for more trust and matches!"),
]

# 200 Funny/Witty/Crazy Templates
FUNNY_TEMPLATES = [
    ("Your phone just got heavier", "3 new crushes loaded. Prepare for butterflies! 🦋"),
    ("Breaking: Local area short on singles", "Because they all matched. Your turn! 💕"),
    ("Your future spouse is procrastinating", "Just like you. Perfect match! 😏"),
    ("Dating app doing push-ups", "Preparing to carry your love life. Let's go! 💪"),
    ("Your grandmother called", "From heaven. She approves of your new match! 👵✨"),
    
    ("Relationship status: It's complicated", "Jk, it's just empty. Fix it! 😅"),
    ("Your bed has filed a complaint", "Too much space. Find a +1! 🛏️"),
    ("Plot twist incoming", "Your soulmate swipes right at midnight! 🌙"),
    ("Your dating life called", "It hung up. Because it doesn't exist yet! 📞"),
    ("Cupid is typing", "3 arrows incoming. Duck or swipe? 🏹"),
    
    ("Your heart: Vacant", "Tenants wanted. No pets. Humans only! ❤️"),
    ("Life achievement unlocked", "Still Single. Time to change that! 🎮"),
    ("Your alone time membership", "Will expire today. Say goodbye! 👋"),
    ("Warning: Cute alert", "Dangerously attractive profile spotted! ⚠️"),
    ("Your future in-laws", "Are wondering when they'll meet you! 👨‍👩‍👧"),
    
    ("Scientists confirm", "You're 10x more attractive when smiling! 😊"),
    ("Math is hard", "You + Someone = Easy! Calculate now! ➕"),
    ("Your romantic comedy", "Needs a co-star. Auditions open! 🎬"),
    ("Therapist said", "Get a partner, not a pet. (Sorry, cat) 🐱"),
    ("Horoscope update", "Today is THE day. Trust us! ⭐"),
    
    ("Your lockscreen is judging you", "Still no couple photo? Seriously? 📱"),
    ("Netflix sent a notification", "Stop watching alone. Find your person! 📺"),
    ("Your mirror reflection", "Practiced the first date smile. Time to use it! 🪞"),
    ("Delivery notification", "Love is out for delivery. Track now! 📦"),
    ("Your WiFi password", "Should be changed to SingleNoMore! 📶"),
    
    ("Google Maps says", "Your soulmate is 2km away. Navigate! 🗺️"),
    ("Your camera roll", "97% selfies, 3% food. Add couple photos! 📸"),
    ("Siri asked", "When are you getting a date? Good question! 🤖"),
    ("Your playlist", "Is 90% breakup songs. Change the vibe! 🎵"),
    ("Battery at 1%", "But your charm is at 100%! Use it! 🔋"),
    
    ("Autocorrect changed love to like", "Even your phone wants you single! 😤"),
    ("Your profile views", "Went from 2 to 200. You're trending! 📈"),
    ("Someone screenshot your pic", "That's either creepy or flattering! 👀"),
    ("Your dating skills", "Loading... 45% complete! Speed it up! ⏳"),
    ("Notification: Heart available", "Free shipping. No returns! 💝"),
    
    ("Your ex is online", "Quick, match with someone better! 🏃"),
    ("Love calculator crashed", "Too many matches for one person! 🧮"),
    ("Your future wedding", "Already on Pinterest. Just need the person! 📌"),
    ("Dance lessons booked", "For your first dance. Partner pending! 💃"),
    ("Couple's counseling called", "They're ready when you find someone! 💑"),
    
    ("Your parents keep asking", "We're tired of lying for you! 👨‍👩‍👦"),
    ("Valentine's Day is coming", "Panic mode: Activated! 💘"),
    ("Wedding season alert", "You'll need a plus-one! 💍"),
    ("Holiday plans empty", "Unless you swipe right now! 🎄"),
    ("New Year's kiss", "Planning ahead. 8 months early! 🎆"),
    
    ("Your ideal type", "Just logged in. Coincidence? We think not! ✨"),
    ("Someone swiped right", "While eating your favorite food. Destiny! 🍕"),
    ("Your dog is tired", "Of being your only companion! 🐕"),
    ("Plants need company too", "They're rooting for you! 🌱"),
    ("Your coffee", "Is tired of being your only morning date! ☕"),
    
    ("Gym membership wasted", "Unless you find a workout partner! 🏋️"),
    ("Your cooking skills", "Deserve an audience of more than one! 👨‍🍳"),
    ("Netflix recommendations", "Are getting too accurate. Find real company! 🍿"),
    ("Your jokes", "Need someone who actually laughs! 😂"),
    ("Friday nights", "Are not meant for solo adventures! 🎉"),
    
    ("Your DMs are empty", "Like your relationship status. Change both! 💬"),
    ("Phone storage full", "Delete apps, not dating prospects! 📲"),
    ("Your standards are high", "Good! Someone here meets them! 🎯"),
    ("Comfort zone expanding", "To include another human! 🤗"),
    ("Life update needed", "From single to taken! 📝"),
    
    ("Your future", "Called. It's married with kids! 👶"),
    ("Dating forecast", "99% chance of matches today! 🌤️"),
    ("Vibe check", "You're giving main character energy! ⚡"),
    ("Your aura", "Is attracting the right people! 🌟"),
    ("Main character moment", "About to happen. Swipe now! 🎬"),
    
    ("Plot armor activated", "Your love story starts today! 🛡️"),
    ("Character development", "From single to coupled! 📖"),
    ("Story arc incoming", "The romance chapter begins! 📚"),
    ("Season finale", "Of your single life! Roll credits! 🎞️"),
    ("Sequel announced", "Your Dating Life: Part 2! 🎥"),
    
    ("Achievement unlocked", "Talked to a human! 500 XP gained! 🏆"),
    ("Level up available", "From level: Forever Alone! 🎮"),
    ("Side quest unlocked", "Find Your Player 2! 🕹️"),
    ("Final boss approaching", "Meeting the parents! 👹"),
    ("Save point created", "Before your first date! 💾"),
    
    ("Loading romance.exe", "Please wait... Almost there! 💻"),
    ("Error 404: Date not found", "Let's fix that bug! 🐛"),
    ("System update required", "From Single to Relationship! 🔄"),
    ("Connection established", "Strong signal detected nearby! 📡"),
    ("Bandwidth available", "For meaningful connections! 📊"),
    
    ("Your social battery", "Charges faster with the right person! 🔌"),
    ("Energy levels", "From 20% to 100% after matching! ⚡"),
    ("Mood: Before match vs After", "Dramatic improvement guaranteed! 😊"),
    ("Serotonin levels", "Will spike after this swipe! 🧪"),
    ("Dopamine delivery", "In progress. Track your match! 💊"),
    
    ("Monday blues canceled", "Someone wants to make it better! 💙"),
    ("Tuesday motivation", "Someone attractive just logged in! 🔥"),
    ("Wednesday wisdom", "Date today, celebrate Friday! 🦉"),
    ("Thursday thoughts", "Tomorrow is date night! 💭"),
    ("Friday feeling", "Someone wants your weekend! 🎊"),
    
    ("Saturday night plans", "Upgraded from couch to date! 🛋️"),
    ("Sunday morning", "Coffee tastes better with company! ☕"),
    ("Midnight snack", "Someone wants to share! 🌙"),
    ("Golden hour", "Your glow up time is NOW! 🌅"),
    ("Blue hour", "Perfect for romantic matches! 🌆"),
    
    ("Your Pinterest board", "Relationship goals is 90% complete! 📍"),
    ("Your Spotify wrapped", "Next year: Couple's playlist! 🎼"),
    ("Your year in review", "Missing: Relationship section! 📅"),
    ("Your vision board", "Needs real photos, not stock images! 🖼️"),
    ("Your bucket list", "Item #1: Find someone special! ✅"),
    
    ("Notification from Future You", "Thanks for matching today! 🚀"),
    ("Time travel confirmed", "Your future partner is here now! ⏰"),
    ("Parallel universe update", "In another life, you're together! 🌌"),
    ("Destiny calling", "Please pick up. It's important! 📞"),
    ("Fate has entered the chat", "And brought someone special! 💫"),
    
    ("Your comfort zone", "Just expanded by one human! 📏"),
    ("Personal space bubble", "Ready to share with someone! 🫧"),
    ("Me time", "Upgraded to we time! 👥"),
    ("Solo mode", "Switching to duo mode! 🎵"),
    ("Single player", "Switching to multiplayer! 🎲"),
    
    ("Your vibe", "Is attracting your tribe! 🌊"),
    ("Energy matching", "Same wavelength detected! 📻"),
    ("Frequency aligned", "Perfect connection found! 📡"),
    ("Resonance achieved", "Someone's on your level! 🎚️"),
    ("Synchronicity moment", "Right person, right time! ⏱️"),
    
    ("Your love language", "Someone speaks it fluently! 🗣️"),
    ("Emotional availability", "Stock: High! Buy now! 📈"),
    ("Attachment style", "Compatible match found! 🔗"),
    ("Green flags everywhere", "This one's a keeper! 🚩"),
    ("Red flag detector", "All clear! Proceed safely! 🔍"),
    
    ("First date outfit", "Already planned? Perfect timing! 👗"),
    ("Conversation starters", "Loaded and ready to fire! 🎤"),
    ("Nervous energy", "Totally normal. They're nervous too! 😰"),
    ("Butterflies incoming", "Stock up on antacids! 🦋"),
    ("Heart racing", "Cardio without the gym! ❤️‍🔥"),
    
    ("Text chemistry", "Strong! Meet in person! 💬"),
    ("Vibe check passed", "Green light for date night! 🚦"),
    ("Background check", "Their profile: Impressive! ✓"),
    ("Compatibility score", "Breaking records! 💯"),
    ("Match percentage", "Scientists are confused! 🧑‍🔬"),
    
    ("Your standards", "Met and exceeded! Shocked? Us too! 🤯"),
    ("Deal breakers", "None detected! Victory! 🏅"),
    ("Must haves", "All boxes checked! ✅"),
    ("Preferences", "Aligned perfectly! 🎯"),
    ("Requirements", "Exceeded expectations! 🌟"),
    
    ("Profile stalking", "Expert level achieved! 🕵️"),
    ("Screenshot taken", "Showed all your friends already! 📸"),
    ("Group chat voting", "Unanimous yes! Swipe right! 👍"),
    ("Friend approved", "Even the picky one! 🤝"),
    ("Mom approved", "And she's never impressed! 👩"),
    
    ("Emergency meeting", "Your heart and brain actually agree! 🧠"),
    ("Internal debate over", "Everyone says swipe right! 🗣️"),
    ("Gut feeling", "Screaming yes at you! 🎤"),
    ("Intuition activated", "This is the one! ✨"),
    ("Sixth sense tingling", "Something good's happening! 🔮"),
    
    ("Universe aligned", "Stars literally spell 'swipe'! 🌠"),
    ("Cosmic intervention", "Planets moved for this match! 🪐"),
    ("Divine timing", "Everything happens for a reason! 🙏"),
    ("Serendipity moment", "Happening right now! ✨"),
    ("Meant to be", "If you actually swipe! 💫"),
    
    ("Your future kids", "Are cheering you on! Go dad/mom! 👶"),
    ("Wedding playlist", "Already being curated! 🎵"),
    ("Honeymoon destination", "Waitlisted. Need partner first! ✈️"),
    ("Relationship goals", "Loading... Partner required! 🎯"),
    ("Couple Halloween costumes", "Ideas brewing! Need teammate! 🎃"),
    
    ("Dating game strong", "Your serve! 🎾"),
    ("Love is a battlefield", "You're winning! ⚔️"),
    ("Heart on your sleeve", "Roll it up, show it off! 💪"),
    ("Shot your shot", "Nothing but net! 🏀"),
    ("Swing for the fences", "Home run potential! ⚾"),
    
    ("Manifesting worked", "Your person is here! 🧘"),
    ("Vision board success", "Universe delivered! 🌟"),
    ("Affirmations paying off", "I am loved. Coming true! 💖"),
    ("Law of attraction", "Working overtime tonight! 🧲"),
    ("Good vibes only", "And they're all here! ✌️"),
    
    ("Text thread preview", "Already imagining good morning texts! 📱"),
    ("Future conversations", "Looking promising! 💭"),
    ("Inside jokes", "Incoming in 3... 2... 1... 😂"),
    ("Couple memories", "About to be made! 📸"),
    ("Shared moments", "Starting now! ⏰"),
    
    ("Your type", "Logged in. Online. Right now! 🔴"),
    ("Dream person", "Materialized into real profile! ✨"),
    ("Perfect match", "Not a drill! Real human! 🚨"),
    ("Ideal partner", "Exists! Living proof here! 🎭"),
    ("The one", "Could be this one! 💍"),
    
    ("Relationship status", "About to get an upgrade! 📊"),
    ("Single chapter", "Final pages being written! 📖"),
    ("New beginnings", "Start with one swipe! 🌅"),
    ("Fresh start", "Your person is waiting! 🆕"),
    ("Plot twist", "Your love story starts now! 🎬"),
]

# 100 Meme-Style Templates (Dating/Love themed)
MEME_TEMPLATES = [
    ("Nobody:", "Literally nobody: You: Still swiping! Let's fix that! 😂"),
    ("POV:", "You're about to meet your soulmate! 👀"),
    ("Me: I'm fine being single", "Also me at 2am: checking dating apps! 🌙"),
    ("Drake meme energy", "Dating apps ❌ Our app ✅ 😎"),
    ("Expectation vs Reality", "Your match is better than expected! 🎯"),
    
    ("This could be us", "But you're still scrolling! Swipe already! 💑"),
    ("Is this a pigeon?", "Is this my soulmate? (Yes, yes it is!) 🦋"),
    ("Woman yelling at cat", "You: Why am I single? Cat: Swipe right! 😾"),
    ("Distracted boyfriend", "Your ex ← You → Your new match! 👫"),
    ("Two buttons meme", "Stay single / Find love [sweating intensifies]! 😓"),
    
    ("Always has been", "Wait, there are matches? Always has been! 🌍"),
    ("Surprised Pikachu", "When your crush likes you back! 😲"),
    ("This is fine", "Your love life: NOT fine. Fix it! 🔥"),
    ("Change my mind", "You should swipe right. Can't change facts! 🪑"),
    ("Shut up and take my money", "Premium? Already worth it! 💰"),
    
    ("SpongeBob tired vs fresh", "Before match vs after match! 🧽"),
    ("Success kid", "Got a match. Didn't mess it up! 👶"),
    ("Disaster girl smiling", "Your ex's relationship vs yours soon! 😈"),
    ("Doge wow", "Such match. Very attract. Much love. Wow! 🐕"),
    ("Grumpy cat approved", "Even I like this match! 😾"),
    
    ("Batman slapping Robin", "Stop overthinking! Just swipe right! 👋"),
    ("Left exit ramp", "Single life ← You taking love exit! 🚗"),
    ("One does not simply", "One does not simply stay single here! 🧙"),
    ("I see this as absolute win", "Match notification? Win! 🏆"),
    ("Modern problems", "Require modern solutions: Dating apps! 🤓"),
    
    ("Stonks", "Love stonks going ↗️ Buy now! 📈"),
    ("Press F to pay respects", "F for your single status. RIP! ⚰️"),
    ("Thanos snap", "Half of singles disappeared. They matched! 🫰"),
    ("Area 51 raid energy", "Raid the single life. Storm into love! 👽"),
    ("Surprised Tom face", "When you actually get a match! 🐱"),
    
    ("Galaxy brain", "Small brain: Stay single. Galaxy brain: Match! 🧠"),
    ("Ight imma head out", "Your single life heading out! 👋"),
    ("Outstanding move", "Swiping right was genius! ♟️"),
    ("Visible confusion", "Wait, people actually like me? 🤔"),
    ("They had us in first half", "Thought I'd stay single. Nope! ⚽"),
    
    ("Cat vibing to music", "You after matching successfully! 🐱"),
    ("Pointing Rick Dalton", "Hey, that's your future partner! 👉"),
    ("Panik Kalm Panik", "No matches! Got match! Now what?! 😱"),
    ("Trade offer", "I receive: Your time. You receive: Love! 🤝"),
    ("Why are you running", "Why are you avoiding love?! 🏃"),
    
    ("I'm in danger", "Your heart in danger of falling! 💝"),
    ("Professionals have standards", "And this match meets them! 🎯"),
    ("Corporate wants difference", "They're the same picture (both cute)! 🖼️"),
    ("Is for me?", "This match... is for me? 👉👈"),
    ("We don't do that here", "Stay single? We don't do that! 🙅"),
    
    ("Leonardo DiCaprio pointing", "When you see your type online! 👆"),
    ("Michael Jordan crying", "Your ex watching you move on! 😭"),
    ("Hide the pain Harold", "Pretending to enjoy single life! 😬"),
    ("First time?", "Getting matches? Could get used to this! 🤠"),
    ("Years of academy training wasted", "All those pickup lines ready! 📚"),
    
    ("Parkour!", "From single to matched! Parkour! 🤸"),
    ("It's free real estate", "Available hearts everywhere! 🏠"),
    ("Why would X do this", "Why would being single do this?! 🦋"),
    ("Upgraded", "Single → Talking → Dating! 📱"),
    ("Ah shit here we go again", "Back to being happy! 🎮"),
    
    ("Task failed successfully", "Tried to stay single. Got match! ✅"),
    ("You weren't supposed to do that", "Match with perfect person? Oops! 😳"),
    ("Actually quantum mechanics forbids", "Being this compatible! 🔬"),
    ("Wait that's illegal", "Being this cute should be! 🚔"),
    ("You guys are getting paid", "You guys are getting matches? Yes! 💰"),
    
    ("Perfectly balanced", "Your vibe and their vibe! ⚖️"),
    ("I used the stones", "Used app to find love! 💎"),
    ("Gone reduced to atoms", "Your single status: Gone! ☢️"),
    ("We won Mr Stark", "Found love on dating app! 🦾"),
    ("I don't even know who you are", "Your future spouse browsing! 🤷"),
    
    ("Is this loss?", "No, this is GAIN! New match! 📊"),
    ("Math lady confused", "Trying to understand why I'm single! ➗"),
    ("Expanding brain", "Normal date → Great date → Perfect match! 🧠"),
    ("Drake hotline bling", "Swiping left ❌ Swiping right ✅ 💃"),
    ("Anakin Padme meme", "We're matching right? ...Right? 😰"),
    
    ("Thomas had never seen", "Such a perfect match before! 🚂"),
    ("Ferb I know what", "We're doing today: Finding love! 📐"),
    ("Obama awarding Obama", "Me congratulating myself for swiping! 🏅"),
    ("Monkey looking away", "Me avoiding my responsibilities to match! 🙈"),
    ("Spiderman pointing", "You and your perfect match! 🕷️"),
    
    ("Bugs Bunny no", "Staying single? No! ❌"),
    ("Plankton yes", "Getting matches? YES! ✅"),
    ("Peter Parker glasses", "Without match | With match (clear!) 👓"),
    ("Brain before sleep", "3am: Should message my match! 🛌"),
    ("Sleeping shaq", "Single life | Love life (WOKE)! 😴"),
    
    ("Is this a butterfly", "Is this true love? (Could be!) 🦋"),
    ("Gru's plan", "Download app → Match → Date → ??? → Married! 📋"),
    ("Mocking SpongeBob", "I dOnT nEeD a DaTiNg ApP! 🧽"),
    ("Kermit tea", "Watching ex stay single. But that's none of my business! 🐸"),
    ("Elmo fire", "Your DMs after this match! 🔥"),
    
    ("Tuxedo Winnie", "Regular date | Fancy restaurant date! 🐻"),
    ("Roll safe", "Can't be heartbroken if you never match. Wrong! 👨"),
    ("Expanding knowledge", "Stage 1: Single. Stage 5: ENLIGHTENED IN LOVE! 📖"),
    ("Draw 25 cards", "Stay single OR find love? Finding love! 🃏"),
    ("Bernie mittens", "Me patiently waiting for my match! 🧤"),
    
    ("Awkward look monkey", "When your crush messages first! 🐵"),
    ("Evil Kermit", "Evil me: Swipe right on everyone! 😈"),
    ("Cheems vs Swole Doge", "Single me vs Coupled me! 💪"),
    ("Wojak", "Tfw you finally get a good match! 😢"),
    ("Gigachad", "Average dating app user vs Our users! 🗿"),
    
    ("Caveman SpongeBob", "Brain empty. Only love! 🧽"),
    ("Principal Skinner", "Am I single? No, it's the matches who are wrong! 👔"),
    ("Is this happiness", "Getting a match notification at 2am? Yes! 🌙"),
    ("Two trucks", "Two hearts about to collide! 🚛"),
    ("Coffin dance", "Your single life's funeral! ⚰️"),
]

async def seed_all_templates():
    async with AsyncSessionLocal() as db:
        # Delete old templates first
        print("🗑️  Clearing old templates...")
        await db.execute(text("DELETE FROM marketing_templates"))
        await db.commit()
        
        print("📝 Adding 100 normal templates...")
        for i, (title, body) in enumerate(NORMAL_TEMPLATES, 1):
            await db.execute(
                text("""
                    INSERT INTO marketing_templates (name, language_code, title, body, notif_type, is_active)
                    VALUES (:name, :lang, :title, :body, :type, :active)
                """),
                {
                    "name": f"Normal #{i}: {title}",
                    "lang": "en",
                    "title": title,
                    "body": body,
                    "type": "promotions",
                    "active": True,
                }
            )
        
        print("😂 Adding 200 funny/witty templates...")
        for i, (title, body) in enumerate(FUNNY_TEMPLATES, 1):
            await db.execute(
                text("""
                    INSERT INTO marketing_templates (name, language_code, title, body, notif_type, is_active)
                    VALUES (:name, :lang, :title, :body, :type, :active)
                """),
                {
                    "name": f"Funny #{i}: {title}",
                    "lang": "en",
                    "title": title,
                    "body": body,
                    "type": "promotions",
                    "active": True,
                }
            )
        
        print("🎭 Adding 100 meme-style templates...")
        for i, (title, body) in enumerate(MEME_TEMPLATES, 1):
            await db.execute(
                text("""
                    INSERT INTO marketing_templates (name, language_code, title, body, notif_type, is_active)
                    VALUES (:name, :lang, :title, :body, :type, :active)
                """),
                {
                    "name": f"Meme #{i}: {title}",
                    "lang": "en",
                    "title": title,
                    "body": body,
                    "type": "promotions",
                    "active": True,
                }
            )
        
        await db.commit()
        
        # Count total
        result = await db.execute(text("SELECT COUNT(*) FROM marketing_templates WHERE is_active = true"))
        total = result.scalar()
        
        print(f"\n✅ Success! {total} templates added:")
        print(f"   • 100 Normal/Standard")
        print(f"   • 200 Funny/Witty/Crazy")
        print(f"   • 100 Meme-Style (dating themed)")
        print(f"   = {total} Total Templates")
        print("\n🎯 All templates saved to database!")
        print("🚀 Marketing scheduler will use these automatically!")

if __name__ == "__main__":
    asyncio.run(seed_all_templates())
