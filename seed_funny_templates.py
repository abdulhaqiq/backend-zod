"""
Seed 100 funny, witty Zomato-style marketing templates.
Run: python seed_funny_templates.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import text
from app.db.session import AsyncSessionLocal

# 100 funny, witty, Zomato-style dating app notifications
FUNNY_TEMPLATES = [
    # Food & Dating Mashups (1-20)
    ("Love at First Bite", "Your soulmate is ordering from the same restaurant. Swipe now before they leave! 🍕❤️"),
    ("Single? So is this pizza", "But at least the pizza won't ghost you. Find someone real tonight! 🍕😏"),
    ("Your crush just logged in", "And they're probably thinking about you... or tacos. Make your move! 🌮💭"),
    ("Netflix asked where you are", "Your date is waiting. Stop binge-watching alone! 📺❤️"),
    ("Calories don't count on dates", "Neither does awkwardness. Let's find you someone! 🍰😉"),
    
    ("Your ex is eating alone", "You deserve better. So does your dinner plan. Find a date! 🍝💔"),
    ("Relationship status: Hungry", "Feed your heart. Someone's waiting to share fries with you! 🍟❤️"),
    ("Love is in the air", "So is the smell of fresh pizza. Both waiting for you! 🍕✨"),
    ("Your mom asked about grandkids again", "Time to find someone. Like, now. 👶💍"),
    ("Breaking: Local single spotted", "They like dogs AND pizza. What are you waiting for? 🐕🍕"),
    
    ("Dinner party for 2", "Table booked. Date missing. Be the +1 someone needs! 🍽️❤️"),
    ("Someone swiped right on you", "While eating biryani. True love exists! 🍛💕"),
    ("Your future spouse is online", "Probably scrolling and eating. Join them! 📱🍕"),
    ("Roses are red", "Violets are blue, someone here is perfect for you! 🌹💙"),
    ("Warning: Cuteness overload", "New profiles just dropped. Prepare your pickup lines! 😍🔥"),
    
    ("Your dating life called", "It wants to exist. Let's make it happen! 📞❤️"),
    ("Relationship goal: Unlocked", "Just kidding. But it could be! Swipe now! 🎯💕"),
    ("Singles in your area", "No really, actual real humans. Not bots! 🙋‍♀️🙋‍♂️"),
    ("Love doesn't deliver", "But we do. Find your match tonight! 🚀❤️"),
    ("Your soulmate is 2 swipes away", "Or maybe 3. We're not psychic. But close! 🔮💕"),
    
    # Funny One-Liners (21-40)
    ("Stop swiping on your ex's profile", "There are 547 better options. Literally. 📊😅"),
    ("Your future in-laws await", "No pressure. Just kidding. LOTS of pressure! Swipe! 👨‍👩‍👧‍👦💍"),
    ("Monday motivation", "Find someone who laughs at your dad jokes. They exist! 😂❤️"),
    ("Your dog wants a new friend", "One with two legs this time. Find them a human! 🐕👫"),
    ("Science fact", "You're 87% more attractive when smiling. So smile & swipe! 😊💯"),
    
    ("Plot twist", "Your match is closer than you think. Maybe next door? 🏠👀"),
    ("Life is short", "Your single phase doesn't have to be. Swipe now! ⏰❤️"),
    ("Urgent: Heart available", "Location: Right here. Status: Looking for you! 💝📍"),
    ("Your bed is too big for one", "Get a cat. Or a date. We recommend the date! 🐱❤️"),
    ("Someone just super-liked you", "While sipping chai. That's dedication! ☕💕"),
    
    ("Your grandma called", "She wants great-grandchildren. No pressure! 👵💍"),
    ("Breaking news", "Local single discovers dating app. Could be your soulmate! 📰❤️"),
    ("Your horoscope says", "Today's the day. Who are we to argue with stars? ⭐💫"),
    ("Loneliness: Cancelled", "Your subscription to single life can end today! ❌😊"),
    ("Pro tip", "Confidence is hot. So is pizza. Find both tonight! 🍕🔥"),
    
    ("Your perfect match exists", "They're probably eating ice cream right now. Join them! 🍦❤️"),
    ("Swipe right for instant happiness", "*Results may vary. Happiness guaranteed eventually! 😄💕"),
    ("Your future partner is online", "And they're probably just as awkward. Perfect match! 😅❤️"),
    ("Relationship speedrun", "Any%. New record loading. Will you be player 2? 🎮💕"),
    ("Someone misses you", "They just don't know you yet. Fix that! 👤❤️"),
    
    # Witty & Clever (41-60)
    ("Your heart has 0 unread messages", "Let's change that. Someone's typing... 💬❤️"),
    ("Notification: Cute human nearby", "Distance: Swipeable. Action required! 📍😊"),
    ("Your dating app misses you", "It's been 3 hours. We're not clingy, YOU are! 😤❤️"),
    ("Love language: Pizza", "Find someone who speaks it fluently! 🍕🗣️"),
    ("Your wingman has a message", "Us. We're your wingman. And we found matches! 🦅❤️"),
    
    ("Roses are red, violets are blue", "Your match is online, what will you do? 🌹💙"),
    ("Your alone time membership", "Has been automatically cancelled. Welcome back! ❌🎉"),
    ("Someone's profile made us LOL", "And we think you'd laugh too. Check them out! 😂💕"),
    ("Your type is online", "Tall. Dark. Handles. I mean, handsome! 😏❤️"),
    ("Swipe right to unlock happiness", "In-app purchases: None. Just your time & heart! 💰❤️"),
    
    ("Your crush from college", "Is on here. Maybe. Statistically possible! 🎓👀"),
    ("Dating tip #847", "Don't swipe while eating. Actually, do. It's more authentic! 🍔📱"),
    ("Your future wedding playlist", "Needs a partner. Apply within! 🎵💍"),
    ("Attention: Hot singles", "No, seriously. Like, temperature-wise. Stay hydrated! 🔥💧"),
    ("Your perfect match ratio", "1 in 500. We did the math. Now you do the swiping! 🧮❤️"),
    
    ("Love calculator says", "92% compatibility detected. Science doesn't lie! 🧪💕"),
    ("Your soulmate status", "Loading... 47% complete. Swipe to finish! ⏳❤️"),
    ("Relationship upgrade available", "From single to taken. Install now! 📲💕"),
    ("Warning: Feelings ahead", "Proceed with caution. Or don't. YOLO! ⚠️❤️"),
    ("Your future spouse just", "Ordered the same coffee as you. Cosmic! ☕✨"),
    
    # Relatable & Fun (61-80)
    ("It's cuffing season", "And you're still solo. Let's fix this tragedy! ❄️💔"),
    ("Your bed called", "It's tired of just you. Find a +1! 🛏️😴"),
    ("Someone thinks you're cute", "Their standards might be questionable, but hey! 😄❤️"),
    ("Your mom's friend's daughter", "Is engaged. Again. Find your match before next wedding! 💍😅"),
    ("Swipe right for free serotonin", "Side effects: Butterflies, smiling, happiness! 🦋😊"),
    
    ("Your lockscreen is lonely", "Add a couple photo to your future gallery! 📸❤️"),
    ("Someone super-liked your bio", "The one you wrote at 2am. They're a keeper! 🌙💕"),
    ("Your Instagram explore page", "Is full of couples. Time to join them! 📱👫"),
    ("Relationship forecast", "99% chance of love today. With a chance of butterflies! 🦋❤️"),
    ("Your future in-laws are nice", "We checked. Now go find their kid! 👨‍👩‍👦✅"),
    
    ("Someone shares your vibe", "And your taste in memes. Marry them! 😂💍"),
    ("Your perfect match drinks", "The same overpriced coffee. You're meant to be! ☕💕"),
    ("Swipe right to cure boredom", "Side effects may include dates, love, and happiness! 💊❤️"),
    ("Your weekend needs an upgrade", "From Netflix to Netflix & someone. Swipe now! 📺👫"),
    ("Someone just viewed your profile", "While listening to your favorite song. Fate! 🎵✨"),
    
    ("Your friends are all coupled up", "Time to catch up. Literally! 👫👫"),
    ("Happiness is one swipe away", "Or maybe two. Three max. We promise! 👆❤️"),
    ("Your future couple photo", "Is waiting. Strike a pose (after finding them)! 📸💕"),
    ("Someone shares your spotify", "Wrapped energy. That's basically marriage! 🎵💍"),
    ("Your dating app patience", "Has been rewarded. New matches incoming! 🎁❤️"),
    
    # Cheeky & Playful (81-100)
    ("Your alone time", "Is cancelled. Someone wants to share fries! 🍟❤️"),
    ("Swipe right or regret forever", "Dramatic? Yes. True? Also yes! 🎭💕"),
    ("Your future kids", "Are asking why you're still swiping. Fair point! 👶❓"),
    ("Someone's waiting for you", "Not creepily. Romantically. Big difference! 🌹😊"),
    ("Your heart has notifications", "3 new matches want to make you smile! 💌😊"),
    
    ("Dating life: ERROR 404", "Let's fix that bug together! 🐛❤️"),
    ("Your soulmate is scrolling", "Probably right now. Don't miss out! 📱💕"),
    ("Swipe right to start laughing", "Your date's jokes are... well, they're trying! 😂❤️"),
    ("Your future partner loves", "The same weird food combos. Keeper alert! 🍕🥤"),
    ("Someone thinks your quirks", "Are adorable. Not weird. Adorable! 😊💕"),
    
    ("Your Valentine needs planning", "It's never too early. Or too late! 💘📅"),
    ("Relationship status update", "In progress... Almost there... Complete! 📊❤️"),
    ("Your perfect match is", "Probably also doom-scrolling. Say hi! 📱👋"),
    ("Someone's profile says", "Looking for you. Specifically. Check it out! 🔍❤️"),
    ("Your future love story", "Starts with one swipe. Make it count! 📖💕"),
    
    ("Swipe right for adventure", "And by adventure we mean dates. And love! 🗺️❤️"),
    ("Your type is active now", "Tall. Funny. Available. Holy trinity! 🙏💕"),
    ("Someone shares your love for", "Random 2am snacks. This is serious! 🌙🍕"),
    ("Your relationship status", "Needs an update. From single to match! 📝❤️"),
    ("The universe has spoken", "Your match awaits. Don't keep them waiting! 🌌💕"),
]

async def seed_templates():
    async with AsyncSessionLocal() as db:
        # Check if templates already exist
        existing = await db.execute(text("SELECT COUNT(*) FROM marketing_templates WHERE name LIKE 'Funny %'"))
        count = existing.scalar()
        
        if count >= 100:
            print(f"✅ {count} funny templates already exist. Skipping...")
            return
        
        print("🎉 Adding 100 funny Zomato-style templates...")
        
        for i, (title, body) in enumerate(FUNNY_TEMPLATES, 1):
            await db.execute(
                text("""
                    INSERT INTO marketing_templates (name, language_code, title, body, notif_type, is_active)
                    VALUES (:name, :lang, :title, :body, :type, :active)
                    ON CONFLICT DO NOTHING
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
        
        await db.commit()
        print("✅ 100 funny templates added!")
        
        # Update India timezone config (6 times a day: 9am, 12pm, 3pm, 6pm, 8pm, 10pm IST)
        print("\n📍 Configuring India timezone (6 notifications/day)...")
        import json
        await db.execute(
            text("""
                INSERT INTO marketing_countries (name, code, region, tz_name, peak_hours, primary_language, is_active)
                VALUES ('India', 'IN', 'Asia', 'Asia/Kolkata', :hours::jsonb, 'en', true)
                ON CONFLICT (code, tz_name) DO UPDATE SET
                    peak_hours = EXCLUDED.peak_hours,
                    is_active = true
            """),
            {"hours": json.dumps([9, 12, 15, 18, 20, 22])}  # 9am, 12pm, 3pm, 6pm, 8pm, 10pm IST
        )
        
        # Update Dubai/UAE timezone (4-5 times a day: 10am, 1pm, 6pm, 9pm GST)
        print("📍 Configuring Dubai/UAE timezone (4 notifications/day)...")
        await db.execute(
            text("""
                INSERT INTO marketing_countries (name, code, region, tz_name, peak_hours, primary_language, is_active)
                VALUES ('United Arab Emirates', 'AE', 'Middle East', 'Asia/Dubai', :hours, 'en', true)
                ON CONFLICT (code, tz_name) DO UPDATE SET
                    peak_hours = EXCLUDED.peak_hours,
                    is_active = true
            """),
            {"hours": [10, 13, 18, 21]}  # 10am, 1pm, 6pm, 9pm GST
        )
        
        # Update Saudi Arabia timezone (4 times a day: 10am, 2pm, 6pm, 9pm AST)
        print("📍 Configuring Saudi Arabia timezone (4 notifications/day)...")
        await db.execute(
            text("""
                INSERT INTO marketing_countries (name, code, region, tz_name, peak_hours, primary_language, is_active)
                VALUES ('Saudi Arabia', 'SA', 'Middle East', 'Asia/Riyadh', :hours, 'ar', true)
                ON CONFLICT (code, tz_name) DO UPDATE SET
                    peak_hours = EXCLUDED.peak_hours,
                    is_active = true
            """),
            {"hours": [10, 14, 18, 21]}  # 10am, 2pm, 6pm, 9pm AST
        )
        
        await db.commit()
        print("✅ Timezone configurations updated!")
        
        print("\n📊 Summary:")
        print("   • 100 funny templates added")
        print("   • India: 6 notifications/day (9am, 12pm, 3pm, 6pm, 8pm, 10pm IST)")
        print("   • Dubai: 4 notifications/day (10am, 1pm, 6pm, 9pm GST)")
        print("   • Saudi: 4 notifications/day (10am, 2pm, 6pm, 9pm AST)")

if __name__ == "__main__":
    asyncio.run(seed_templates())
