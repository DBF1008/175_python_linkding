from unittest import mock

from django.test import TestCase
from django.urls import reverse
from huey.contrib.djhuey import HUEY as huey

from bookmarks.models import Bookmark
from bookmarks.tests.helpers import BookmarkFactoryMixin


class MetadataCenterViewTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        huey.immediate = True
        huey.results = True
        huey.store_none = True

        self.mock_load_favicon_patcher = mock.patch(
            "bookmarks.services.favicon_loader.load_favicon"
        )
        self.mock_load_favicon = self.mock_load_favicon_patcher.start()
        self.mock_load_favicon.return_value = "favicon.png"

        self.mock_load_preview_patcher = mock.patch(
            "bookmarks.services.preview_image_loader.load_preview_image"
        )
        self.mock_load_preview = self.mock_load_preview_patcher.start()
        self.mock_load_preview.return_value = "preview.png"

        self.user = self.get_or_create_test_user()
        # Favicons + previews on, web archive left off to keep cases focused
        self.user.profile.enable_favicons = True
        self.user.profile.enable_preview_images = True
        self.user.profile.save()
        self.client.force_login(self.user)

    def tearDown(self):
        self.mock_load_favicon_patcher.stop()
        self.mock_load_preview_patcher.stop()
        huey.storage.flush_results()
        huey.immediate = False

    def index_url(self, query=""):
        return reverse("linkding:metadata.index") + query

    def action_url(self, query=""):
        return reverse("linkding:metadata.index.action") + query

    # Access
    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(self.index_url())
        self.assertEqual(response.status_code, 302)

    def test_renders_for_logged_in_user(self):
        response = self.client.get(self.index_url())
        self.assertEqual(response.status_code, 200)

    # Listing
    def test_lists_owned_bookmarks(self):
        self.setup_bookmark(
            title="MyBookmark", favicon_file="icon.png", preview_image_file="p.png"
        )
        response = self.client.get(self.index_url())
        self.assertContains(response, "MyBookmark")

    def test_excludes_other_users_bookmarks(self):
        other = self.setup_user()
        self.setup_bookmark(title="OtherBookmark", user=other)
        response = self.client.get(self.index_url())
        self.assertNotContains(response, "OtherBookmark")

    def test_shows_status_badges(self):
        self.setup_bookmark(title="NeedsIcon", preview_image_file="p.png")
        response = self.client.get(self.index_url())
        self.assertContains(response, 'data-metadata-type="favicon"')
        self.assertContains(response, 'data-metadata-state="missing"')

    # Filters
    def test_attention_filter_shows_missing_favicon(self):
        self.setup_bookmark(title="MissingIcon", preview_image_file="p.png")
        self.setup_bookmark(
            title="AllGood", favicon_file="icon.png", preview_image_file="p.png"
        )
        response = self.client.get(self.index_url("?status=attention"))
        self.assertContains(response, "MissingIcon")
        self.assertNotContains(response, "AllGood")

    def test_failed_filter_shows_failed(self):
        self.setup_bookmark(
            title="FailedIcon",
            favicon_status=Bookmark.METADATA_STATUS_FAILURE,
            preview_image_file="p.png",
        )
        self.setup_bookmark(
            title="GoodOne", favicon_file="icon.png", preview_image_file="p.png"
        )
        response = self.client.get(self.index_url("?status=failed"))
        self.assertContains(response, "FailedIcon")
        self.assertNotContains(response, "GoodOne")

    def test_summary_counts(self):
        # failed favicon (also counts as needing attention)
        self.setup_bookmark(
            favicon_status=Bookmark.METADATA_STATUS_FAILURE,
            preview_image_file="p.png",
        )
        # missing favicon (needs attention)
        self.setup_bookmark(preview_image_file="p.png")
        response = self.client.get(self.index_url())
        self.assertEqual(response.context["summary"]["failed"], 1)
        self.assertEqual(response.context["summary"]["attention"], 2)

    # Retry actions
    def test_single_retry_collects_metadata(self):
        bookmark = self.setup_bookmark(
            favicon_status=Bookmark.METADATA_STATUS_FAILURE
        )
        response = self.client.post(self.action_url(), {"retry": bookmark.id})
        self.assertEqual(response.status_code, 302)
        bookmark.refresh_from_db()
        self.assertEqual(bookmark.favicon_file, "favicon.png")
        self.assertEqual(
            bookmark.favicon_status, Bookmark.METADATA_STATUS_COMPLETE
        )

    def test_bulk_retry_selected(self):
        b1 = self.setup_bookmark()
        b2 = self.setup_bookmark()
        b3 = self.setup_bookmark()
        response = self.client.post(
            self.action_url(),
            {"bulk_execute": "1", "bookmark_id": [b1.id, b2.id]},
        )
        self.assertEqual(response.status_code, 302)
        b1.refresh_from_db()
        b2.refresh_from_db()
        b3.refresh_from_db()
        self.assertEqual(b1.favicon_status, Bookmark.METADATA_STATUS_COMPLETE)
        self.assertEqual(b2.favicon_status, Bookmark.METADATA_STATUS_COMPLETE)
        # Not selected, untouched
        self.assertEqual(b3.favicon_status, "")

    def test_bulk_retry_across_matching_filter(self):
        m1 = self.setup_bookmark(preview_image_file="p.png")  # missing favicon
        m2 = self.setup_bookmark(preview_image_file="p.png")  # missing favicon
        ok = self.setup_bookmark(
            favicon_file="icon.png", preview_image_file="p.png"
        )  # complete -> not in attention
        response = self.client.post(
            self.action_url("?status=attention"),
            {"bulk_execute": "1", "bulk_select_across": "on"},
        )
        self.assertEqual(response.status_code, 302)
        m1.refresh_from_db()
        m2.refresh_from_db()
        ok.refresh_from_db()
        self.assertEqual(m1.favicon_status, Bookmark.METADATA_STATUS_COMPLETE)
        self.assertEqual(m2.favicon_status, Bookmark.METADATA_STATUS_COMPLETE)
        # ok was not part of the attention filter, so it was not retried
        self.assertEqual(ok.favicon_status, "")

    def test_retry_ignores_other_users_bookmarks(self):
        other = self.setup_user()
        their_bookmark = self.setup_bookmark(
            user=other, favicon_status=Bookmark.METADATA_STATUS_FAILURE
        )
        response = self.client.post(
            self.action_url(), {"retry": their_bookmark.id}
        )
        self.assertEqual(response.status_code, 302)
        their_bookmark.refresh_from_db()
        self.assertEqual(
            their_bookmark.favicon_status, Bookmark.METADATA_STATUS_FAILURE
        )
        self.assertEqual(their_bookmark.favicon_file, "")

    # Status surfaced on list + detail
    def test_bookmark_list_shows_metadata_status(self):
        self.setup_bookmark(title="ListNeedsIcon", preview_image_file="p.png")
        response = self.client.get(reverse("linkding:bookmarks.index"))
        self.assertContains(response, 'data-metadata-type="favicon"')
        self.assertContains(response, 'data-metadata-state="missing"')

    def test_details_modal_shows_metadata_section_with_retry(self):
        bookmark = self.setup_bookmark(
            favicon_file="icon.png", preview_image_file="p.png"
        )
        response = self.client.get(
            reverse("linkding:bookmarks.index") + f"?details={bookmark.id}"
        )
        self.assertContains(response, 'name="retry_metadata"')
        self.assertContains(response, "Retry collection")
