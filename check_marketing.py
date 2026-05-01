#!/usr/bin/env python3
"""Check marketing settings for India."""
import asyncio
from sqlalchemy import select, text, desc
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from app.models.marketing import MarketingCountry, MarketingCampaign, MarketingTemplate

async def check():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        # Check India settings
        result = await session.execute(
            select(MarketingCountry).where(MarketingCountry.code == 'IN')
        )
        india = result.scalar_one_or_none()
        
        print('=== INDIA (IN) MARKETING SETTINGS ===')
        if india:
            print(f'Name: {india.name}')
            print(f'Code: {india.code}')
            print(f'Timezone: {india.tz_name}')
            print(f'Peak hours: {india.peak_hours}')
            print(f'Is active: {india.is_active}')
        else:
            print('India not found in marketing countries!')
        
        # Check all countries
        print('\n=== ALL MARKETING COUNTRIES ===')
        result = await session.execute(
            select(MarketingCountry).order_by(MarketingCountry.code)
        )
        countries = result.scalars().all()
        for c in countries:
            print(f'{c.code}: {c.name} | TZ: {c.tz_name} | Peak: {c.peak_hours} | Active: {c.is_active}')
        
        # Check recent campaigns
        print('\n=== RECENT MARKETING CAMPAIGNS (last 20) ===')
        result = await session.execute(
            select(MarketingCampaign)
            .order_by(desc(MarketingCampaign.created_at))
            .limit(20)
        )
        campaigns = result.scalars().all()
        for camp in campaigns:
            print(f'{camp.created_at}: {camp.campaign_name}')
        
        # Check templates
        print('\n=== MARKETING TEMPLATES ===')
        result = await session.execute(
            select(MarketingTemplate).where(MarketingTemplate.is_active == True)
        )
        templates = result.scalars().all()
        for t in templates:
            print(f'{t.language_code}: {t.notif_type} | "{t.title}"')
            
        # Check your user marketing sends
        print('\n=== YOUR RECENT MARKETING SENDS ===')
        result = await session.execute(
            text('''
                SELECT u.email, ums.sent_at, mc.campaign_name
                FROM user_marketing_sends ums
                JOIN users u ON u.id = ums.user_id
                LEFT JOIN marketing_campaigns mc ON mc.id = ums.campaign_id
                WHERE u.email LIKE '%kumshey%'
                ORDER BY ums.sent_at DESC
                LIMIT 10
            ''')
        )
        sends = result.all()
        for s in sends:
            print(f'{s.sent_at}: {s.campaign_name or "Unknown"}')
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check())
