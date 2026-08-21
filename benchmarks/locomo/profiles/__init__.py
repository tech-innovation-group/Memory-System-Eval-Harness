"""LoCoMo evaluation profiles."""

from .common import AGENT_PLUGIN, default_vikingbot_workspace
from .schema import ProfileSettings, ProfileSpec
from .vikingboat0411 import (
    VIKINGBOAT_0411_PROFILE,
    VIKINGBOAT_0411_REFERENCE,
    VIKINGBOAT_0411_SETTINGS,
    VIKINGBOAT_0411_SOURCE,
)
from .vikingboat0411_natural_no_tools import (
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_REFERENCE,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_SETTINGS,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_SOURCE,
)


PROFILE_SETTINGS = {
    VIKINGBOAT_0411_PROFILE: VIKINGBOAT_0411_SETTINGS,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE: (
        VIKINGBOAT_0411_NATURAL_NO_TOOLS_SETTINGS
    ),
}
PROFILE_SOURCES = {
    VIKINGBOAT_0411_PROFILE: VIKINGBOAT_0411_SOURCE,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE: (
        VIKINGBOAT_0411_NATURAL_NO_TOOLS_SOURCE
    ),
}
PROFILE_REFERENCES = {
    VIKINGBOAT_0411_PROFILE: VIKINGBOAT_0411_REFERENCE,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE: (
        VIKINGBOAT_0411_NATURAL_NO_TOOLS_REFERENCE
    ),
}

PROFILE_SPECS = {
    name: ProfileSpec(
        name=name,
        reference=PROFILE_REFERENCES[name],
        source=PROFILE_SOURCES[name],
        settings=ProfileSettings.from_mapping(settings),
    )
    for name, settings in PROFILE_SETTINGS.items()
}


def profile_settings(profile: str):
    try:
        return PROFILE_SPECS[profile].settings.as_dict()
    except KeyError as exc:
        raise ValueError(f"unknown LoCoMo QA profile: {profile}") from exc


def profile_spec(profile: str) -> ProfileSpec:
    try:
        return PROFILE_SPECS[profile]
    except KeyError as exc:
        raise ValueError(f"unknown LoCoMo QA profile: {profile}") from exc


def profile_source(profile: str):
    return PROFILE_SOURCES.get(profile, {})


def profile_reference(profile: str) -> str:
    return PROFILE_REFERENCES.get(profile, "")

__all__ = [
    "AGENT_PLUGIN",
    "PROFILE_REFERENCES",
    "PROFILE_SPECS",
    "PROFILE_SETTINGS",
    "PROFILE_SOURCES",
    "ProfileSettings",
    "ProfileSpec",
    "VIKINGBOAT_0411_PROFILE",
    "VIKINGBOAT_0411_REFERENCE",
    "VIKINGBOAT_0411_SETTINGS",
    "VIKINGBOAT_0411_SOURCE",
    "VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE",
    "VIKINGBOAT_0411_NATURAL_NO_TOOLS_REFERENCE",
    "VIKINGBOAT_0411_NATURAL_NO_TOOLS_SETTINGS",
    "VIKINGBOAT_0411_NATURAL_NO_TOOLS_SOURCE",
    "default_vikingbot_workspace",
    "profile_reference",
    "profile_spec",
    "profile_settings",
    "profile_source",
]
