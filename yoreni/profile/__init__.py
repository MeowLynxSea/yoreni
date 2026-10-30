"""Persistent user profile and conversation style.

The profile is structured data the user supplied during onboarding. It is
separate from ``USER.md``: core memory is maintained by the memory system,
while this profile is edited directly by the owner and injected as-is.
"""

from yoreni.profile.schema import (
    CHAT_STYLE_BASES,
    STYLE_TUNING_VALUES,
    ChatStyle,
    OnboardingStatus,
    UserProfile,
)
from yoreni.profile.store import (
    ONBOARDING_FILENAME,
    PROFILE_FILENAME,
    OnboardingRecord,
    OnboardingStore,
    UserProfileStore,
    profile_store_for_config,
)

__all__ = [
    "CHAT_STYLE_BASES",
    "STYLE_TUNING_VALUES",
    "ChatStyle",
    "OnboardingRecord",
    "OnboardingStatus",
    "OnboardingStore",
    "PROFILE_FILENAME",
    "ONBOARDING_FILENAME",
    "UserProfile",
    "UserProfileStore",
    "profile_store_for_config",
]
