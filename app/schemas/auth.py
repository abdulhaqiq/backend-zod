import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DeviceInfo(BaseModel):
    """Optional device fingerprint sent from the mobile app."""
    ip_address: str | None = None
    device_id: str | None = None        # iOS identifierForVendor / Android ID
    device_model: str | None = None     # e.g. "iPhone 15 Pro"
    device_os: str | None = None        # e.g. "iOS 17.4"
    device_name: str | None = None      # user's device nickname, e.g. "Abdul's iPhone"
    network_type: str | None = None     # "wifi" | "cellular" | "unknown"
    carrier: str | None = None          # SIM carrier name, e.g. "Airtel", "Jio"
    app_version: str | None = None      # app build version, e.g. "1.2.3"


class PhoneSendOtpRequest(BaseModel):
    phone: str = Field(..., description="E.164 format phone number, e.g. +12125551234")
    channel: Literal["sms", "whatsapp"] = "sms"
    device: DeviceInfo | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("+") or not v[1:].isdigit() or len(v) < 8:
            raise ValueError("Phone must be in E.164 format, e.g. +12125551234")
        return v


class PhoneVerifyOtpRequest(BaseModel):
    phone: str = Field(..., description="E.164 format phone number")
    code: str = Field(..., min_length=5, max_length=5, description="5-digit OTP code")
    device: DeviceInfo | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("+") or not v[1:].isdigit() or len(v) < 8:
            raise ValueError("Phone must be in E.164 format, e.g. +12125551234")
        return v

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("OTP code must be 5 digits")
        return v


class AppleAuthRequest(BaseModel):
    identity_token: str = Field(..., description="JWT identity token from Apple Sign In")
    full_name: str | None = None
    device: DeviceInfo | None = None


class GoogleAuthRequest(BaseModel):
    access_token: str = Field(..., description="Short-lived access token from Google Sign In")
    device: DeviceInfo | None = None


class FacebookAuthRequest(BaseModel):
    access_token: str = Field(..., description="Short-lived access token from Facebook SDK")
    device: DeviceInfo | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access token expiry
    is_new_user: bool = False


class PhoneLinkRequest(BaseModel):
    phone: str = Field(..., description="E.164 format phone number")
    code: str = Field(..., min_length=5, max_length=5, description="5-digit OTP code")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("+") or not v[1:].isdigit() or len(v) < 8:
            raise ValueError("Phone must be in E.164 format, e.g. +12125551234")
        return v

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("OTP code must be 5 digits")
        return v


class RefreshRequest(BaseModel):
    refresh_token: str


class OtpSentResponse(BaseModel):
    message: str
    channel: str
    expires_in_seconds: int


class UserResponse(BaseModel):
    id: uuid.UUID
    phone: str | None
    email: str | None
    full_name: str | None
    is_active: bool
    is_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}
