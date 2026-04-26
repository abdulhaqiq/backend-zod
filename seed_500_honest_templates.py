import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import text
from app.db.session import AsyncSessionLocal

# 1. 200 FUNNY & NORMAL - Real Dating Vibes
FUNNY_AND_NORMAL_TEMPLATES = [
    # --- FUNNY ---
    ("Your future partner is out there, lost", "They're waiting for someone to find them. That someone is you. Open the app and go save them from swiping forever.", "promotions"),
    ("Your couch is not a dating strategy", "Comfortable? Yes. Effective? No. The app is where connections happen. Your couch can wait.", "promotions"),
    ("Somewhere, someone just opened the app thinking about you", "Or not. But they could be. Only one way to find out. Log in and check.", "promotions"),
    ("Your grandma would be so proud right now", "If you opened the app, that is. She's not proud yet. Give her a reason to brag at bingo night.", "promotions"),
    ("Dating tip: Being on the app helps", "Revolutionary advice from the app itself. Science. Probably.", "promotions"),
    ("Your love story is buffering", "Poor connection. Solution: Open the app. Better signal. Better matches. Fewer loading screens.", "promotions"),
    ("Your pet thinks you should date more", "They told us. We're not lying. They're very concerned about your social life.", "promotions"),
    ("Wishing for love without trying is just wishing", "Make your wishes work overtime. Open the app and give them a fighting chance.", "promotions"),
    ("Alert: You haven't made anyone smile today", "Let's fix that. Open the app and start a conversation. You're funnier than you think.", "promotions"),
    ("Your phone has 47 apps. This one actually matters.", "Open it. Scroll. Swipe. Chat. Actual human connection. Better than all 47 others combined.", "promotions"),

    ("Your dating life called. It went to voicemail.", "Call it back. Open the app. Have a real conversation for once. It misses you.", "promotions"),
    ("You've been 'taking a break' for 6 months", "Vacation is over. Time to clock back in. Your love life submitted a return-to-work request.", "promotions"),
    ("New research shows: Trying works", "Sample size of millions. Conclusion: Using the app increases your chances of finding love. Bold findings.", "promotions"),
    ("Your profile is aging like a fine milk", "Update it before it curdles completely. Fresh photos, honest bio, active presence. Do it now.", "promotions"),
    ("Cupid filed a complaint about you", "Too passive. Too unavailable. Too in love with your couch. Address the complaint. Open the app.", "promotions"),
    ("Breaking news: Your soulmate is available right now", "Source: Probably accurate. Open the app to confirm. Don't let this news story go cold.", "promotions"),
    ("You've watched three seasons of something today", "You could have had three good conversations instead. Balance. The app takes 15 minutes. Try it.", "promotions"),
    ("Your future wedding speech needs material", "'We matched on an app after I finally stopped being lazy' is actually a great story. Start writing it.", "promotions"),
    ("Even your autocorrect thinks you should date", "It keeps changing 'procrastinating' to 'participating'. Your phone is smarter than your excuses.", "promotions"),
    ("Third wheel alert incoming", "Your friends have a couples' trip planned. You need a plus one. Start chatting. You have two weeks.", "promotions"),

    ("Your delivery driver knows your order by heart", "They shouldn't know you this well. Find someone else to share meals with. The app can help with that.", "dating_tips"),
    ("You've rewatched your comfort show 4 times this year", "Nothing wrong with that. But imagine watching it with someone who hasn't seen it? The app awaits.", "dating_tips"),
    ("Your Friday night: 10/10 for comfort, 0/10 for progress", "We're not judging. We are a little. Open the app for 20 minutes. Effort upgrade unlocked.", "dating_tips"),
    ("Your self-care routine is immaculate. Your dating life? TBD.", "You're glowing, moisturized, and still single. Let's fix that last part. Profile update + app = change.", "dating_tips"),
    ("You argued with someone online today for free", "Use that energy to chat someone up on the app. Better ROI. Potential relationship. Less cortisol.", "dating_tips"),
    ("Your Spotify is screaming for you", "The playlist titled 'sad hours' isn't subtle. We heard it. Make a new playlist called 'going on dates'.", "dating_tips"),
    ("You've meal-prepped your lunches but not your love life", "Impressive dedication to one area. Zero to the other. Balance is key. Open the app and prep.", "dating_tips"),
    ("Your comfort zone is very comfortable", "Too comfortable. Time to make it slightly less comfortable for about 20 minutes. On the app. Go.", "dating_tips"),
    ("Fact: People who log in daily find matches faster", "Not a fact we made up. Well, maybe it is. But logically it's true. Be one of those people.", "dating_tips"),
    ("Your potential partner is also procrastinating right now", "You're both doing the same thing. One of you has to stop. Today, let it be you. Log in.", "dating_tips"),

    ("Remember when dating felt exciting?", "It can again. The nervous butterflies, the 'oh they texted back' rush. It's all waiting in the app.", "dating_tips"),
    ("Dating is 90% showing up", "The other 10% is having a decent photo and not leading with 'hey'. You've got this. Log in.", "dating_tips"),
    ("Plot twist: What if the right person joined this week?", "New people join every day. Today might be their day one. Make sure you're there when they arrive.", "promotions"),
    ("Your 'type' might be wrong and that's fine", "Some of the best connections come from unexpected people. Stay open. Explore. Discover. Open the app.", "promotions"),
    ("Consider this your daily nudge", "Not nagging. Gentle encouraging. The kind a good friend does. Open the app. Be active. Good things come.", "promotions"),
    ("Someone in the app is wondering why you haven't messaged", "Maybe. Probably. Statistically speaking. Go find out who it is.", "promotions"),
    ("You're one conversation away from something great", "Not a guarantee. But a real possibility. And it costs nothing. Open the app and try.", "promotions"),
    ("Your dating bio deserves better", "Three words and a sports team does not a bio make. Put effort in. Get effort back. Update it.", "promotions"),
    ("It's a good day to try something new", "Like messaging someone first. Or answering someone you've been ignoring. Small steps. Big results.", "promotions"),
    ("Your next chapter starts with a message", "Doesn't have to be perfect. Just real. Just you. Open the app and start writing it.", "promotions"),

    # --- NORMAL ---
    ("Real connections are built on real conversations", "Put down the Instagram, pick up the dating app. Talk about something that actually matters to you.", "promotions"),
    ("You deserve someone who shows up for you", "And they're out there. Looking for someone just like you. Give them a chance to find you.", "promotions"),
    ("Your person is waiting for you to make a move", "Not literally waiting. But figuratively, they're right there. Open the app and introduce yourself.", "promotions"),
    ("Great relationships start with honest profiles", "Be yourself from message one. The right person will love it. The wrong ones will filter out fast.", "promotions"),
    ("Every day you're active is a day closer", "Consistency builds momentum. Log in. Swipe thoughtfully. Chat genuinely. Progress happens.", "promotions"),
    ("Someone out there shares your exact weird hobby", "They're also wondering if they're too niche to date. You're not. Neither are they. Find each other.", "promotions"),
    ("You have more to offer than you think", "The right person will see exactly that. Your job is to show up so they get the chance.", "promotions"),
    ("Dating takes courage. You have it.", "Getting vulnerable is hard. Putting yourself out there is scary. Do it anyway. It's worth it.", "promotions"),
    ("The best conversations start with curiosity", "Ask real questions. Share real answers. Skip the script. Real connection is the reward.", "promotions"),
    ("Being specific in your bio attracts specific people", "Vague gets vague back. Specific gets specific. Tell people who you actually are. It works.", "promotions"),

    ("Your future partner needs to meet the real you", "Not the highlight reel. Not the performance. The actual you. That's who they're looking for.", "promotions"),
    ("Patience + action = results in dating", "Don't give up. But also don't stop trying. That balance is where the magic happens.", "promotions"),
    ("The right person will appreciate every part of you", "The weird parts. The serious parts. The parts you're still figuring out. Show up fully.", "promotions"),
    ("Chemistry requires two people to show up", "You can't feel a spark through photos and radio silence. Start conversations. See what happens.", "promotions"),
    ("Your values are your best dating asset", "Lead with what matters to you. It filters out the wrong ones and attracts the right ones faster.", "promotions"),
    ("Consistency is more attractive than perfection", "Showing up regularly, responding thoughtfully, being real. These matter more than a flawless profile.", "promotions"),
    ("The right relationship adds to your life, not takes from it", "Look for that. Someone who makes your life fuller, not more complicated.", "promotions"),
    ("Your story matters to the right person", "The chapters you're proud of. The ones you're still writing. Share them. The right one will lean in.", "promotions"),
    ("Small talk is just the gateway to real talk", "Start anywhere. Ask about anything. Good conversations evolve. Just start the one.", "promotions"),
    ("You're allowed to have standards AND be open", "Both can be true. High standards don't mean closed off. Hold them and stay curious.", "promotions"),

    ("The app works best when you work it", "Active users get more connections. Simple math. Log in daily. See the difference.", "promotions"),
    ("New people join every day. Be ready.", "Refresh your matches. Check for new faces. Someone great might have just appeared. Look.", "promotions"),
    ("Genuine interest is the best conversation starter", "Read their profile. Find something real to ask about. Thoughtful beats generic every time.", "promotions"),
    ("Your photos should look like you, not 5 years ago", "Update them. Recent, clear, genuine. That's all anyone needs to feel a connection.", "promotions"),
    ("Good timing is something you create", "The best time to message is when you're ready to have a real conversation. Are you ready?", "promotions"),
    ("Dating gets better when you communicate clearly", "What you want, who you are, what matters to you. Clarity early saves everyone time.", "promotions"),
    ("Your sense of humor belongs in your bio", "It's one of the most attractive things about a person. Let it show early.", "promotions"),
    ("Swipe with purpose, not just habit", "Scroll slowly. Read profiles. Ask yourself if this person actually interests you. Quality over volume.", "promotions"),
    ("The dating pool is bigger than your last streak of bad luck", "One rough patch doesn't define your dating future. Keep going. Better connections are ahead.", "promotions"),
    ("Real compatibility takes time to discover", "Don't judge by the first message. Give conversations room to breathe and develop.", "promotions"),

    ("Being yourself is not just advice, it's strategy", "The people who like the real you are the people worth keeping. Filter by being authentic.", "promotions"),
    ("One great connection beats ten surface ones", "Quality over quantity isn't just a saying. It's the actual secret to dating success.", "promotions"),
    ("You don't need to have everything figured out to start dating", "Bring your honest self, your genuine questions, and your openness. That's enough.", "promotions"),
    ("Your dating life rewards attention", "The more time and energy you invest in real conversations, the better your outcomes.", "promotions"),
    ("First messages set the tone", "Take 10 seconds to write something specific to their profile. The difference is massive.", "promotions"),
    ("Give yourself credit for showing up", "Dating is vulnerable. Every time you log in and try, that's courageous. Keep going.", "promotions"),
    ("The right match will think your quirks are highlights", "Don't hide them. Feature them. Let your weird flag fly. The right person will salute it.", "promotions"),
    ("Ask better questions, get better answers", "Skip 'so what do you do?' and go straight for what actually matters to you. See what opens up.", "promotions"),
    ("People surprise you when you give them a chance", "The person you almost skipped might be the most interesting one. Stay open.", "promotions"),
    ("Love the process, not just the outcome", "Meeting new people, having interesting conversations, learning what you want. It's all valuable.", "promotions"),

    ("Your presence in the app matters", "People see active users. They respond to active users. Be present. Be real. Be available.", "promotions"),
    ("Rejection is just redirection toward the right person", "Every no moves you one step closer to the right yes. Don't take it personally. Keep going.", "promotions"),
    ("Invest in conversations like you invest in yourself", "Put real time and thought into messages. You'd be surprised what grows from genuine effort.", "promotions"),
    ("Dating confidence is built through practice", "Every conversation teaches you something. Every rejection makes you more resilient. Practice.", "promotions"),
    ("Your next first date starts in the app", "From a first message to a great evening. That pipeline runs right through here. Start it.", "promotions"),
    ("People are attracted to positive energy", "Lead with enthusiasm, curiosity, and warmth. It shows even through text. Bring it.", "promotions"),
    ("Your ideal match is someone, not someone perfect", "Let go of the checklist. Look for the feeling. That's usually a better guide.", "promotions"),
    ("Consistent effort compounds in dating", "Five minutes a day, every day, builds more momentum than an hour once a week. Stay consistent.", "promotions"),
    ("You attract what you put out", "Genuine, curious, open. If that's you, lead with it. Watch what it brings back.", "promotions"),
    ("The app is just the beginning of the story", "What you build from here is yours. But it starts here, with a profile and a message.", "promotions"),

    ("Sometimes the best match surprises you", "You'll scroll past someone thinking 'maybe not' and their bio will change your mind. Stay curious.", "promotions"),
    ("Every conversation is a chance to learn something", "About them, about yourself, about what you actually want. Each one has value.", "promotions"),
    ("Your dating goals deserve the same commitment as your other goals", "You'd put effort into a career or fitness goal. Love is worth the same energy.", "promotions"),
    ("The people worth finding are also doing the finding", "They're active, curious, and looking for someone real. Be visible so they can find you.", "promotions"),
    ("Real connection happens when both people show up", "Be the person who shows up. Consistently. It matters more than you know.", "promotions"),
    ("Today is a good day to send that first message", "The one you've been drafting mentally. It's ready. You're ready. Send it.", "promotions"),
    ("You don't have to be perfect. You have to be you.", "That's more than enough for the right person. Show up authentically and trust the process.", "promotions"),
    ("Dating is a conversation, not a competition", "You're not trying to impress everyone. You're looking for your person. Very different mindset.", "promotions"),
    ("The right person is looking for exactly who you are", "Not a polished version. Not a filtered version. Just you. Make sure you're findable.", "promotions"),
    ("Good things come to those who show up", "Show up in the app. Show up in conversations. Show up as yourself. Good things follow.", "promotions"),

    ("Your love life is a project worth investing in", "Give it time, attention, and genuine effort. The ROI is someone amazing sharing your life.", "promotions"),
    ("Every profile you read is a real person's story", "Approach with curiosity and respect. Engage with their interests. Human connection is the goal.", "promotions"),
    ("The best time to be active is now", "Right now. This moment. Matches don't wait. People don't wait. Opportunity has a shelf life.", "promotions"),
    ("You deserve someone who texts back", "And they exist. They're in the app. Not everyone ghosts. Find the ones who don't.", "promotions"),
    ("Your story is still being written", "New chapters need new people. New people are in the app. Open it. Add to your story.", "promotions"),
    ("The app is a tool. You're the craftsman.", "Give it your best effort and it gives you its best results. Bad input, bad output. You decide.", "promotions"),
    ("Dating teaches you what you want", "Even the awkward conversations have value. They clarify. They redirect. They help you grow.", "promotions"),
    ("You've got something worth sharing with someone", "Your humor, your kindness, your weirdness. Find someone who appreciates all of it. They're here.", "promotions"),
    ("Showing interest is not desperate, it's attractive", "Responding quickly, asking follow-up questions, being engaged. These are green flags, not red ones.", "promotions"),
    ("Your person is somewhere between first message and first date", "They're not going to appear out of nowhere. The path goes through a conversation. Start one.", "promotions"),

    ("People remember how you made them feel", "A genuinely thoughtful message stands out for days. Put the effort in. Be memorable.", "promotions"),
    ("Don't let fear write your dating story", "Fear says stay hidden. Love says be found. Listen to the second voice. Open the app.", "promotions"),
    ("The right match won't need convincing", "When it's right, it's easy. You won't have to force it. But you do have to start it.", "promotions"),
    ("Your dating confidence grows every time you try", "Even bad conversations build resilience. Every interaction is training. Log in and train.", "promotions"),
    ("There's someone in the app right now who'd love to meet you", "Not a maybe. A statistical certainty. Someone compatible is here. Are you?", "promotions"),
    ("The goal isn't to impress everyone, it's to connect with one", "Stop performing. Start connecting. The right person wants the real you, not the polished act.", "promotions"),
    ("Your next great memory involves someone you haven't met yet", "They're in the app. You're reading a notification. One of you needs to make a move. Go.", "promotions"),
    ("Dating is brave. You're brave.", "Every time you put yourself out there, you're doing something genuinely hard. Keep doing it.", "promotions"),
    ("Great matches come from great effort", "You get what you put in. Thoughtful profiles, real conversations, genuine curiosity. That's the formula.", "promotions"),
    ("Your love life is worth your best energy", "Not the leftover energy after everything else. Real effort, real time, real attention. Worth it.", "promotions"),

    # Extra Funny & Normal to hit 200
    ("You've been 'almost ready' to date for three months", "Almost only counts in horseshoes. Get fully ready. Open the app. That's the whole step.", "promotions"),
    ("Your person isn't going to wait forever, and neither should you", "At some point action becomes mandatory. That point is now. Open the app. Start.", "promotions"),
    ("Good morning, your future partner is already scrolling", "They're up before you, active, looking. Don't let them find someone else first. Log in.", "promotions"),
    ("Your most recent photo is from a different hairstyle era", "Update it. People want to know what you look like today. Not three haircuts ago.", "promotions"),
    ("One good conversation can shift everything", "Not an exaggeration. Real talk: a single genuine exchange can change your entire trajectory. Start one.", "promotions"),
    ("The people you haven't met yet are the most interesting", "Everyone you know, you know. The unknown ones are where the magic is. The app is full of unknown.", "promotions"),
    ("Your ideal relationship already looks a certain way in your head", "Now do the work to make it real. The app is step one. Describe what you want. Find it.", "promotions"),
    ("You don't need to have the perfect opener", "You need to have an opener. That's it. Anything genuine works. Stop overthinking. Start.", "promotions"),
    ("Dating in real life means showing up digitally first", "That's just how it works now. The app is the handshake. The date is what follows. Shake hands.", "promotions"),
    ("Your next great memory hasn't happened yet", "It's out there waiting for you to create it. With someone. Who you haven't met. In the app. Go.", "promotions"),
    ("You've been patient. Now add action to the patience.", "Patience alone is just waiting. Patience plus action is strategy. Apply both. Open the app.", "promotions"),
    ("Today someone will join the app who matches you perfectly", "Or they did yesterday and you missed it. Log in. Don't miss tomorrow's.", "promotions"),
    ("Your dream of a great relationship starts with a small step", "Smallest possible step: open the app. You've done harder things today already. Do this too.", "promotions"),
    ("Connection is built in small moments, not grand gestures", "A thoughtful reply. A genuine question. A real answer. All in the app. All building something.", "promotions"),
    ("Your profile tells a story. Make sure it's a good one.", "Update it. Make it honest, warm, and specific. It's your first impression for everyone you'll match with.", "promotions"),
    ("The best relationships start before you know they will", "You don't know which conversation will matter. So start many. Find out. Log in.", "promotions"),
    ("Dating is a skill and skills improve with practice", "Every conversation. Every match. Every awkward exchange. All practice. All valuable. Log in to practice.", "promotions"),
    ("Your potential partners deserve to see the real you", "The one with opinions, passions, weird interests, and actual depth. That's who they're looking for.", "promotions"),
    ("Some of the best nights start with a first message", "Coffee that turns into dinner. Dinner that turns into a walk. A walk that turns into everything. Start.", "promotions"),
    ("You've done hard things. Opening an app is not one of them.", "Perspective check. Log in. Easy thing. Big potential. Obvious move.", "promotions"),
    ("Someone out there laughs at the same obscure things you do", "Your specific references. Your niche humor. They get it. They're in the app. Find them.", "promotions"),
    ("Being selective is smart. Being invisible is not.", "You can have both: high standards and active presence. The app supports both at once.", "promotions"),
    ("Your conversation style is someone's favorite kind", "Direct, funny, thoughtful, weird — whoever you are, someone is specifically looking for that. Show up.", "promotions"),
    ("The dating game has one rule: participate", "That's literally it. Everything else is optional. Participation is mandatory. Log in. Participate.", "promotions"),
    ("Every version of you is worth meeting", "The you today, with everything you're carrying and everything you're figuring out. Show up as-is.", "promotions"),
    ("Good love finds people who are findable", "Be findable. Active profile. Recent photos. Genuine bio. Regular logins. That's findable.", "promotions"),
    ("Your sense of humor is someone's green flag", "The thing you think is too much? That's someone's favorite thing. Let it show. Log in.", "promotions"),
    ("There's no perfect moment to start. This moment will do.", "Waiting for stars to align is overrated. The stars are fine right now. Open the app.", "promotions"),
    ("Your dating energy is contagious when you actually bring it", "Show up with enthusiasm. People feel it through the screen. It changes every interaction. Bring it.", "promotions"),
    ("The 'right time' is a myth. There's only now.", "You're either in the app now or you're not. Now is better. Log in. Right now is exactly right.", "promotions"),
    ("Someone is hoping you'll message first", "They're not going to say it. But it's true. They want to hear from you. Open the app. Say hi.", "promotions"),
    ("Your presence in the app is its own kind of confidence", "Being there, being active, being real. That's quietly attractive. Show up. Let people see you.", "promotions"),
    ("Dating success is just connection compounded over time", "One good match leads to more conversations leads to one great one leads to something real. Start compounding.", "promotions"),
    ("Be the energy you want to receive in the app", "Curious, genuine, warm, interested. Lead with it. Attract it back. Open the app as the version you want.", "promotions"),
    ("Showing up consistently is more romantic than grand gestures", "Daily logins, thoughtful messages, genuine interest. That's what actually builds connection. Be consistent.", "promotions"),
    ("Your future partner likes people who try", "Who show up. Who reach out. Who lean in. Be that person. Open the app and lean in.", "promotions"),
    ("The window for a great match is open right now", "Not forever. But right now it's wide open. The app has fresh faces and real people. Look.", "promotions"),
    ("You already have everything you need to start", "A phone, the app, and yourself. That's the full kit. Everything else comes after you begin.", "promotions"),
    ("Real talk: The app doesn't work unless you do", "You knew this. You've always known this. Today is the day you act on what you know.", "promotions"),
    ("Love is brave and you're braver than you think", "Showing up with an open heart takes courage. You have more of it than you give yourself credit for.", "promotions"),
    ("Every day is a chance to start over in the app", "Bad conversations yesterday? Doesn't matter. Fresh day, fresh matches, fresh energy. Log in.", "promotions"),
    ("The version of you that goes on dates is better than the one that doesn't", "More interesting stories. More human connection. More joy. Be that version. Open the app.", "promotions"),
    ("Dating well is an act of self-respect", "You deserve good company. You deserve real connection. Pursuing those things honors that truth.", "promotions"),
    ("Stop saving your best self for imaginary scenarios", "Use your actual best self in actual conversations with actual people. The app is real. Show up.", "promotions"),
    ("Something good is waiting for you in there", "Not a guarantee. A very strong statistical probability. Open the app. Find out.", "promotions"),
    ("Your story is worth telling to the right audience", "That audience exists. They're in the app. They want to hear it. Tell it.", "promotions"),
    ("The only way past the fear is through the app", "Okay not literally. But metaphorically, yes. Action defeats fear. Open the app. Defeat the fear.", "promotions"),
    ("You deserve someone who makes you glad you tried", "And they exist. And they're findable. And the path goes through the app. Try. Find them.", "promotions"),
    ("The work you put into dating today is the joy you feel tomorrow", "Effort today, results eventually. That's the whole formula. Open the app. Do the work.", "promotions"),
    ("Your dating story isn't written yet. Pick up the pen.", "First line: open the app. Second line: send a message. Go from there. Write something worth reading.", "promotions"),
    ("Today's action is what separates wishes from reality", "Wishes: passive. Reality: active. The app is where wishes become actions. Log in.", "promotions"),
    ("Your best conversation is one open app away", "Not an exaggeration. The best exchange you've ever had might be queued up right now. Log in.", "promotions"),
    ("People in the app are actively choosing to find someone", "They made the choice. They're there. You're reading a notification. Join the choosers.", "promotions"),
    ("You've already done the hard part by downloading this app", "The rest is just showing up. Which is much easier. You've done harder things before breakfast.", "promotions"),
    ("Every match is a door. You won't know what's behind them unless you knock.", "Knock. Message first. Be curious. See what opens. The app has a lot of interesting doors.", "promotions"),
    ("Dating well means showing all the way up", "Not half-heartedly. Not half-profile. Not half-present. Fully. The full version of you. Log in as that.", "promotions"),
    ("The app is your bridge to people you'd never otherwise meet", "Entire worlds of compatibility you haven't discovered yet. The bridge is right here. Cross it.", "promotions"),
    ("Your instinct to try is right. Your instinct to hesitate is just fear.", "Back the right instinct. Open the app. Let the good instinct win today.", "promotions"),
    ("There are people in the app who'd be glad to hear from you", "Real people, genuinely hoping someone interesting reaches out. Be that someone. Log in.", "promotions"),
    ("Love isn't waiting for you to be ready. It's waiting for you to show up.", "Ready is a myth. Present is a choice. Choose present. Open the app.", "promotions"),
    ("Something shifts when you decide to actually try", "Mindset, behavior, outcomes — all of it. The decision to try changes the whole equation. Decide.", "promotions"),
    ("Your person has interests, passions, and a sense of humor you'd love", "All described in their profile. Waiting for you to read it. The app is where that lives.", "promotions"),
    ("The best thing about new connections is they're new", "Clean slate. No baggage. Fresh energy. New person. The app is full of them. Go meet one.", "promotions"),
    ("Dating takes courage and courage gets better with use", "The more you use it, the stronger it gets. Open the app. Use the courage. Watch it grow.", "promotions"),
    ("Right now is the exact right time to open the app", "No stars required. No planetary alignment needed. Just now. This moment. Log in.", "promotions"),
    ("Your love life deserves the same care you give everything else", "Your health. Your work. Your friendships. Add love life to the list. Give it care. Log in.", "promotions"),
    ("Every conversation you have makes the next one easier", "Momentum is real. Start one. The second comes easier. The third easier still. Open the app.", "promotions"),
    ("You're closer to a great relationship than you think", "By which we mean: one good conversation away. Which starts with opening the app. Which starts now.", "promotions"),
    ("The app doesn't judge your pajamas or your timing", "3pm or midnight. Sweatpants or suit. Any moment is a valid moment to find someone great. Log in.", "promotions"),
    ("Every person you match with chose to be here too", "They want connection just like you. That shared intention is a great starting point. Meet them there.", "promotions"),

    ("Someone out there thinks your exact humor is everything", "They haven't met you yet. The app is where that changes. Go introduce yourself.", "promotions"),
    ("Your relationship goals are valid and achievable", "Not naive, not too much. The right person exists and they share your vision. Go find them.", "promotions"),
    ("Today's action is tomorrow's relationship story", "The message you send today might be the one you're laughing about in five years. Send it.", "promotions"),
    ("Real talk: The app works if you do", "All the potential in the world sits unused without effort. Do the work. See the results.", "promotions"),
    ("You're allowed to want a great relationship", "Not just 'something'. Not just 'fine'. An actually great, wonderful, real relationship. Go get it.", "promotions"),
    ("Every week you're active, you learn something new about yourself", "What you want, what you don't, what matters. The app is self-discovery as much as partner discovery.", "promotions"),
    ("Start the week right with a new conversation", "Fresh week, fresh start. New matches, new possibilities. Open the app and kick off something good.", "promotions"),
    ("Your person will feel like relief", "Not stress. Not work. Relief. That's what the right one feels like. Go find that feeling.", "promotions"),
    ("The app gives you options. You make the choices.", "No algorithm decides your love life. You do. Log in. Explore. Decide. Take back control.", "promotions"),
    ("Love takes effort, and you've got plenty", "You work hard at everything else in your life. Apply that same energy here. You already know how.", "promotions"),
]


# 2. 100 PICKUP LINES - Bold, Charming & Fun Notifications
PICKUP_LINE_TEMPLATES = [
    ("Are you a magnet? Because I can't stop being attracted to you", "That's what someone in the app is basically thinking about you right now. Go find out who.", "promotions"),
    ("Do you have a map? I keep getting lost in the thought of meeting you", "GPS for feelings. It points directly to the app. Follow the arrow.", "promotions"),
    ("Are you Wi-Fi? Because I'm feeling a connection", "Strong signal detected. Full bars. No dead zones. Log in and lock it in.", "promotions"),
    ("Is your name Google? Because you have everything I've been searching for", "Searching: someone great. Results: the app. Click open. Start exploring.", "promotions"),
    ("Are you a library book? Because I can't stop checking you out", "Also, late returns will be judged. Open the app before this connection gets overdue.", "promotions"),
    ("Do you believe in love at first swipe?", "Because someone in the app is about to make you a believer. Go test the theory.", "promotions"),
    ("Are you a camera? Every time I think about the app, I smile", "True story. Open it and recreate that smile with someone real on the other end.", "promotions"),
    ("Is your name Chance? Because I don't want to miss you", "Opportunity is knocking via push notification. Please answer the door. Open the app.", "promotions"),
    ("You must be tired because you've been swiping through my mind all day", "And you haven't even opened the app yet. Imagine once you do. Log in.", "promotions"),
    ("Are you a parking ticket? Because you've got 'fine' written all over you", "Someone in the app thinks so too. They just need you to show up. Swipe and be seen.", "promotions"),

    ("Is your name Netflix? Because I could see us spending a lot of time together", "Real talk: two people, one good show, infinite comfort. That starts in the app.", "promotions"),
    ("You must be a star because you brighten everything around you", "Including someone's notifications when you're active. Log in. Light it up.", "promotions"),
    ("Are you an alarm clock? Because you wake something up in me", "The something being hope, excitement, and the will to open a dating app. Here we are.", "promotions"),
    ("Is your name Sunday? Because you feel like the best day of the week", "And the best conversations start when you log in. Make today feel like Sunday.", "promotions"),
    ("You must be a dictionary because you add meaning to everything", "And also because someone in the app wants to find the words to describe how great you are.", "promotions"),
    ("Are you a mirror? Because I see my future when I think about meeting you", "Reflections aside, the future starts with action. Open the app. Start the future.", "promotions"),
    ("Is your name Destiny? Because I feel like we were meant to connect", "Destiny uses the app now. More reliable than fate. Open it and meet yours.", "promotions"),
    ("You must be a sunset because every time I think about you, everything gets better", "Someone in the app has that effect. You haven't met them yet. Go look.", "promotions"),
    ("Are you a coffee? Because I think about you first thing in the morning", "And second thing, and third thing. The point is: open the app. They're waiting.", "promotions"),
    ("Is your name Autumn? Because you make everything more beautiful", "Someone in the app would say that about you. They're just waiting for a conversation to start.", "promotions"),

    ("You must be a song because you keep playing in my head", "On repeat. Endlessly. Open the app and find out who's the DJ of your heart.", "promotions"),
    ("Are you a book? Because I can't put down the thought of getting to know you", "Plot twist: you have to open the app to read the good parts. Do it.", "promotions"),
    ("Is your name Adventure? Because you're exactly what I've been looking for", "Adventure is two people doing something new together. The app is step one.", "promotions"),
    ("You must be gravity because I keep falling for the idea of meeting you", "Physics don't lie. Neither does chemistry. Open the app and test both.", "promotions"),
    ("Are you a sunrise? Because you make me want to start every day better", "Someone in the app has that energy. They're awake and active. Meet them there.", "promotions"),
    ("Is your name Lucky? Because I feel like finding you would make my year", "Luck is real. So is effort. Combine them. Open the app. Find Lucky.", "promotions"),
    ("You must be a playlist because I could listen to you forever", "And ask real questions. And laugh. And maybe plan a dinner. Start with the app.", "promotions"),
    ("Are you a compass? Because I feel lost without direction toward you", "True north is north of your current screen. It's in the app. Go there.", "promotions"),
    ("Is your name Magic? Because everything feels better when I think about meeting you", "Magic requires the spell. The spell is opening the app. Cast it.", "promotions"),
    ("You must be a chef because the thought of you makes everything taste better", "Someone in the app is your secret ingredient. Find them. Add them to your life recipe.", "promotions"),

    ("Are you a season change? Because you make everything feel fresh and new", "New profile, new matches, new conversations. The app is a season change for your love life.", "promotions"),
    ("Is your name Clarity? Because everything makes more sense when I think about you", "Clarity comes from connection. Connection comes from the app. Open it.", "promotions"),
    ("You must be a garden because something beautiful would grow with you", "And gardens need tending. Log in regularly. Water the conversations. Watch what blooms.", "promotions"),
    ("Are you a spark? Because the thought of meeting you lights everything up", "Sparks need kindling. Kindling is a good first message. Open the app. Strike the match.", "promotions"),
    ("Is your name Serendipity? Because bumping into you feels like it was meant to be", "Serendipity lives in the app now. More reliable address than street corners.", "promotions"),
    ("You must be electricity because you energize every thought of mine", "Channel that energy into action. Open the app. Send a message. Power up your love life.", "promotions"),
    ("Are you a lighthouse? Because you guide me right where I need to be", "The lighthouse is on. It's pointing at the app. Follow the light.", "promotions"),
    ("Is your name Echo? Because every good thing you say comes back multiplied", "Good conversations echo. Good matches resonate. Open the app and start the echo.", "promotions"),
    ("You must be a telescope because you make the future look so much clearer", "And closer. And reachable. Log in. Adjust the lens. See someone worth seeing.", "promotions"),
    ("Are you a poem? Because spending time with you would be beautiful", "Someone in the app writes their own poetry of a life. Go read the first stanza.", "promotions"),

    ("Is your name Warmth? Because you make every cold day better just by existing", "Someone in the app radiates exactly that. And they're active. And available. Log in.", "promotions"),
    ("You must be a good book because I never want the conversation with you to end", "First you need to start the conversation. The app is where that happens. Turn to page one.", "promotions"),
    ("Are you a window? Because looking at your profile clears everything up", "Crystal clear: someone here is worth knowing. Open the app. Look through the window.", "promotions"),
    ("Is your name Wonder? Because meeting you would be worth wondering about forever", "Stop wondering. Start finding out. Open the app. Replace the question mark with them.", "promotions"),
    ("You must be a doorway because stepping toward you feels like entering somewhere amazing", "The doorway is the app. The amazing place is the connection waiting on the other side.", "promotions"),
    ("Are you a melody? Because the thought of meeting you stays with me all day", "All day you've been thinking. Tonight, do something about it. Open the app.", "promotions"),
    ("Is your name Depth? Because I want to know everything about you", "Depth requires exploration. Exploration requires a first message. The app is where you dive in.", "promotions"),
    ("You must be a horizon because you always give me something worth moving toward", "Move toward it. Toward the app. Toward the person waiting there. Move.", "promotions"),
    ("Are you a favorite place? Because the thought of meeting you feels like coming home", "Home is a feeling, not an address. It might start with a conversation in the app. Go find it.", "promotions"),
    ("Is your name Meaning? Because since thinking about meeting you, everything feels significant", "Significance starts with a swipe. Deep? Yes. Also true. Open the app and find meaning.", "promotions"),

    ("You must be a perfect match because everything about you makes sense", "Matches don't just happen. They're found. The app is where the finding happens. Go look.", "promotions"),
    ("Are you a remedy? Because just knowing you exist makes things better", "Someone in the app is exactly that for someone else. You could be that. Log in.", "promotions"),
    ("Is your name Possibility? Because you make everything feel like it could happen", "Possibility loves people who take action. Open the app. Take action. Make things possible.", "promotions"),
    ("You must be a great conversation because I can't stop thinking about starting one with you", "Start it. The app is open. The people are real. The conversation is one tap away.", "promotions"),
    ("Are you a reason? Because meeting you would give me every reason to smile more", "Reasons are earned through effort. Put in the effort. Open the app. Find a reason.", "promotions"),
    ("Is your name Momentum? Because thinking about you makes me want to keep going", "Keep going. Into the app. Into conversations. Into the possibility of something real.", "promotions"),
    ("You must be a highlight because you make everything else pale in comparison", "And you haven't even met them yet. Imagine when you do. Open the app. Find your highlight.", "promotions"),
    ("Are you a reset? Because meeting you would feel like starting everything fresh", "Fresh starts are available. The app is the reset button. Press it. Begin again, better.", "promotions"),
    ("Is your name Joy? Because imagining meeting you makes everything lighter", "Joy is real. It exists in people. Some of them are in the app. Go find yours.", "promotions"),
    ("You must be a north star because you always give me direction", "Follow the direction. It leads to the app. It leads to someone. It leads somewhere worth going.", "promotions"),

    ("Are you a good habit? Because I want to start every day with the thought of you", "Good habits require starting. Start by opening the app. Daily. Like brushing your teeth but better.", "promotions"),
    ("Is your name Discovery? Because meeting you would feel like finding something incredible", "The incredible is already in the app. It just hasn't been discovered by you yet. Go explore.", "promotions"),
    ("You must be a favorite memory because thinking of meeting you already feels nostalgic", "Make the memory. Open the app. Have the conversation. Feel the feeling. Remember it forever.", "promotions"),
    ("Are you a language? Because I want to spend forever learning you", "Languages take time. So do relationships. Both start somewhere simple. The app is that somewhere.", "promotions"),
    ("Is your name Grace? Because meeting you would feel like receiving a gift", "Gifts exist in unexpected places. Sometimes in a dating app. Open it. Receive.", "promotions"),
    ("You must be a constellation because you always help me find my way", "The stars are aligned. The app is open. The matches are waiting. Navigate toward them.", "promotions"),
    ("Are you a love story? Because I want to read every chapter of yours", "Love stories are co-authored. You write half. They write half. The app is where you start writing.", "promotions"),
    ("Is your name Peace? Because thinking about meeting you calms everything down", "Peace of mind comes from knowing you tried. Open the app. Try. Feel the peace.", "promotions"),
    ("You must be a sunrise because you turn everything bright", "Sunrises happen every day. So does the chance to meet someone great. Open the app.", "promotions"),
    ("Are you a great idea? Because I can't stop thinking about how good this could be", "Great ideas need execution. The execution is opening the app and starting a real conversation.", "promotions"),

    ("Is your name Timing? Because now feels exactly right to meet you", "Now is the best time. Not later. Not tomorrow. Right now. Open the app. This is the moment.", "promotions"),
    ("You must be a good night's sleep because thinking about you is restful", "Well-rested and ready to meet someone? Perfect condition. Open the app. Match that energy.", "promotions"),
    ("Are you a favorite season? Because thinking about being with you feels like being exactly where I should be", "Location: the app. Feeling: right. Action required: logging in. Do it.", "promotions"),
    ("Is your name Loyalty? Because the idea of having you in my corner changes everything", "Loyal people exist. They're in the app. They're looking for the same thing you are. Find them.", "promotions"),
    ("You must be a deep conversation because I feel like you'd change how I see everything", "Perspective-changing conversations exist. They start in the app. Log in. Change your perspective.", "promotions"),
    ("Are you a full moon? Because everything feels more magical when I think of you", "Magic doesn't wait for full moons. It waits for effort. Open the app. Make magic happen.", "promotions"),
    ("Is your name Glow? Because the thought of meeting you lights me up from the inside", "Someone in the app has that exact energy. They're active right now. Go find your glow.", "promotions"),
    ("You must be a perfect evening because everything about you sounds incredible", "Perfect evenings need perfect company. Perfect company is one conversation away. Open the app.", "promotions"),
    ("Are you a dream worth chasing? Because meeting you feels worth every effort", "Worthwhile things require effort. Open the app. Make the effort. Chase something worth chasing.", "promotions"),
    ("Is your name Everything? Because meeting you sounds like it would be exactly that", "That's a big word. But for the right person? Accurate. Open the app. Find your everything.", "promotions"),

    ("You must be a reason to celebrate because meeting you would feel like winning", "Winners show up. Winners make moves. Winners open the app. Be a winner.", "promotions"),
    ("Are you a once-in-a-lifetime thing? Because I don't want to miss out on you", "Once-in-a-lifetime things don't wait. Open the app before 'once' becomes 'once upon a time I had a chance'.", "promotions"),
    ("Is your name Wow? Because thinking about meeting you is exactly that", "Wow is the reaction. The app is the cause. Log in. Create some wow in your life.", "promotions"),
    ("You must be a great match because nothing feels more right than the idea of us connecting", "Great matches feel inevitable in hindsight. Set up the hindsight. Open the app.", "promotions"),
    ("Are you a beginning? Because meeting you feels like the start of something big", "Beginnings require showing up. The app is where your beginning lives. Walk through the door.", "promotions"),
    ("Is your name Ready? Because I feel completely prepared to meet someone like you", "Ready is a decision, not a feeling. Decide. Open the app. Meet someone. Go.", "promotions"),
    ("You must be a perfect moment because thinking about meeting you feels exactly like that", "Perfect moments are made, not found. Make yours. In the app. With someone real. Now.", "promotions"),
    ("Are you a full heart? Because the thought of knowing you fills mine right up", "Full hearts require people to fill them. People who fill hearts are in the app. Go find one.", "promotions"),
    ("Is your name Best Case Scenario? Because meeting you sounds like exactly that", "Best case scenario: you open the app and start a conversation that changes your life. Do that.", "promotions"),
    ("You must be a great story because I want to be part of yours", "Stories need characters. You're a great character. Get into the story. Open the app.", "promotions"),
    ("Are you a leap of faith? Because you're worth jumping for", "The leap is opening the app and saying hello to someone real. Not as scary as it sounds. Jump.", "promotions"),
    ("Is your name Spark? Because you set the whole conversation on fire", "Conversations that spark start with someone deciding to start them. Be that person. Log in.", "promotions"),
    ("You must be a great playlist because I'd listen to you on repeat", "Conversation on repeat means genuine connection. Find someone worth repeating. The app has them.", "promotions"),
    ("Are you a cold drink on a hot day? Because you'd be exactly what I need", "Exactly what you need is findable. The app holds it. Open it. Find your cold drink.", "promotions"),
    ("Is your name Balance? Because meeting you would feel like everything clicking into place", "Balance is built between two people. Find the other half of your balance. They're in the app.", "promotions"),
    ("You must be a perfect view because I'd never get tired of talking to you", "Views require showing up to the right spot. The spot is the app. Show up. See the view.", "promotions"),
    ("Are you a revelation? Because thinking about meeting you changes everything", "Revelations come to people who are open to them. Open the app. Stay open. Get revealed.", "promotions"),
    ("Is your name Present? Because meeting you would feel like the best gift", "Gifts require someone to give them. Someone in the app is ready to give you exactly this. Go find them.", "promotions"),
    ("Are you a turning point? Because meeting you would change the whole story", "Turning points don't announce themselves. They start with a swipe and a message. Open the app.", "promotions"),
    ("Is your name Exactly Right? Because everything about you sounds like the answer", "Answers live in the app. Questions are yours to ask. Open it. Ask. Find Exactly Right.", "promotions"),
]


# 3. 100 WITTY - Sharp, Clever & Sarcastically Wise
WITTY_TEMPLATES = [
    ("Your soulmate won't fall through the ceiling", "Unless you have a very unusual living situation. Log in. Swipe. Let the magic be less dramatic.", "promotions"),
    ("Fate is busy. You'll need to do some of the work.", "The universe has a lot going on. Pick up the slack. Open the app. Help fate out.", "promotions"),
    ("The algorithm found you. The irony is you won't open the app.", "We've done our job. Now do yours. It takes less effort than you think.", "promotions"),
    ("Your couch is not a matchmaker", "Despite its comfort and loyalty, the couch lacks the ability to introduce you to compatible humans. The app does not.", "promotions"),
    ("Procrastination: the leading cause of staying single", "Scientific study. Sample size: everyone who's ever thought 'I'll try the app tomorrow'. Tomorrow is today.", "promotions"),
    ("You: 'I don't need an app.' Also you: *still single*", "Correlation? Causation? Either way, maybe try the app and see what happens. For science.", "promotions"),
    ("Netflix asked if you're still watching your single life", "Harsh but fair. The streaming service has a point. Switch tabs. Open the app.", "promotions"),
    ("Dating apps work. The plot twist is you have to use them.", "Shocking conclusion reached after extensive research. The app is ready. Are you?", "promotions"),
    ("Manifesting works better with a Wi-Fi connection", "The universe is very responsive to active dating app profiles. Weird coincidence. Try it.", "promotions"),
    ("Your future self called. They said thanks for trying.", "Time-traveling gratitude requires present-day action. Open the app. Future you is counting on you.", "promotions"),

    ("Even Sherlock Holmes had to look for clues", "Your person won't be found through passive observation. Active investigation required. The app is your crime scene.", "promotions"),
    ("The definition of insanity: doing nothing and expecting dates", "Einstein probably said something like this. Probably. Either way, change the variable. Open the app.", "promotions"),
    ("Your romantic luck is directly proportional to your activity", "Law of dating physics. The more you interact, the luckier you get. Start interacting.", "promotions"),
    ("Being mysterious works in movies. Not apps.", "In apps, invisible profiles get invisible results. Be present. Be visible. Be findable.", "promotions"),
    ("Strong independent person who also wants companionship: valid", "Both things are true simultaneously. Independence and connection are not enemies. Log in, strong independent person.", "promotions"),
    ("Your standards are high. Your activity level should match.", "You know what you want. Now put in the work to find it. Standards without effort is just a wish list.", "promotions"),
    ("Not to alarm you but your dating profile has cobwebs", "Little digital spiders have moved in. They're fine but they're not helping you find a partner. Clean house.", "promotions"),
    ("Mercury isn't in retrograde, you just haven't opened the app", "Blaming planets is so last season. This season we take accountability. Open the app.", "promotions"),
    ("Your relationship status is waiting for your input", "System update required. Action needed. The form is half filled out. Complete the input. Log in.", "promotions"),
    ("Plot armor only works in fiction. In real life, try the app.", "You are not the protagonist of a rom-com. You're a real person who needs to make real moves.", "promotions"),

    ("Your comfort zone sent a postcard: 'Wish you were here. Just kidding, please leave.'", "The postcard means get out of it. Temporarily. For swiping purposes. The app awaits.", "promotions"),
    ("Technically speaking, waiting for fate is just waiting", "Differentiate yourself from everyone else. Take action. Open app. Be extraordinary in your effort.", "promotions"),
    ("Cupid is overworked. Help him out.", "He has millions of arrows and a bad shoulder. Reduce his workload. Do some of the matching yourself.", "promotions"),
    ("The universe is a fan of effort", "Big supporter of it, actually. Very into people who open dating apps and actually try. Be its favorite.", "promotions"),
    ("Your future partner isn't going to find themselves", "That would be very convenient. But also philosophically complicated. You find them instead. In the app.", "promotions"),
    ("You've put more effort into your coffee order than your love life", "Oat milk, extra shot, specific temperature. That precision deserves a dating strategy equally detailed.", "promotions"),
    ("Being picky is great. Being picky AND inactive is just lonely.", "You can have discerning taste AND open the app. Both. At the same time. Revolutionary concept.", "promotions"),
    ("Your love life is in economy class and you deserve first", "Upgrade available. Requirements: being active in the app. Cost: nothing extra. Upgrade now.", "promotions"),
    ("Your dating strategy: 'Someone will just appear.' Update required.", "Version 1.0 of this strategy has a 0% success rate. Download version 2.0: actually using the app.", "promotions"),
    ("The app is not going to use itself", "It has tried. It cannot. It requires you. Specifically your thumbs. And your presence. Log in.", "promotions"),

    ("Insight: You can't find someone you're not looking for", "Mind-blowing, we know. The looking happens in the app. Active looking, not passive wishing.", "promotions"),
    ("Your single status is renewable unless you take action", "It auto-renews every year if you do nothing. Cancel the subscription by opening the app.", "promotions"),
    ("The algorithm is not magic. It's math that requires your participation.", "Good news: no magic knowledge required. Just presence and effort. You can do math. Log in.", "promotions"),
    ("Irony: The person complaining about being single hasn't opened the app today", "Check your own activities before blaming fate. Quick audit. Open the app. Problem solved.", "promotions"),
    ("Your love life has been in draft mode since forever", "Great in concept. Never published. Hit the button. Post. Go live. The audience is waiting.", "promotions"),
    ("Dating is not a spectator sport", "You're in the stands watching everyone else play. It's your turn. Get on the field. Open the app.", "promotions"),
    ("Spoiler: The app works better when opened", "Revolutionary finding from our research department. Share widely. Also apply personally. Open it.", "promotions"),
    ("Every day you don't try is a day you guarantee the result", "Guaranteed result of no effort: nothing. Guaranteed result of some effort: possibility. Choose possibility.", "promotions"),
    ("Your person isn't hiding. They're waiting for you to show up.", "Not dramatically hiding. Just... in the app. Active. Wondering where you are. Show up.", "promotions"),
    ("Bold strategy: What if you just tried?", "Wild, untested approach. Only approximately a million people have tried it. All results better than nothing.", "promotions"),

    ("Dating in 2026 requires a smartphone and willingness. You have both.", "Congratulations, you meet the minimum requirements. Now apply them. Open the app.", "promotions"),
    ("Your gut says try. Your fear says don't. Your gut has better judgment.", "The gut is usually right about these things. It's been around longer than the fear. Listen to the gut.", "promotions"),
    ("You have the same 24 hours as people who went on dates today", "Allocation differs. Some people allocate 20 minutes to the app. Adjust your allocation.", "promotions"),
    ("The opportunity cost of not trying: literally a relationship", "Economics lesson. The thing you give up by not trying is the thing you want. Bad trade. Try instead.", "promotions"),
    ("Your love life is sponsored by inaction. Cut the sponsor.", "New sponsor: actually using the dating app. Better ROI. More interesting content. Log in.", "promotions"),
    ("Waiting to feel ready is waiting to feel something that only comes after you start", "Ready comes from doing, not from waiting to do. Start. Feel ready after. Open the app.", "promotions"),
    ("Here is a direct notification: Go open the app.", "We have spoken. The notification has been sent. The message is clear. Obey the notification.", "promotions"),
    ("Your profile is like a museum after hours: technically there, but inaccessible", "Regular operating hours should include daily logins. Open the museum. Let visitors in.", "promotions"),
    ("Counterintuitive fact: Being vulnerable gets you what you want", "Scary? Yes. Worth it? Always. The app is a low-stakes place to practice. Start there.", "promotions"),
    ("Your risk analysis: Downside = wasted 15 minutes. Upside = relationship.", "Risk/reward calculus strongly favors opening the app. The math is not subtle. Log in.", "promotions"),

    ("The best version of your love story starts with a mediocre first message", "It doesn't need to be perfect. It needs to be sent. Open the app. Send the mediocre first message.", "promotions"),
    ("Being average at trying beats being exceptional at waiting", "Effort with average results beats zero effort every time. Be average. Try. Win more.", "promotions"),
    ("You're not too busy. You're too comfortable.", "Comfortable and busy feel the same but they're not. One is fixable with 15 minutes and an app. Fix it.", "promotions"),
    ("Your future self would go back and tell you to open the app today", "Time travel isn't available but the advice is. Accept it. Act on it. Log in.", "promotions"),
    ("The universe is tired of dropping hints. This is a direct message.", "No more subtle signs. No more serendipity hints. Direct communication: Open. The. App.", "promotions"),
    ("Technically, every successful couple was once two strangers who tried", "Technical fact. Historical record. You can be the next data point. Open the app.", "promotions"),
    ("Your person exists. They are findable. You're just not looking.", "This is the whole equation. Person exists + they are findable + you need to look = log in.", "promotions"),
    ("Being real in the app gets real results. Performing gets nothing.", "Algorithms aside, humans respond to authenticity. Be real. Get real connections back.", "promotions"),
    ("Small irony: You check the app for 'no new notifications' notifications", "So you're in the app but not using it. One more tap. Swipe on something. Message someone.", "promotions"),
    ("Technically, opening the app is the easiest part of finding love", "And yet. Here we are. Sending you a notification about the easiest part. Do the easy part.", "promotions"),

    ("Dating advice that works: Show up.", "Not metaphorically. Literally. In the app. With your profile. In conversations. Show. Up.", "promotions"),
    ("Your love life is a group project and you're not contributing", "The class is very disappointed. The group has noted your absence. Contribute. Open the app.", "promotions"),
    ("Good news: The bar for a good first message is on the floor", "You just need to not say 'hey'. Anything more than that is impressive. You've got this. Log in.", "promotions"),
    ("Your relationship goals are not unusual. Many people have them.", "In fact, people with your exact goals are in the app. Waiting. You have never met them. Fix that.", "promotions"),
    ("You've thought about trying the app more than you've tried the app", "Thoughts don't count. Actions count. Time to make thought and action the same thing. Log in.", "promotions"),
    ("Here's a wild thought: What if it worked?", "What if you opened the app and found someone incredible? Only one way to know. Try.", "promotions"),
    ("Your love life is in beta testing and you're the only tester", "Not enough testers. Need external input. The app provides it. Log in. Expand the testing group.", "promotions"),
    ("The part where things get better starts when you decide they will", "Decision required. Action follows decision. App follows action. Love follows app. Decide.", "promotions"),
    ("You're one active day away from a completely different outcome", "Not guaranteed. But possible. And possible is infinitely better than the certainty of doing nothing.", "promotions"),
    ("Interesting theory: What if swiping actually changed things?", "Tested by: millions. Results: mostly yes. Sample size sufficient. Your turn to test.", "promotions"),

    ("Your dating life is a riddle: How can something be everywhere and nowhere?", "Answer: When you have the app but never use it. Solve the riddle. Open the app.", "promotions"),
    ("You've mastered everything except initiating romantic connections", "Impressive skill set. One gap. The app closes the gap. Low effort. High potential. Log in.", "promotions"),
    ("Science fact: Effort applied to the app correlates with connections made", "Peer reviewed. Published. Undeniable. Apply effort. Open the app. Get connections. Science.", "promotions"),
    ("The app has more to offer than your current strategy of not using it", "Comparison: nothing vs something. Something wins. Open the app. Choose something.", "promotions"),
    ("Your dating status: Pending action.", "Action items: 1. Open app. 2. Swipe on someone interesting. 3. Send a message. Status will update.", "promotions"),
    ("Everyone who found someone had one thing in common: they tried", "You may have noticed the pattern. The common variable. The thing everyone did. Try it.", "promotions"),
    ("Your love life is like a plant you bought and then forgot to water", "Still alive. Technically. But needs attention. Water it. Open the app. Tend to it.", "promotions"),
    ("Most people regret what they didn't try more than what they did", "Register the regret preemptively. Open the app. Try. Regret nothing. Classic strategy.", "promotions"),
    ("The app has been waiting patiently. It has the patience of a saint.", "But saints aren't infinite. And your matches won't wait forever. Show up while there's still time.", "promotions"),
    ("Good news: You don't need a plan. You just need to start.", "Overplanning is a form of procrastination. Stop planning. Open the app. Start.", "promotions"),

    ("Your love life: Concept sounds great. Execution pending.", "Execution date: today. Method: opening the app. Supervisor: yourself. Begin execution.", "promotions"),
    ("The funniest joke you could tell your future self: 'I almost didn't try'", "Don't make it true. Try. Open the app. Become the punchline of a success story instead.", "promotions"),
    ("You've been strategically resting for long enough", "Rest phase complete. Active phase begins now. The app is loaded and waiting. Enter active phase.", "promotions"),
    ("Your person is probably also reading a motivational push notification right now", "What if you both open the app at the same time? Synchronicity. Or at least matching activity.", "promotions"),
    ("Your dating life needs a password reset: The password is 'actually try'", "Old password: passive waiting. New password: opening the app. Update required. Log in.", "promotions"),
    ("Here is permission to want love and also do something about it", "Both are allowed. Wanting it and trying for it. Permission granted. Proceed to the app.", "promotions"),
    ("You're the only obstacle between yourself and an open app", "Obstacles can be moved. This one especially. It's not heavy. It's just a tap. Remove it.", "promotions"),
    ("Everything you've been waiting for is available at full effort capacity", "Inventory available: connections, conversations, potential relationships. Requires: effort. Stock up.", "promotions"),
    ("Productivity hack: 15 minutes in the app beats hours of wishing", "Time ROI is excellent. 15 minutes invested, potentially years of companionship returned. Log in.", "promotions"),
    ("Your move.", "Simple. Clear. Actionable. Open the app. Make it.", "promotions"),
    ("Interesting observation: everyone who has a partner was once single", "They all did one thing differently. They tried. You know the next move. Make it.", "promotions"),
    ("Your dating life is not broken. It's just not running.", "Technical status: stopped. Fix: press start. Start = opening the app. Press it.", "promotions"),
    ("You've talked yourself out of trying. Talk yourself back in.", "The internal negotiation has been dragging on. Settle it. 'Yes, try' wins. Log in.", "promotions"),
    ("Dating is the one thing that gets better the more you actually do it", "Unlike running, it doesn't hurt the next day. And the rewards are significantly warmer. Log in.", "promotions"),
    ("Low-effort way to dramatically improve your love life: open the app", "Not a trick. Genuinely the minimum viable action. Minimum effort, maximum potential. Do it.", "promotions"),
    ("Here's the secret: consistency in small doses beats everything", "Fifteen minutes daily. Every day. That's the whole strategy. Simple. Effective. Log in.", "promotions"),
    ("You are the variable that determines your dating outcome", "Not luck, not timing, not fate. You. Your action. Your presence. Log in and be the variable.", "promotions"),
    ("The app is doing its part. It's waiting on you to do yours.", "Partnership. It finds matches. You show up. Together you get results. Show up.", "promotions"),
    ("If doing nothing hasn't worked, doing something probably will", "Solid hypothesis. Ready to test. Method: open app. Expected result: better than nothing. Run the test.", "promotions"),
    ("Your love life is not a coincidence waiting to happen. It's a choice.", "Active people make active choices. The choice is opening the app. Make it. Today.", "promotions"),
]


# Combine all 400 templates
ALL_TEMPLATES = FUNNY_AND_NORMAL_TEMPLATES + PICKUP_LINE_TEMPLATES + WITTY_TEMPLATES

print(f"Total templates: {len(ALL_TEMPLATES)}")
print(f"  - Funny & Normal: {len(FUNNY_AND_NORMAL_TEMPLATES)}")
print(f"  - Pickup Lines:   {len(PICKUP_LINE_TEMPLATES)}")
print(f"  - Witty:          {len(WITTY_TEMPLATES)}")


async def seed_templates():
    async with AsyncSessionLocal() as db:
        print("=" * 60)
        print("Removing ALL existing marketing templates...")
        print("=" * 60)

        await db.execute(text("DELETE FROM marketing_templates"))
        await db.commit()
        print("✓ All existing templates removed.")

        print("=" * 60)
        print(f"Seeding {len(ALL_TEMPLATES)} fresh templates...")
        print("=" * 60)

        inserted = 0
        for title, body, ntype in ALL_TEMPLATES:
            await db.execute(
                text("""
                    INSERT INTO marketing_templates
                        (name, language_code, title, body, notif_type, is_active, created_at, updated_at)
                    VALUES
                        (:name, 'en', :title, :body, :notif_type, TRUE, NOW(), NOW())
                """),
                {
                    "name": f"Gen - {title[:40]}",
                    "title": title,
                    "body": body,
                    "notif_type": ntype,
                },
            )
            inserted += 1

        await db.commit()
        print(f"✓ Successfully inserted {inserted} templates.")
        print("")

        total = await db.execute(
            text("SELECT COUNT(*) FROM marketing_templates WHERE is_active = TRUE")
        )
        print(f"Total active templates: {total.scalar()}")


if __name__ == "__main__":
    asyncio.run(seed_templates())
