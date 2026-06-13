from unittest import mock

import waybackpy
from django.test import TestCase
from huey.contrib.djhuey import HUEY as huey
from waybackpy.exceptions import WaybackError

from bookmarks import metadata
from bookmarks.models import Bookmark, UserProfile
from bookmarks.services import tasks
from bookmarks.tests.helpers import BookmarkFactoryMixin, disable_logging


class MetadataTasksStatusTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        huey.immediate = True
        huey.results = True
        huey.store_none = True

        self.mock_save_api = mock.Mock(
            archive_url="https://web.archive.org/snapshot"
        )
        self.mock_save_api_patcher = mock.patch.object(
            waybackpy, "WaybackMachineSaveAPI", return_value=self.mock_save_api
        )
        self.mock_save_api_patcher.start()

        self.mock_load_favicon_patcher = mock.patch(
            "bookmarks.services.favicon_loader.load_favicon"
        )
        self.mock_load_favicon = self.mock_load_favicon_patcher.start()
        self.mock_load_favicon.return_value = "favicon.png"

        self.mock_load_preview_image_patcher = mock.patch(
            "bookmarks.services.preview_image_loader.load_preview_image"
        )
        self.mock_load_preview_image = self.mock_load_preview_image_patcher.start()
        self.mock_load_preview_image.return_value = "preview.png"

        self.user = self.get_or_create_test_user()
        self.user.profile.web_archive_integration = (
            UserProfile.WEB_ARCHIVE_INTEGRATION_ENABLED
        )
        self.user.profile.enable_favicons = True
        self.user.profile.enable_preview_images = True
        self.user.profile.save()

    def tearDown(self):
        self.mock_save_api_patcher.stop()
        self.mock_load_favicon_patcher.stop()
        self.mock_load_preview_image_patcher.stop()
        huey.storage.flush_results()
        huey.immediate = False

    # Favicon
    def test_favicon_success_sets_complete(self):
        bookmark = self.setup_bookmark()
        tasks.load_favicon(self.user, bookmark)
        bookmark.refresh_from_db()
        self.assertEqual(bookmark.favicon_file, "favicon.png")
        self.assertEqual(
            bookmark.favicon_status, Bookmark.METADATA_STATUS_COMPLETE
        )

    @disable_logging
    def test_favicon_failure_sets_failure(self):
        bookmark = self.setup_bookmark()
        self.mock_load_favicon.side_effect = Exception("boom")
        try:
            tasks.load_favicon(self.user, bookmark)
        except Exception:
            pass
        bookmark.refresh_from_db()
        self.assertEqual(bookmark.favicon_file, "")
        self.assertEqual(
            bookmark.favicon_status, Bookmark.METADATA_STATUS_FAILURE
        )

    # Preview image
    def test_preview_success_sets_complete(self):
        bookmark = self.setup_bookmark()
        tasks.load_preview_image(self.user, bookmark)
        bookmark.refresh_from_db()
        self.assertEqual(bookmark.preview_image_file, "preview.png")
        self.assertEqual(
            bookmark.preview_image_status, Bookmark.METADATA_STATUS_COMPLETE
        )

    def test_preview_none_sets_complete_with_empty_file(self):
        bookmark = self.setup_bookmark()
        self.mock_load_preview_image.return_value = None
        tasks.load_preview_image(self.user, bookmark)
        bookmark.refresh_from_db()
        self.assertEqual(bookmark.preview_image_file, "")
        self.assertEqual(
            bookmark.preview_image_status, Bookmark.METADATA_STATUS_COMPLETE
        )

    @disable_logging
    def test_preview_failure_sets_failure(self):
        bookmark = self.setup_bookmark()
        self.mock_load_preview_image.side_effect = Exception("boom")
        try:
            tasks.load_preview_image(self.user, bookmark)
        except Exception:
            pass
        bookmark.refresh_from_db()
        self.assertEqual(
            bookmark.preview_image_status, Bookmark.METADATA_STATUS_FAILURE
        )

    # Web archive snapshot
    def test_web_archive_success_sets_complete(self):
        bookmark = self.setup_bookmark()
        tasks.create_web_archive_snapshot(self.user, bookmark, False)
        bookmark.refresh_from_db()
        self.assertEqual(
            bookmark.web_archive_snapshot_url, "https://web.archive.org/snapshot"
        )
        self.assertEqual(
            bookmark.web_archive_status, Bookmark.METADATA_STATUS_COMPLETE
        )

    @disable_logging
    def test_web_archive_failure_sets_failure(self):
        bookmark = self.setup_bookmark()
        self.mock_save_api.save.side_effect = WaybackError
        tasks.create_web_archive_snapshot(self.user, bookmark, False)
        bookmark.refresh_from_db()
        self.assertEqual(
            bookmark.web_archive_status, Bookmark.METADATA_STATUS_FAILURE
        )

    # Scheduling marks bookmarks as pending
    def test_schedule_without_favicons_marks_pending(self):
        bookmark = self.setup_bookmark()
        # Prevent the immediate task from flipping the status back to complete
        with mock.patch("bookmarks.services.tasks._load_favicon_task"):
            tasks.schedule_bookmarks_without_favicons(self.user)
        bookmark.refresh_from_db()
        self.assertEqual(
            bookmark.favicon_status, Bookmark.METADATA_STATUS_PENDING
        )

    def test_schedule_without_previews_marks_pending(self):
        bookmark = self.setup_bookmark()
        with mock.patch("bookmarks.services.tasks._load_preview_image_task"):
            tasks.schedule_bookmarks_without_previews(self.user)
        bookmark.refresh_from_db()
        self.assertEqual(
            bookmark.preview_image_status, Bookmark.METADATA_STATUS_PENDING
        )


class PreviewReEnableTestCase(TestCase, BookmarkFactoryMixin):
    """Regression: previews disabled then re-enabled should (re)collect previews
    for bookmarks that lack one and move their status from disabled to complete."""

    def setUp(self):
        huey.immediate = True
        huey.results = True
        huey.store_none = True

        self.mock_load_preview_image_patcher = mock.patch(
            "bookmarks.services.preview_image_loader.load_preview_image"
        )
        self.mock_load_preview_image = self.mock_load_preview_image_patcher.start()
        self.mock_load_preview_image.return_value = "preview.png"

        self.user = self.get_or_create_test_user()

    def tearDown(self):
        self.mock_load_preview_image_patcher.stop()
        huey.storage.flush_results()
        huey.immediate = False

    def test_reenabling_previews_collects_missing_previews(self):
        # Previews disabled, bookmark has no preview
        self.user.profile.enable_preview_images = False
        self.user.profile.save()
        bookmark = self.setup_bookmark()

        # While disabled, the metadata center reports the preview as disabled
        states = metadata.get_metadata_states(bookmark, self.user.profile)
        self.assertEqual(states.preview_image.state, metadata.STATE_DISABLED)

        # Re-enable previews and run the scheduler update_profile triggers
        self.user.profile.enable_preview_images = True
        self.user.profile.save()
        tasks.schedule_bookmarks_without_previews(self.user)

        bookmark.refresh_from_db()
        self.mock_load_preview_image.assert_called_once()
        self.assertEqual(bookmark.preview_image_file, "preview.png")
        self.assertEqual(
            bookmark.preview_image_status, Bookmark.METADATA_STATUS_COMPLETE
        )
        states = metadata.get_metadata_states(bookmark, self.user.profile)
        self.assertEqual(states.preview_image.state, metadata.STATE_COMPLETE)
