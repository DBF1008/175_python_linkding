from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from huey.contrib.djhuey import HUEY as huey

from bookmarks.models import Bookmark, UserProfile
from bookmarks.services import tasks
from bookmarks.services import bookmarks as bookmarks_service
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin


class MetadataStatusTaskTestCase(TestCase, BookmarkFactoryMixin):
    """Tests for metadata status persistence in background tasks."""

    def setUp(self):
        huey.immediate = True
        huey.results = True
        huey.store_none = True

        self.mock_load_favicon_patcher = mock.patch(
            "bookmarks.services.favicon_loader.load_favicon"
        )
        self.mock_load_favicon = self.mock_load_favicon_patcher.start()
        self.mock_load_favicon.return_value = "https_example_com.png"

        self.mock_load_preview_image_patcher = mock.patch(
            "bookmarks.services.preview_image_loader.load_preview_image"
        )
        self.mock_load_preview_image = self.mock_load_preview_image_patcher.start()
        self.mock_load_preview_image.return_value = "preview_image.png"

        self.user = self.get_or_create_test_user()
        self.user.profile.enable_favicons = True
        self.user.profile.enable_preview_images = True
        self.user.profile.save()

    def tearDown(self):
        self.mock_load_favicon_patcher.stop()
        self.mock_load_preview_image_patcher.stop()
        huey.storage.flush_results()
        huey.immediate = False

    # --- Favicon status lifecycle ---

    def test_favicon_success_sets_complete(self):
        bookmark = self.setup_bookmark()
        tasks.load_favicon(self.user, bookmark)
        bookmark.refresh_from_db()

        self.assertEqual(bookmark.favicon_status, "complete")
        self.assertEqual(bookmark.favicon_file, "https_example_com.png")

    def test_favicon_failure_after_retries_sets_failure(self):
        """When all retries are exhausted, status should be set to failure."""
        self.mock_load_favicon.side_effect = Exception("Download failed")
        bookmark = self.setup_bookmark()

        # Access the inner function via Huey's TaskWrapper.func to test
        # with a mock task context (bypasses the Huey queue).
        inner_fn = tasks._load_favicon_task.func
        mock_task_ctx = mock.Mock()
        mock_task_ctx.retries = 0  # Last retry exhausted

        with self.assertRaises(Exception):
            inner_fn(bookmark.id, task=mock_task_ctx)

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.favicon_status, "failure")

    def test_favicon_pending_during_retry(self):
        """When task fails but has retries remaining, status stays pending."""
        self.mock_load_favicon.side_effect = Exception("Download failed")
        bookmark = self.setup_bookmark()

        # Call through normal path — first attempt runs with immediate=True,
        # fails, and gets requeued. Status should stay pending since there
        # are retries remaining.
        tasks._load_favicon_task(bookmark.id)

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.favicon_status, "pending")

    def test_load_favicon_sets_pending_before_enqueue(self):
        """When load_favicon is called, status should be set to pending
        immediately, even before the task completes."""
        bookmark = self.setup_bookmark()
        self.assertEqual(bookmark.favicon_status, "")

        # The load_favicon function sets status to pending before enqueuing.
        # With immediate=True, the task runs synchronously and completes,
        # so we verify the end state is complete.
        tasks.load_favicon(self.user, bookmark)
        bookmark.refresh_from_db()

        self.assertEqual(bookmark.favicon_status, "complete")

    # --- Preview image status lifecycle ---

    def test_preview_image_success_sets_complete(self):
        bookmark = self.setup_bookmark()
        tasks.load_preview_image(self.user, bookmark)
        bookmark.refresh_from_db()

        self.assertEqual(bookmark.preview_image_status, "complete")
        self.assertEqual(bookmark.preview_image_file, "preview_image.png")

    def test_preview_image_failure_after_retries_sets_failure(self):
        """When all retries are exhausted, status should be set to failure."""
        self.mock_load_preview_image.side_effect = Exception("Download failed")
        bookmark = self.setup_bookmark()

        inner_fn = tasks._load_preview_image_task.func
        mock_task_ctx = mock.Mock()
        mock_task_ctx.retries = 0

        with self.assertRaises(Exception):
            inner_fn(bookmark.id, task=mock_task_ctx)

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.preview_image_status, "failure")

    def test_preview_image_pending_during_retry(self):
        """When task fails but has retries remaining, status stays pending."""
        self.mock_load_preview_image.side_effect = Exception("Download failed")
        bookmark = self.setup_bookmark()

        tasks._load_preview_image_task(bookmark.id)

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.preview_image_status, "pending")

    def test_load_preview_image_sets_pending_before_enqueue(self):
        bookmark = self.setup_bookmark()
        self.assertEqual(bookmark.preview_image_status, "")

        tasks.load_preview_image(self.user, bookmark)
        bookmark.refresh_from_db()

        self.assertEqual(bookmark.preview_image_status, "complete")


class MetadataStatusToggleTestCase(TestCase, BookmarkFactoryMixin):
    """Tests for favicon/preview toggle off → on scenarios."""

    def setUp(self):
        huey.immediate = True
        huey.results = True
        huey.store_none = True

        self.mock_load_favicon_patcher = mock.patch(
            "bookmarks.services.favicon_loader.load_favicon"
        )
        self.mock_load_favicon = self.mock_load_favicon_patcher.start()
        self.mock_load_favicon.return_value = "https_example_com.png"

        self.mock_load_preview_image_patcher = mock.patch(
            "bookmarks.services.preview_image_loader.load_preview_image"
        )
        self.mock_load_preview_image = self.mock_load_preview_image_patcher.start()
        self.mock_load_preview_image.return_value = "preview_image.png"

        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def tearDown(self):
        self.mock_load_favicon_patcher.stop()
        self.mock_load_preview_image_patcher.stop()
        huey.storage.flush_results()
        huey.immediate = False

    def create_profile_form_data(self, overrides=None):
        form_data = {
            "update_profile": "",
            "theme": UserProfile.THEME_AUTO,
            "bookmark_date_display": UserProfile.BOOKMARK_DATE_DISPLAY_RELATIVE,
            "bookmark_description_display": UserProfile.BOOKMARK_DESCRIPTION_DISPLAY_INLINE,
            "bookmark_description_max_lines": 1,
            "bookmark_link_target": UserProfile.BOOKMARK_LINK_TARGET_BLANK,
            "web_archive_integration": UserProfile.WEB_ARCHIVE_INTEGRATION_DISABLED,
            "enable_sharing": False,
            "enable_public_sharing": False,
            "enable_favicons": False,
            "enable_preview_images": False,
            "enable_automatic_html_snapshots": True,
            "tag_search": UserProfile.TAG_SEARCH_STRICT,
            "tag_grouping": UserProfile.TAG_GROUPING_ALPHABETICAL,
            "display_url": False,
            "display_view_bookmark_action": True,
            "display_edit_bookmark_action": True,
            "display_archive_bookmark_action": True,
            "display_remove_bookmark_action": True,
            "permanent_notes": False,
            "custom_css": "",
            "auto_tagging_rules": "",
            "items_per_page": "30",
            "sticky_pagination": False,
            "collapse_side_panel": False,
            "hide_bundles": False,
            "legacy_search": False,
        }
        return {**form_data, **(overrides or {})}

    def test_toggle_favicon_off_then_on(self):
        """Enabling favicons triggers backfill which sets pending status."""
        # Start with favicons enabled and a bookmark with no favicon
        self.user.profile.enable_favicons = True
        self.user.profile.save()
        bookmark = self.setup_bookmark()
        self.assertEqual(bookmark.favicon_status, "")

        # Disable favicons
        form_data = self.create_profile_form_data({"enable_favicons": False})
        self.client.post(reverse("linkding:settings.update"), form_data)
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.enable_favicons)

        # Re-enable favicons — should trigger backfill
        form_data = self.create_profile_form_data({"enable_favicons": True})
        with mock.patch.object(
            tasks, "schedule_bookmarks_without_favicons"
        ) as mock_schedule:
            self.client.post(reverse("linkding:settings.update"), form_data)
            mock_schedule.assert_called_once_with(self.user)

        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.enable_favicons)

    def test_toggle_preview_off_then_on(self):
        """Enabling preview images triggers backfill."""
        self.user.profile.enable_preview_images = True
        self.user.profile.save()
        bookmark = self.setup_bookmark()
        self.assertEqual(bookmark.preview_image_status, "")

        # Disable preview images
        form_data = self.create_profile_form_data({"enable_preview_images": False})
        self.client.post(reverse("linkding:settings.update"), form_data)
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.enable_preview_images)

        # Re-enable — should trigger backfill
        form_data = self.create_profile_form_data({"enable_preview_images": True})
        with mock.patch.object(
            tasks, "schedule_bookmarks_without_previews"
        ) as mock_schedule:
            self.client.post(reverse("linkding:settings.update"), form_data)
            mock_schedule.assert_called_once_with(self.user)


class MetadataStatusRetryTestCase(TestCase, BookmarkFactoryMixin):
    """Tests for individual and bulk retry functionality."""

    def setUp(self):
        huey.immediate = True
        huey.results = True
        huey.store_none = True

        self.mock_load_favicon_patcher = mock.patch(
            "bookmarks.services.favicon_loader.load_favicon"
        )
        self.mock_load_favicon = self.mock_load_favicon_patcher.start()
        self.mock_load_favicon.return_value = "https_example_com.png"

        self.mock_load_preview_image_patcher = mock.patch(
            "bookmarks.services.preview_image_loader.load_preview_image"
        )
        self.mock_load_preview_image = self.mock_load_preview_image_patcher.start()
        self.mock_load_preview_image.return_value = "preview_image.png"

        self.user = self.get_or_create_test_user()
        self.user.profile.enable_favicons = True
        self.user.profile.enable_preview_images = True
        self.user.profile.save()
        self.client.force_login(self.user)

    def tearDown(self):
        self.mock_load_favicon_patcher.stop()
        self.mock_load_preview_image_patcher.stop()
        huey.storage.flush_results()
        huey.immediate = False

    def test_individual_retry_enqueues_task(self):
        """Individual retry on a failed favicon should re-enqueue the task."""
        bookmark = self.setup_bookmark()
        bookmark.favicon_status = "failure"
        bookmark.save()

        tasks.load_favicon(self.user, bookmark)
        bookmark.refresh_from_db()

        self.assertEqual(bookmark.favicon_status, "complete")
        self.assertEqual(bookmark.favicon_file, "https_example_com.png")

    def test_bulk_retry_only_retries_failures(self):
        """Bulk retry should only retry bookmarks in failure/pending+empty state."""
        # Failed bookmark
        failed = self.setup_bookmark(url="https://example.com/failed")
        failed.favicon_status = "failure"
        failed.save()

        # Complete bookmark (should NOT be retried)
        complete = self.setup_bookmark(
            url="https://example.com/complete",
            favicon_file="https_example_com.png",
        )
        complete.favicon_status = "complete"
        complete.save()

        # Pending without file (should be retried)
        pending = self.setup_bookmark(url="https://example.com/pending")
        pending.favicon_status = "pending"
        pending.save()

        self.mock_load_favicon.reset_mock()

        bookmarks_service.retry_failed_favicons(
            [failed.id, complete.id, pending.id], self.user
        )

        # Should only be called for failed and pending bookmarks
        self.assertEqual(self.mock_load_favicon.call_count, 2)

    def test_bulk_retry_respects_feature_flag(self):
        """When favicon feature is disabled, bulk retry should be a no-op."""
        bookmark = self.setup_bookmark()
        bookmark.favicon_status = "failure"
        bookmark.save()

        self.user.profile.enable_favicons = False
        self.user.profile.save()

        self.mock_load_favicon.reset_mock()

        bookmarks_service.retry_failed_favicons([bookmark.id], self.user)

        # load_favicon checks is_favicon_feature_active internally
        self.mock_load_favicon.assert_not_called()

    def test_retry_all_failed_favicons(self):
        """retry_all_failed_favicons should retry all bookmarks with failure status."""
        failed1 = self.setup_bookmark(url="https://example.com/f1")
        failed1.favicon_status = "failure"
        failed1.save()

        failed2 = self.setup_bookmark(url="https://example.com/f2")
        failed2.favicon_status = "failure"
        failed2.save()

        complete = self.setup_bookmark(
            url="https://example.com/c", favicon_file="existing.png"
        )
        complete.favicon_status = "complete"
        complete.save()

        count = bookmarks_service.retry_all_failed_favicons(self.user)

        self.assertEqual(count, 2)

    def test_retry_all_failed_preview_images(self):
        """retry_all_failed_preview_images should retry all bookmarks with failure status."""
        failed = self.setup_bookmark(url="https://example.com/f1")
        failed.preview_image_status = "failure"
        failed.save()

        complete = self.setup_bookmark(
            url="https://example.com/c", preview_image_file="existing.png"
        )
        complete.preview_image_status = "complete"
        complete.save()

        count = bookmarks_service.retry_all_failed_preview_images(self.user)

        self.assertEqual(count, 1)


class MetadataStatusViewTestCase(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    """Tests for metadata status display in list and detail views."""

    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.user.profile.enable_favicons = True
        self.user.profile.enable_preview_images = True
        self.user.profile.save()
        self.client.force_login(self.user)

    def test_list_view_shows_failure_status_indicator(self):
        """List view should show failure indicator for bookmarks with failed favicon."""
        bookmark = self.setup_bookmark(
            title="Failed Bookmark",
            favicon_file="",
        )
        bookmark.favicon_status = "failure"
        bookmark.save()

        response = self.client.get(reverse("linkding:bookmarks.index"))
        html = response.content.decode()

        self.assertIn("✗ favicon", html)

    def test_list_view_shows_pending_status_indicator(self):
        """List view should show pending indicator for bookmarks with pending status."""
        bookmark = self.setup_bookmark(
            title="Pending Bookmark",
            preview_image_file="",
        )
        bookmark.preview_image_status = "pending"
        bookmark.save()

        response = self.client.get(reverse("linkding:bookmarks.index"))
        html = response.content.decode()

        self.assertIn("⏳ preview", html)

    def test_list_view_hides_status_when_complete(self):
        """List view should NOT show status indicator when all metadata is complete."""
        bookmark = self.setup_bookmark(
            title="Complete Bookmark",
            favicon_file="icon.png",
            preview_image_file="preview.png",
        )
        bookmark.favicon_status = "complete"
        bookmark.preview_image_status = "complete"
        bookmark.save()

        response = self.client.get(reverse("linkding:bookmarks.index"))
        html = response.content.decode()

        self.assertNotIn("metadata-status", html)

    def test_detail_view_shows_retry_button_on_failure(self):
        """Detail view should show retry button when favicon has failed."""
        bookmark = self.setup_bookmark()
        bookmark.favicon_status = "failure"
        bookmark.save()

        response = self.client.get(
            reverse("linkding:bookmarks.index") + f"?details={bookmark.id}"
        )
        html = response.content.decode()

        self.assertIn('name="retry_favicon"', html)
        self.assertIn("✗ Failed", html)

    def test_detail_view_hides_retry_button_on_complete(self):
        """Detail view should NOT show retry button when metadata is complete."""
        bookmark = self.setup_bookmark(
            favicon_file="icon.png",
            preview_image_file="preview.png",
        )
        bookmark.favicon_status = "complete"
        bookmark.preview_image_status = "complete"
        bookmark.save()

        response = self.client.get(
            reverse("linkding:bookmarks.index") + f"?details={bookmark.id}"
        )
        html = response.content.decode()

        self.assertNotIn('name="retry_favicon"', html)
        self.assertNotIn('name="retry_preview_image"', html)
        self.assertIn("✓ Complete", html)

    def test_detail_view_shows_metadata_section(self):
        """Detail view should show Metadata section when status fields are set."""
        bookmark = self.setup_bookmark()
        bookmark.favicon_status = "pending"
        bookmark.preview_image_status = "complete"
        bookmark.save()

        response = self.client.get(
            reverse("linkding:bookmarks.index") + f"?details={bookmark.id}"
        )
        html = response.content.decode()

        self.assertIn("Metadata", html)
        self.assertIn("Favicon", html)
        self.assertIn("Preview image", html)


class MetadataStatusSettingsTestCase(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    """Tests for metadata health dashboard in settings."""

    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.user.profile.enable_favicons = True
        self.user.profile.enable_preview_images = True
        self.user.profile.save()
        self.client.force_login(self.user)

    def test_settings_health_counts(self):
        """Settings page should show correct pending/failed counts."""
        # Create bookmarks with different statuses
        for i in range(3):
            b = self.setup_bookmark(url=f"https://example.com/pending-{i}")
            b.favicon_status = "pending"
            b.save()

        for i in range(2):
            b = self.setup_bookmark(url=f"https://example.com/failed-{i}")
            b.favicon_status = "failure"
            b.save()

        b = self.setup_bookmark(url="https://example.com/complete")
        b.favicon_status = "complete"
        b.save()

        response = self.client.get(reverse("linkding:settings.general"))
        html = response.content.decode()

        self.assertIn("Metadata Health", html)
        self.assertIn("Favicons pending", html)
        self.assertIn("3", html)  # 3 pending
        self.assertIn("Favicons failed", html)
        self.assertIn("2", html)  # 2 failed

    def test_settings_retry_all_failed_favicons_button(self):
        """Settings should show retry button when there are failed favicons."""
        bookmark = self.setup_bookmark()
        bookmark.favicon_status = "failure"
        bookmark.save()

        response = self.client.get(reverse("linkding:settings.general"))
        html = response.content.decode()

        self.assertIn('name="retry_all_failed_favicons"', html)

    def test_settings_hides_retry_button_when_no_failures(self):
        """Settings should NOT show retry button when there are no failures."""
        bookmark = self.setup_bookmark(favicon_file="icon.png")
        bookmark.favicon_status = "complete"
        bookmark.save()

        response = self.client.get(reverse("linkding:settings.general"))
        html = response.content.decode()

        self.assertNotIn('name="retry_all_failed_favicons"', html)

    def test_settings_retry_all_favicons_action(self):
        """Clicking retry all failed favicons should enqueue tasks."""
        bookmark = self.setup_bookmark()
        bookmark.favicon_status = "failure"
        bookmark.save()

        with mock.patch.object(
            bookmarks_service, "retry_all_failed_favicons", return_value=1
        ) as mock_retry:
            response = self.client.post(
                reverse("linkding:settings.update"),
                {"retry_all_failed_favicons": ""},
                follow=True,
            )
            mock_retry.assert_called_once_with(self.user)


class MetadataStatusBackfillTestCase(TestCase, BookmarkFactoryMixin):
    """Tests for schedule_bookmarks_without_favicons including failed bookmarks."""

    def setUp(self):
        huey.immediate = True
        huey.results = True
        huey.store_none = True

        self.mock_load_favicon_patcher = mock.patch(
            "bookmarks.services.favicon_loader.load_favicon"
        )
        self.mock_load_favicon = self.mock_load_favicon_patcher.start()
        self.mock_load_favicon.return_value = "https_example_com.png"

        self.mock_load_preview_image_patcher = mock.patch(
            "bookmarks.services.preview_image_loader.load_preview_image"
        )
        self.mock_load_preview_image = self.mock_load_preview_image_patcher.start()
        self.mock_load_preview_image.return_value = "preview_image.png"

        self.user = self.get_or_create_test_user()
        self.user.profile.enable_favicons = True
        self.user.profile.enable_preview_images = True
        self.user.profile.save()

    def tearDown(self):
        self.mock_load_favicon_patcher.stop()
        self.mock_load_preview_image_patcher.stop()
        huey.storage.flush_results()
        huey.immediate = False

    def test_schedule_without_favicons_includes_failed(self):
        """Backfill task should include bookmarks with failure status."""
        # Bookmark with empty favicon and failure status
        failed = self.setup_bookmark(url="https://example.com/failed")
        failed.favicon_status = "failure"
        failed.save()

        # Bookmark with empty favicon and no status (never attempted)
        empty = self.setup_bookmark(url="https://example.com/empty")
        empty.save()

        # Bookmark with existing favicon (should NOT be included)
        complete = self.setup_bookmark(
            url="https://example.com/complete",
            favicon_file="existing.png",
        )
        complete.favicon_status = "complete"
        complete.save()

        tasks.schedule_bookmarks_without_favicons(self.user)

        # Should have been called for failed and empty bookmarks (2 total)
        self.assertEqual(self.mock_load_favicon.call_count, 2)

    def test_schedule_without_previews_includes_failed(self):
        """Backfill task should include bookmarks with failure status for previews."""
        failed = self.setup_bookmark(url="https://example.com/failed")
        failed.preview_image_status = "failure"
        failed.save()

        empty = self.setup_bookmark(url="https://example.com/empty")
        empty.save()

        complete = self.setup_bookmark(
            url="https://example.com/complete",
            preview_image_file="existing.png",
        )
        complete.preview_image_status = "complete"
        complete.save()

        tasks.schedule_bookmarks_without_previews(self.user)

        self.assertEqual(self.mock_load_preview_image.call_count, 2)


class MetadataStatusMigrationTestCase(TestCase, BookmarkFactoryMixin):
    """Tests for the migration backfill logic."""

    def _run_backfill(self):
        """Run the backfill function from the migration."""
        import importlib

        migration_module = importlib.import_module(
            "bookmarks.migrations.0055_bookmark_metadata_status"
        )
        from django.apps import apps

        migration_module.backfill_metadata_status(apps, None)

    def test_backfill_sets_complete_for_existing_favicon_files(self):
        """Bookmarks with existing favicon files should get complete status."""
        bookmark = self.setup_bookmark(favicon_file="existing_icon.png")
        bookmark.favicon_status = ""
        bookmark.save()

        self._run_backfill()

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.favicon_status, "complete")

    def test_backfill_sets_complete_for_existing_preview_files(self):
        """Bookmarks with existing preview image files should get complete status."""
        bookmark = self.setup_bookmark(preview_image_file="existing_preview.png")
        bookmark.preview_image_status = ""
        bookmark.save()

        self._run_backfill()

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.preview_image_status, "complete")

    def test_backfill_leaves_empty_status_for_empty_files(self):
        """Bookmarks without files should keep empty status (not active)."""
        bookmark = self.setup_bookmark()
        bookmark.save()

        self._run_backfill()

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.favicon_status, "")
        self.assertEqual(bookmark.preview_image_status, "")
