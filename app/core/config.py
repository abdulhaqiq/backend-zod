from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    APP_NAME: str = "Mil API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Mobile-app shared secret — every request from the app must include:
    #   X-App-Key: <APP_API_KEY>
    # Leave empty ("") to disable the check (not recommended in production).
    APP_API_KEY: str = ""

    # Rate limiting (requests per minute, per IP)
    RATE_LIMIT_OTP: str = "5/minute"        # send-otp / resend-otp
    RATE_LIMIT_AUTH: str = "20/minute"      # login / refresh / verify
    RATE_LIMIT_WRITE: str = "60/minute"     # POST/PATCH/DELETE general
    RATE_LIMIT_READ: str = "120/minute"     # GET general

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

    # Email (SMTP) — used for university email verification
    # Leave blank to use dev-mode (OTP is logged to console instead of sent)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = "noreply@zod.app"
    EMAIL_FROM_NAME: str = "Zod"

    # OTP policy
    OTP_EXPIRE_MINUTES: int = 10
    OTP_MAX_ATTEMPTS: int = 5
    OTP_BLOCK_MINUTES: int = 30
    OTP_MAX_SENDS_PER_HOUR: int = 3

    # LinkedIn OAuth
    LINKEDIN_CLIENT_ID: str = ""
    LINKEDIN_CLIENT_SECRET: str = ""

    # LinkedIn Scraper APIs
    # Tier 1 — Apify LinkedIn Profile Scraper (https://apify.com/apify/linkedin-profile-scraper)
    APIFY_API_TOKEN: str = ""
    # Tier 2 — Proxycurl (https://nubela.co/proxycurl)  ~$0.01/call, best quality
    PROXYCURL_API_KEY: str = ""
    # Tier 3 — RapidAPI Fresh LinkedIn Profile Data (https://rapidapi.com/freshdata-freshdata-default/api/fresh-linkedin-profile-data)
    RAPIDAPI_KEY: str = ""

    # RevenueCat
    REVENUECAT_PUBLIC_KEY: str = ""        # iOS SDK public key (appl_... or test_...)
    REVENUECAT_SECRET_KEY: str = ""        # V1 secret key from RevenueCat dashboard
    REVENUECAT_WEBHOOK_AUTH: str = ""      # Authorization header value for webhooks

    # AWS Rekognition (face detection + matching — used for verification)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"

    # Expo Push Notifications (optional — improves rate limits in production)
    # Generate at: https://expo.dev/accounts/<username>/settings/access-tokens
    EXPO_ACCESS_TOKEN: str = ""

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
        )

    @property
    def SYNC_DATABASE_URL(self) -> str:
        return (
            f"postgresql+psycopg2://{self.DB_USERNAME}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            f"?sslmode={self.DB_SSLMODE}"
        )


settings = Settings()
