from fastapi import APIRouter

from app.api.v1.endpoints import auth, location, lookup, profile, subscription, upload, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(profile.router)
api_router.include_router(users.router)
api_router.include_router(lookup.router)
api_router.include_router(upload.router)
api_router.include_router(subscription.router)
api_router.include_router(location.router)
