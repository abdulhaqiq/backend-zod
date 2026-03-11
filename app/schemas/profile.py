import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class ProfileUpdateRequest(BaseModel):
    """
    All fields optional — send only what the current onboarding step collects.
    The backend merges the payload into the existing user row.
    """
    full_name: str | None = Field(None, max_length=255)
    date_of_birth: date | None = None
    gender: str | None = Field(None, max_length=32)
    bio: str | None = Field(None, max_length=300)
    purpose: list[str] | None = None
    height_cm: int | None = Field(None, ge=100, le=250)
    interests: list[str] | None = None
    lifestyle: dict[str, Any] | None = None
    values_list: list[str] | None = None
    prompts: list[dict[str, Any]] | None = None
    photos: list[str] | None = None
    is_onboarded: bool | None = None
    education_level: str | None = Field(None, max_length=64)
    looking_for: str | None = Field(None, max_length=64)
    family_plans: str | None = Field(None, max_length=64)
    have_kids: str | None = Field(None, max_length=64)
    star_sign: str | None = Field(None, max_length=32)
    religion: str | None = Field(None, max_length=64)
    languages: list[str] | None = None
    voice_prompts: list[dict[str, Any]] | None = None   # [{topic, url, duration_sec}]
    causes: list[str] | None = None
    work_experience: list[dict[str, Any]] | None = None
    education: list[dict[str, Any]] | None = None
    city: str | None = Field(None, max_length=128)
    hometown: str | None = Field(None, max_length=128)
    address: str | None = Field(None, max_length=512)
    country: str | None = Field(None, max_length=128)
    latitude: float | None = None
    longitude: float | None = None
    dark_mode: bool | None = None


class MeResponse(BaseModel):
    id: uuid.UUID
    phone: str | None
    email: str | None
    full_name: str | None
    date_of_birth: date | None
    gender: str | None
    bio: str | None
    purpose: list[str] | None
    height_cm: int | None
    interests: list[str] | None
    lifestyle: dict[str, Any] | None
    values_list: list[str] | None
    prompts: list[dict[str, Any]] | None
    photos: list[str] | None
    education_level: str | None
    looking_for: str | None
    family_plans: str | None
    have_kids: str | None
    star_sign: str | None
    religion: str | None
    languages: list[str] | None
    voice_prompts: list[dict[str, Any]] | None
    causes: list[str] | None
    work_experience: list[dict[str, Any]] | None
    education: list[dict[str, Any]] | None
    city: str | None
    hometown: str | None
    address: str | None
    country: str | None
    latitude: float | None
    longitude: float | None
    dark_mode: bool
    is_active: bool
    is_verified: bool
    is_onboarded: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
