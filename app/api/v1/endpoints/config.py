"""
App configuration endpoint — returns public config settings like test mode status.
"""
from fastapi import APIRouter
from pydantic import BaseModel
import os

router = APIRouter(prefix="/config", tags=["config"])


class ConfigResponse(BaseModel):
    """Public configuration settings."""
    test_mode_enabled: bool


@router.get("/public", response_model=ConfigResponse, summary="Get public app configuration")
async def get_public_config():
    """
    Returns public configuration settings that don't require authentication.
    
    - **test_mode_enabled**: Whether phone number login is enabled (test mode)
    """
    # Check environment variable for test mode (defaults to False for production)
    test_mode = os.getenv("TEST_MODE_ENABLED", "false").lower() in ("true", "1", "yes")
    
    return ConfigResponse(test_mode_enabled=test_mode)
