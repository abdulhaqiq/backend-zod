from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App
    APP_NAME: str = "Mil API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Database
    DB_USERNAME: str
    DB_PASSWORD: str
    DB_HOST: str
    DB_PORT: int = 25060
    DB_NAME: str
    DB_SSLMODE: str = "require"

    # Twilio
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_SMS: str = ""
    TWILIO_FROM_WHATSAPP: str = "whatsapp:+14155238886"

    # Apple Sign In
    APPLE_APP_BUNDLE_ID: str = ""

    # Google Maps (server-side reverse geocoding)
    GOOGLE_MAPS_API_KEY: str = ""

    # OpenAI
    OPENAI_API_KEY: str = ""

    # Facebook Sign In
    FACEBOOK_APP_ID: str = ""
    FACEBOOK_APP_SECRET: str = ""

    # OTP policy
    OTP_EXPIRE_MINUTES: int = 10
    OTP_MAX_ATTEMPTS: int = 5
    OTP_BLOCK_MINUTES: int = 30
    OTP_MAX_SENDS_PER_HOUR: int = 3

    # RevenueCat
    REVENUECAT_PUBLIC_KEY: str = ""        # iOS SDK public key (appl_... or test_...)
    REVENUECAT_SECRET_KEY: str = ""        # V1 secret key from RevenueCat dashboard
    REVENUECAT_WEBHOOK_AUTH: str = ""      # Authorization header value for webhooks

    # DigitalOcean Spaces
    DO_SPACES_KEY: str = ""
    DO_SPACES_SECRET: str = ""
    DO_SPACES_REGION: str = "sfo3"
    DO_SPACES_BUCKET: str = "zod"
    DO_SPACES_ENDPOINT: str = "https://sfo3.digitaloceanspaces.com"
    DO_SPACES_CDN_BASE: str = "https://zod.sfo3.cdn.digitaloceanspaces.com"

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USERNAME}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            f"?ssl={self.DB_SSLMODE}"
        )

    @property
    def SYNC_DATABASE_URL(self) -> str:
        return (
            f"postgresql+psycopg2://{self.DB_USERNAME}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            f"?sslmode={self.DB_SSLMODE}"
        )


settings = Settings()
