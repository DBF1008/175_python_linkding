from django.test import TestCase

from bookmarks import metadata
from bookmarks.models import Bookmark, UserProfile
from bookmarks.tests.helpers import BookmarkFactoryMixin


class MetadataStatesTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.profile = self.user.profile
        # Enable all three metadata features by default
        self.profile.enable_favicons = True
        self.profile.enable_preview_images = True
        self.profile.web_archive_integration = (
            UserProfile.WEB_ARCHIVE_INTEGRATION_ENABLED
        )
        self.profile.save()

    def states(self, **kwargs):
        bookmark = self.setup_bookmark(**kwargs)
        return metadata.get_metadata_states(bookmark, self.profile)

    # Favicon
    def test_favicon_complete_when_file_present(self):
        states = self.states(favicon_file="icon.png")
        self.assertEqual(states.favicon.state, metadata.STATE_COMPLETE)

    def test_favicon_missing_when_no_file_and_no_status(self):
        states = self.states()
        self.assertEqual(states.favicon.state, metadata.STATE_MISSING)
        self.assertTrue(states.favicon.needs_attention)
        self.assertTrue(states.needs_attention)

    def test_favicon_failure(self):
        states = self.states(favicon_status=Bookmark.METADATA_STATUS_FAILURE)
        self.assertEqual(states.favicon.state, metadata.STATE_FAILURE)
        self.assertTrue(states.has_failure)

    def test_favicon_pending(self):
        states = self.states(favicon_status=Bookmark.METADATA_STATUS_PENDING)
        self.assertEqual(states.favicon.state, metadata.STATE_PENDING)
        self.assertFalse(states.favicon.needs_attention)
        self.assertTrue(states.has_pending)

    def test_favicon_disabled_when_feature_off(self):
        self.profile.enable_favicons = False
        self.profile.save()
        states = self.states()
        self.assertEqual(states.favicon.state, metadata.STATE_DISABLED)
        # A disabled feature is never flagged for attention
        self.assertFalse(states.favicon.needs_attention)

    # Preview image
    def test_preview_complete_when_file_present(self):
        states = self.states(preview_image_file="p.png")
        self.assertEqual(states.preview_image.state, metadata.STATE_COMPLETE)

    def test_preview_none_when_collected_without_image(self):
        states = self.states(preview_image_status=Bookmark.METADATA_STATUS_COMPLETE)
        self.assertEqual(states.preview_image.state, metadata.STATE_NONE)
        self.assertFalse(states.preview_image.needs_attention)

    def test_preview_missing_when_no_file_and_no_status(self):
        states = self.states()
        self.assertEqual(states.preview_image.state, metadata.STATE_MISSING)

    def test_preview_failure(self):
        states = self.states(preview_image_status=Bookmark.METADATA_STATUS_FAILURE)
        self.assertEqual(states.preview_image.state, metadata.STATE_FAILURE)

    def test_preview_disabled(self):
        self.profile.enable_preview_images = False
        self.profile.save()
        states = self.states()
        self.assertEqual(states.preview_image.state, metadata.STATE_DISABLED)

    # Web archive
    def test_web_archive_complete_when_url_present(self):
        states = self.states(web_archive_snapshot_url="https://web.archive.org/x")
        self.assertEqual(states.web_archive.state, metadata.STATE_COMPLETE)

    def test_web_archive_missing(self):
        states = self.states()
        self.assertEqual(states.web_archive.state, metadata.STATE_MISSING)

    def test_web_archive_failure(self):
        states = self.states(web_archive_status=Bookmark.METADATA_STATUS_FAILURE)
        self.assertEqual(states.web_archive.state, metadata.STATE_FAILURE)

    def test_web_archive_disabled(self):
        self.profile.web_archive_integration = (
            UserProfile.WEB_ARCHIVE_INTEGRATION_DISABLED
        )
        self.profile.save()
        states = self.states()
        self.assertEqual(states.web_archive.state, metadata.STATE_DISABLED)

    # Aggregations
    def test_active_items_excludes_disabled_features(self):
        self.profile.enable_favicons = False
        self.profile.save()
        states = self.states()
        types = [item.type for item in states.active_items]
        self.assertNotIn(metadata.TYPE_FAVICON, types)
        self.assertIn(metadata.TYPE_PREVIEW_IMAGE, types)
        self.assertIn(metadata.TYPE_WEB_ARCHIVE, types)

    def test_attention_items_only_problems_and_pending(self):
        states = self.states(
            favicon_file="icon.png",  # complete -> excluded
            preview_image_status=Bookmark.METADATA_STATUS_FAILURE,  # failure
            web_archive_status=Bookmark.METADATA_STATUS_PENDING,  # pending
        )
        types = [item.type for item in states.attention_items]
        self.assertNotIn(metadata.TYPE_FAVICON, types)
        self.assertIn(metadata.TYPE_PREVIEW_IMAGE, types)
        self.assertIn(metadata.TYPE_WEB_ARCHIVE, types)


class MetadataStatusFilterTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.profile = self.user.profile
        self.profile.enable_favicons = True
        self.profile.enable_preview_images = True
        self.profile.web_archive_integration = (
            UserProfile.WEB_ARCHIVE_INTEGRATION_ENABLED
        )
        self.profile.save()

    def filtered(self, filter_name):
        status_q = metadata.get_status_filter(filter_name, self.profile)
        query_set = Bookmark.objects.filter(owner=self.user)
        return list(query_set if status_q is None else query_set.filter(status_q))

    def test_all_returns_no_filter(self):
        self.assertIsNone(
            metadata.get_status_filter(metadata.FILTER_ALL, self.profile)
        )

    def test_failed_filter(self):
        failed = self.setup_bookmark(
            favicon_status=Bookmark.METADATA_STATUS_FAILURE
        )
        ok = self.setup_bookmark(favicon_file="icon.png")
        result = self.filtered(metadata.FILTER_FAILED)
        self.assertIn(failed, result)
        self.assertNotIn(ok, result)

    def test_pending_filter(self):
        pending = self.setup_bookmark(
            preview_image_status=Bookmark.METADATA_STATUS_PENDING
        )
        ok = self.setup_bookmark(preview_image_file="p.png")
        result = self.filtered(metadata.FILTER_PENDING)
        self.assertIn(pending, result)
        self.assertNotIn(ok, result)

    def test_attention_filter(self):
        complete_bm = self.setup_bookmark(
            favicon_file="icon.png",
            preview_image_file="p.png",
            web_archive_snapshot_url="https://web.archive.org/x",
        )
        pending_bm = self.setup_bookmark(
            favicon_status=Bookmark.METADATA_STATUS_PENDING,
            preview_image_status=Bookmark.METADATA_STATUS_PENDING,
            web_archive_status=Bookmark.METADATA_STATUS_PENDING,
        )
        preview_none_bm = self.setup_bookmark(
            favicon_file="icon.png",
            preview_image_status=Bookmark.METADATA_STATUS_COMPLETE,
            web_archive_snapshot_url="https://web.archive.org/x",
        )
        missing_bm = self.setup_bookmark()
        failed_bm = self.setup_bookmark(
            favicon_status=Bookmark.METADATA_STATUS_FAILURE,
            preview_image_file="p.png",
            web_archive_snapshot_url="https://web.archive.org/x",
        )

        result = self.filtered(metadata.FILTER_ATTENTION)

        self.assertIn(missing_bm, result)
        self.assertIn(failed_bm, result)
        self.assertNotIn(complete_bm, result)
        self.assertNotIn(pending_bm, result)
        self.assertNotIn(preview_none_bm, result)

    def test_filters_match_nothing_when_no_feature_enabled(self):
        self.profile.enable_favicons = False
        self.profile.enable_preview_images = False
        self.profile.web_archive_integration = (
            UserProfile.WEB_ARCHIVE_INTEGRATION_DISABLED
        )
        self.profile.save()
        self.setup_bookmark(favicon_status=Bookmark.METADATA_STATUS_FAILURE)

        self.assertEqual(self.filtered(metadata.FILTER_FAILED), [])
        self.assertEqual(self.filtered(metadata.FILTER_ATTENTION), [])
        self.assertEqual(self.filtered(metadata.FILTER_PENDING), [])
