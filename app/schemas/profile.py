import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class ProfileUpdateRequest(BaseModel):
    """
    All fields optional — send only what the current step collects.
    Categorical single-value fields use lookup_options integer IDs.
    Categorical multi-value fields are lists of lookup_options IDs (integers).
    Lifestyle dict values are lookup_options IDs keyed by trait name.
    Purpose is a list of relationship_types IDs.
    Free-text, booleans, and complex objects are unchanged.
    """
    full_name: str | None = Field(None, max_length=255)
    email: EmailStr | None = None
    date_of_birth: date | None = None
    gender_id: int | None = None                          # lookup_options (category=gender)
    bio: str | None = Field(None, max_length=300)

    # Multi-value ID arrays
    purpose: list[int] | None = None                      # [relationship_types.id, ...]
    interests: list[int] | None = None                    # [lookup_options.id, ...] category=interests
    values_list: list[int] | None = None                  # [lookup_options.id, ...] category=values_list
    languages: list[int] | None = None                    # [lookup_options.id, ...] category=language
    causes: list[int] | None = None                       # [lookup_options.id, ...] category=causes

    # Lifestyle dict: keys are trait names, values are lookup_options IDs
    lifestyle: dict[str, Any] | None = None               # {drinking: id, smoking: id, exercise: id, diet: id}

    # Height (raw integer — not a lookup)
    height_cm: int | None = Field(None, ge=100, le=250)

    # Free-text rich content
    prompts: list[dict[str, Any]] | None = None
    photos: list[str] | None = None

    # Single-value ID fields
    education_level_id: int | None = None                 # lookup_options (category=education_level)
    looking_for_id: int | None = None                     # lookup_options (category=looking_for)
    family_plans_id: int | None = None                    # lookup_options (category=family_plans)
    have_kids_id: int | None = None                       # lookup_options (category=have_kids)
    star_sign_id: int | None = None                       # lookup_options (category=star_sign)
    religion_id: int | None = None                        # lookup_options (category=religion)
    ethnicity_id: int | None = None                       # lookup_options (category=ethnicity)

    # University / institution
    university: str | None = Field(None, max_length=255)

    # Privacy settings
    hide_age:                 bool | None = None
    hide_distance:            bool | None = None
    require_verified_to_chat: bool | None = None

    # Pro features
    is_incognito:        bool | None = None
    travel_mode_enabled: bool | None = None
    auto_zod_enabled:    bool | None = None
    travel_city:         str | None = Field(None, max_length=128)
    travel_country:      str | None = Field(None, max_length=128)

    # Mood / vibe status
    mood_emoji: str | None = Field(None, max_length=8)
    mood_text:  str | None = Field(None, max_length=60)

    # Booleans / flags
    is_onboarded: bool | None = None
    dark_mode: bool | None = None
    best_photo_enabled: bool | None = None

    # Location (free-text only — coordinates are set exclusively via
    # POST /location/update and POST /location/change-city, never via profile PATCH)
    city: str | None = Field(None, max_length=128)
    hometown: str | None = Field(None, max_length=128)
    address: str | None = Field(None, max_length=512)
    country: str | None = Field(None, max_length=128)

    # Rich structured data (no lookup IDs needed)
    voice_prompts: list[dict[str, Any]] | None = None     # [{topic, url, duration_sec}]
    work_experience: list[dict[str, Any]] | None = None   # [{job_title, company, start_year, end_year, current}]
    education: list[dict[str, Any]] | None = None         # [{institution, course, degree, grad_year}]

    # ── Zod Work profile ──────────────────────────────────────────────────────
    work_photos: list[str] | None = None
    work_prompts: list[dict[str, Any]] | None = None
    work_matching_goals: list[int] | None = None          # [lookup_options.id, ...] category=work_matching_goals
    work_are_you_hiring: bool | None = None
    work_commitment_level_id: int | None = None           # lookup_options (category=work_commitment_level)
    work_skills: list[int] | None = None                  # [lookup_options.id, ...] category=work_skills
    work_equity_split_id: int | None = None               # lookup_options (category=work_equity_split)
    work_industries: list[int] | None = None              # [lookup_options.id, ...] category=work_industries
    work_scheduling_url: str | None = Field(None, max_length=512)
    work_who_to_show_id: int | None = None                # lookup_options (category=work_who_to_show)
    work_priority_startup: bool | None = None
    work_headline: str | None = Field(None, max_length=256)
    work_persona: str | None = Field(None, pattern="^(founder|job_seeker|both)$")
    work_num_founders_id: int | None = None               # lookup_options (category=work_num_founders)
    work_primary_role_id: int | None = None               # lookup_options (category=work_role)
    work_years_experience_id: int | None = None           # lookup_options (category=work_years_experience)
    work_job_search_status_id: int | None = None          # lookup_options (category=work_job_search_status)

    # ── Notification preferences ──────────────────────────────────────────────
    notif_new_match:     bool | None = None
    notif_new_message:   bool | None = None
    notif_super_like:    bool | None = None
    notif_liked_profile: bool | None = None
    notif_profile_views: bool | None = None
    notif_ai_picks:      bool | None = None
    notif_promotions:    bool | None = None
    notif_dating_tips:   bool | None = None

    # ── Halal profile fields ──────────────────────────────────────────────────
    sect_id:              int | None = None                # lookup_options (category=sect)
    prayer_frequency_id:  int | None = None                # lookup_options (category=prayer_frequency)
    marriage_timeline_id: int | None = None                # lookup_options (category=marriage_timeline)
    wali_email:           str | None = Field(None, max_length=255)
    wali_verified:        bool | None = None               # can be set by the user themselves
    blur_photos_halal:    bool | None = None
    halal_mode_enabled:   bool | None = None
    work_mode_enabled:    bool | None = None

    # ── LinkedIn ──────────────────────────────────────────────────────────────
    linkedin_url: str | None = Field(None, max_length=512)

    # ── Discover filter preferences ───────────────────────────────────────────
    filter_age_min:         int | None = Field(None, ge=18, le=80)
    filter_age_max:         int | None = Field(None, ge=18, le=80)
    filter_max_distance_km: int | None = Field(None, ge=1, le=80)
    filter_verified_only:   bool | None = None
    filter_star_signs:      list[int] | None = None        # [lookup_options.id] category=star_sign
    filter_interests:       list[int] | None = None        # [lookup_options.id] category=interests
    filter_languages:       list[int] | None = None        # [lookup_options.id] category=language
    filter_religions:       list[int] | None = None        # [lookup_options.id] category=religion
    # Pro-only (backend ignores if not pro)
    filter_purpose:         list[int] | None = None        # [relationship_types.id]
    filter_looking_for:     list[int] | None = None        # [lookup_options.id]
    filter_education_level: list[int] | None = None        # [lookup_options.id]
    filter_family_plans:    list[int] | None = None        # [lookup_options.id]
    filter_have_kids:       list[int] | None = None        # [lookup_options.id]
    filter_ethnicities:     list[int] | None = None        # [lookup_options.id] category=ethnicity
    filter_exercise:        list[int] | None = None        # [lookup_options.id] category=exercise
    filter_drinking:        list[int] | None = None        # [lookup_options.id] category=drinking
    filter_smoking:         list[int] | None = None        # [lookup_options.id] category=smoking
    filter_height_min:      int | None = Field(None, ge=130, le=220)  # cm
    filter_height_max:      int | None = Field(None, ge=130, le=220)  # cm
    # Halal-specific filters
    filter_sect:               list[int] | None = None     # [lookup_options.id] category=sect
    filter_prayer_frequency:   list[int] | None = None     # [lookup_options.id] category=prayer_frequency
    filter_marriage_timeline:  list[int] | None = None     # [lookup_options.id] category=marriage_timeline
    filter_wali_verified_only: bool | None = None
    filter_wants_to_work:      bool | None = None          # True=must work, False=must not, None=no pref


class FilterUpdateRequest(BaseModel):
    """
    Sent by the filter sheet when the user taps "Apply Filters".
    Only discover filter preferences — nothing else.
    """
    filter_age_min:         int | None = Field(None, ge=18, le=80)
    filter_age_max:         int | None = Field(None, ge=18, le=80)
    filter_max_distance_km: int | None = Field(None, ge=1, le=80)
    filter_verified_only:   bool | None = None
    filter_star_signs:      list[int] | None = None
    filter_interests:       list[int] | None = None
    filter_languages:       list[int] | None = None
    filter_religions:       list[int] | None = None        # basic, free filter
    # Pro-only (backend silently ignores if user is not pro)
    filter_purpose:         list[int] | None = None
    filter_looking_for:     list[int] | None = None
    filter_education_level: list[int] | None = None
    filter_family_plans:    list[int] | None = None
    filter_have_kids:       list[int] | None = None
    filter_ethnicities:     list[int] | None = None
    filter_exercise:        list[int] | None = None
    filter_drinking:        list[int] | None = None
    filter_smoking:         list[int] | None = None
    filter_height_min:      int | None = Field(None, ge=130, le=220)
    filter_height_max:      int | None = Field(None, ge=130, le=220)
    # Halal-specific filters
    filter_sect:               list[int] | None = None
    filter_prayer_frequency:   list[int] | None = None
    filter_marriage_timeline:  list[int] | None = None
    filter_wali_verified_only: bool | None = None
    filter_wants_to_work:      bool | None = None
    # Work-mode filters (single JSONB blob)
    work_filter_settings:      dict | None = None


class MeResponse(BaseModel):
    id: uuid.UUID
    phone: str | None
    email: str | None
    apple_id: str | None
    full_name: str | None
    date_of_birth: date | None
    gender_id: int | None
    bio: str | None

    # Multi-value ID arrays
    purpose: list[int] | None
    interests: list[int] | None
    lifestyle: dict[str, Any] | None
    values_list: list[int] | None
    languages: list[int] | None
    causes: list[int] | None

    height_cm: int | None
    prompts: list[dict[str, Any]] | None
    photos: list[str] | None

    # Single-value ID fields
    education_level_id: int | None
    looking_for_id: int | None
    family_plans_id: int | None
    have_kids_id: int | None
    star_sign_id: int | None
    religion_id: int | None
    ethnicity_id: int | None

    # Halal profile fields
    sect_id:              int | None
    prayer_frequency_id:  int | None
    marriage_timeline_id: int | None
    wali_email:           str | None
    wali_verified:        bool
    blur_photos_halal:    bool
    halal_mode_enabled:   bool
    work_mode_enabled:    bool

    voice_prompts: list[dict[str, Any]] | None
    work_experience: list[dict[str, Any]] | None
    education: list[dict[str, Any]] | None
    city: str | None
    hometown: str | None
    address: str | None
    country: str | None
    dark_mode: bool
    best_photo_enabled: bool
    mood_emoji: str | None
    mood_text: str | None

    # ── Zod Work profile ──────────────────────────────────────────────────────
    work_photos: list[str] | None
    work_prompts: list[dict[str, Any]] | None
    work_matching_goals: list[int] | None
    work_are_you_hiring: bool | None
    work_commitment_level_id: int | None
    work_skills: list[int] | None
    work_equity_split_id: int | None
    work_industries: list[int] | None
    work_scheduling_url: str | None
    work_who_to_show_id: int | None
    work_priority_startup: bool | None
    work_headline: str | None
    work_persona: str | None
    work_num_founders_id: int | None
    work_primary_role_id: int | None
    work_years_experience_id: int | None
    work_job_search_status_id: int | None

    # ── Discover filter preferences ───────────────────────────────────────────
    filter_age_min:         int | None
    filter_age_max:         int | None
    filter_max_distance_km: int | None
    filter_verified_only:   bool
    filter_star_signs:      list[int] | None
    filter_interests:       list[int] | None
    filter_languages:       list[int] | None
    filter_religions:       list[int] | None
    filter_purpose:         list[int] | None
    filter_looking_for:     list[int] | None
    filter_education_level: list[int] | None
    filter_family_plans:    list[int] | None
    filter_have_kids:       list[int] | None
    filter_ethnicities:     list[int] | None
    filter_exercise:        list[int] | None
    filter_drinking:        list[int] | None
    filter_smoking:         list[int] | None
    filter_height_min:      int | None
    filter_height_max:      int | None
    # Halal filters
    filter_sect:               list[int] | None
    filter_prayer_frequency:   list[int] | None
    filter_marriage_timeline:  list[int] | None
    filter_wali_verified_only: bool
    filter_wants_to_work:      bool | None
    # Work-mode filters
    work_filter_settings:      dict | None

    university:               str | None
    university_email:         str | None
    university_email_verified: bool

    linkedin_url:      str | None
    linkedin_verified: bool

    # ── Notification preferences ──────────────────────────────────────────────
    notif_new_match:     bool
    notif_new_message:   bool
    notif_super_like:    bool
    notif_liked_profile: bool
    notif_profile_views: bool
    notif_ai_picks:      bool
    notif_promotions:    bool
    notif_dating_tips:   bool

    hide_age: bool
    hide_distance: bool
    require_verified_to_chat: bool

    is_incognito: bool
    travel_mode_enabled: bool
    auto_zod_enabled: bool
    travel_city: str | None
    travel_country: str | None
    travel_expires_at: datetime | None

    face_match_score: float | None
    verification_status: str   # unverified | pending | verified | rejected
    face_scan_required: bool
    id_scan_required:   bool = False
    subscription_tier: str     # free | pro
    linkedin_import_count: int = 0
    linkedin_import_reset_at: datetime | None = None
    super_likes_remaining: int
    daily_revert_used: int = 0
    is_active: bool
    is_verified: bool
    is_onboarded: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
