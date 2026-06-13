"""Helpers for inspecting the collection status of asynchronously loaded site
metadata (favicon, preview image, web archive snapshot).

The functions here are pure and query-free: they only read fields that are
already loaded on the bookmark plus the user's profile flags, so they can be
used safely while rendering bookmark lists without introducing N+1 queries.
"""

from django.db.models import Q

from bookmarks.models import Bookmark, UserProfile

# Display states for a single metadata type
STATE_DISABLED = "disabled"  # feature is turned off for the user
STATE_PENDING = "pending"  # collection is queued or in progress
STATE_COMPLETE = "complete"  # collected successfully, a value is present
STATE_NONE = "none"  # collection ran successfully but nothing was available
STATE_MISSING = "missing"  # never collected / no value yet
STATE_FAILURE = "failure"  # the last collection attempt failed

# Metadata type keys
TYPE_FAVICON = "favicon"
TYPE_PREVIEW_IMAGE = "preview_image"
TYPE_WEB_ARCHIVE = "web_archive"

# States the user may want to act on (i.e. retry)
ATTENTION_STATES = {STATE_MISSING, STATE_FAILURE}

# Filter names supported by the metadata maintenance center
FILTER_ALL = "all"
FILTER_ATTENTION = "attention"
FILTER_FAILED = "failed"
FILTER_PENDING = "pending"
FILTER_CHOICES = [FILTER_ALL, FILTER_ATTENTION, FILTER_FAILED, FILTER_PENDING]


class MetadataItemState:
    def __init__(self, type_key: str, label: str, state: str):
        self.type = type_key
        self.label = label
        self.state = state

    @property
    def needs_attention(self) -> bool:
        return self.state in ATTENTION_STATES

    @property
    def is_failure(self) -> bool:
        return self.state == STATE_FAILURE

    @property
    def is_pending(self) -> bool:
        return self.state == STATE_PENDING

    @property
    def is_disabled(self) -> bool:
        return self.state == STATE_DISABLED


class MetadataStates:
    def __init__(
        self,
        favicon: MetadataItemState,
        preview_image: MetadataItemState,
        web_archive: MetadataItemState,
    ):
        self.favicon = favicon
        self.preview_image = preview_image
        self.web_archive = web_archive

    @property
    def items(self) -> list[MetadataItemState]:
        return [self.favicon, self.preview_image, self.web_archive]

    @property
    def active_items(self) -> list[MetadataItemState]:
        # Items whose feature is enabled for the user
        return [item for item in self.items if not item.is_disabled]

    @property
    def attention_items(self) -> list[MetadataItemState]:
        # Items worth surfacing inline: a problem (missing/failure) or in progress
        return [
            item
            for item in self.items
            if item.needs_attention or item.is_pending
        ]

    @property
    def needs_attention(self) -> bool:
        return any(item.needs_attention for item in self.items)

    @property
    def has_failure(self) -> bool:
        return any(item.is_failure for item in self.items)

    @property
    def has_pending(self) -> bool:
        return any(item.is_pending for item in self.items)


def _web_archive_enabled(profile: UserProfile) -> bool:
    return (
        profile.web_archive_integration
        == UserProfile.WEB_ARCHIVE_INTEGRATION_ENABLED
    )


def any_metadata_feature_enabled(profile: UserProfile) -> bool:
    return (
        profile.enable_favicons
        or profile.enable_preview_images
        or _web_archive_enabled(profile)
    )


def _favicon_state(bookmark: Bookmark, profile: UserProfile) -> str:
    if not profile.enable_favicons:
        return STATE_DISABLED
    if bookmark.favicon_file:
        return STATE_COMPLETE
    if bookmark.favicon_status == Bookmark.METADATA_STATUS_FAILURE:
        return STATE_FAILURE
    if bookmark.favicon_status == Bookmark.METADATA_STATUS_PENDING:
        return STATE_PENDING
    return STATE_MISSING


def _preview_image_state(bookmark: Bookmark, profile: UserProfile) -> str:
    if not profile.enable_preview_images:
        return STATE_DISABLED
    if bookmark.preview_image_file:
        return STATE_COMPLETE
    if bookmark.preview_image_status == Bookmark.METADATA_STATUS_FAILURE:
        return STATE_FAILURE
    if bookmark.preview_image_status == Bookmark.METADATA_STATUS_PENDING:
        return STATE_PENDING
    if bookmark.preview_image_status == Bookmark.METADATA_STATUS_COMPLETE:
        # Collection ran, but the website does not offer a preview image
        return STATE_NONE
    return STATE_MISSING


def _web_archive_state(bookmark: Bookmark, profile: UserProfile) -> str:
    if not _web_archive_enabled(profile):
        return STATE_DISABLED
    if bookmark.web_archive_snapshot_url:
        return STATE_COMPLETE
    if bookmark.web_archive_status == Bookmark.METADATA_STATUS_FAILURE:
        return STATE_FAILURE
    if bookmark.web_archive_status == Bookmark.METADATA_STATUS_PENDING:
        return STATE_PENDING
    return STATE_MISSING


def get_metadata_states(bookmark: Bookmark, profile: UserProfile) -> MetadataStates:
    return MetadataStates(
        favicon=MetadataItemState(
            TYPE_FAVICON, "Favicon", _favicon_state(bookmark, profile)
        ),
        preview_image=MetadataItemState(
            TYPE_PREVIEW_IMAGE,
            "Preview image",
            _preview_image_state(bookmark, profile),
        ),
        web_archive=MetadataItemState(
            TYPE_WEB_ARCHIVE, "Web archive", _web_archive_state(bookmark, profile)
        ),
    )


def _combine_or(conditions: list[Q]) -> Q:
    if not conditions:
        # Match nothing when no metadata feature is enabled
        return Q(pk__in=[])
    combined = conditions[0]
    for condition in conditions[1:]:
        combined |= condition
    return combined


def _attention_conditions(profile: UserProfile) -> list[Q]:
    conditions = []
    # favicon / web archive: an empty value that is not pending needs attention
    if profile.enable_favicons:
        conditions.append(
            Q(favicon_file="")
            & ~Q(favicon_status=Bookmark.METADATA_STATUS_PENDING)
        )
    if profile.enable_preview_images:
        # A successful run with no image available (status complete) is fine,
        # so only an empty/failed status counts as needing attention
        conditions.append(
            Q(preview_image_file="")
            & Q(
                preview_image_status__in=[
                    "",
                    Bookmark.METADATA_STATUS_FAILURE,
                ]
            )
        )
    if _web_archive_enabled(profile):
        conditions.append(
            Q(web_archive_snapshot_url="")
            & ~Q(web_archive_status=Bookmark.METADATA_STATUS_PENDING)
        )
    return conditions


def _status_conditions(profile: UserProfile, status: str) -> list[Q]:
    conditions = []
    if profile.enable_favicons:
        conditions.append(Q(favicon_status=status))
    if profile.enable_preview_images:
        conditions.append(Q(preview_image_status=status))
    if _web_archive_enabled(profile):
        conditions.append(Q(web_archive_status=status))
    return conditions


def get_status_filter(filter_name: str, profile: UserProfile) -> Q | None:
    """Return a queryset filter for the maintenance center, or None for "all"."""
    if filter_name == FILTER_ATTENTION:
        return _combine_or(_attention_conditions(profile))
    if filter_name == FILTER_FAILED:
        return _combine_or(
            _status_conditions(profile, Bookmark.METADATA_STATUS_FAILURE)
        )
    if filter_name == FILTER_PENDING:
        return _combine_or(
            _status_conditions(profile, Bookmark.METADATA_STATUS_PENDING)
        )
    return None
