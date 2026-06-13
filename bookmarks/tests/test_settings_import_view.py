from django.test import TestCase
from django.urls import reverse

from bookmarks.models import Bookmark
from bookmarks.tests.helpers import BookmarkFactoryMixin, disable_logging


class SettingsImportViewTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self) -> None:
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def assertSuccessMessage(self, response, message: str):
        self.assertInHTML(
            f"""
            <div class="toast toast-success mb-4">{message}</div>
        """,
            response.content.decode("utf-8"),
        )

    def assertNoSuccessMessage(self, response):
        self.assertNotContains(response, '<div class="toast toast-success mb-4">')

    def assertErrorMessage(self, response, message: str):
        self.assertInHTML(
            f"""
            <div class="toast toast-error mb-4">{message}</div>
        """,
            response.content.decode("utf-8"),
        )

    def assertNoErrorMessage(self, response):
        self.assertNotContains(response, '<div class="toast toast-error mb-4">')

    def post_preview(self, resource: str, mode: str = "r", **extra):
        """Upload a file to the import endpoint, returning the rendered preview."""
        with open(f"bookmarks/tests/resources/{resource}", mode) as import_file:
            return self.client.post(
                reverse("linkding:settings.import"),
                {"import_file": import_file, **extra},
                follow=True,
            )

    def confirm(self, strategy: str = None):
        data = {}
        if strategy is not None:
            data["strategy"] = strategy
        return self.client.post(
            reverse("linkding:settings.import.confirm"), data, follow=True
        )

    # --- Authentication ---

    def test_preview_should_check_authentication(self):
        self.client.logout()
        response = self.client.get(reverse("linkding:settings.import"), follow=True)

        self.assertRedirects(
            response, reverse("login") + "?next=" + reverse("linkding:settings.import")
        )

    def test_confirm_should_check_authentication(self):
        self.client.logout()
        response = self.client.get(
            reverse("linkding:settings.import.confirm"), follow=True
        )

        self.assertRedirects(
            response,
            reverse("login") + "?next=" + reverse("linkding:settings.import.confirm"),
        )

    # --- Preview (pre-flight check) ---

    def test_preview_shows_summary_without_writing(self):
        response = self.post_preview("simple_valid_import_file.html")

        self.assertEqual(response.status_code, 200)
        # The summary and the strategy selector are rendered
        self.assertContains(response, "New bookmarks")
        self.assertContains(response, 'name="strategy"')
        self.assertContains(response, "Confirm import")
        # Counts are correct: a fresh import is all new
        preview = response.context["import_preview"]
        self.assertEqual(preview.total, 3)
        self.assertEqual(preview.new, 3)
        self.assertEqual(preview.existing, 0)
        self.assertEqual(preview.invalid, 0)
        # Nothing is imported during the preview step
        self.assertEqual(Bookmark.objects.count(), 0)
        self.assertNoSuccessMessage(response)

    def test_preview_counts_existing_bookmarks(self):
        user = self.get_or_create_test_user()
        self.setup_bookmark(url="https://example.com/1", user=user)

        response = self.post_preview("simple_valid_import_file.html")

        self.assertContains(response, "Existing bookmarks (duplicate URLs)")
        preview = response.context["import_preview"]
        self.assertEqual(preview.new, 2)
        self.assertEqual(preview.existing, 1)
        self.assertEqual(preview.invalid, 0)
        # Still only the pre-existing bookmark
        self.assertEqual(Bookmark.objects.count(), 1)

    def test_preview_counts_invalid_bookmarks(self):
        response = self.post_preview(
            "simple_valid_import_file_with_one_invalid_bookmark.html"
        )

        preview = response.context["import_preview"]
        self.assertEqual(preview.total, 3)
        self.assertEqual(preview.new, 2)
        self.assertEqual(preview.invalid, 1)

    def test_preview_missing_file_shows_hint(self):
        response = self.client.post(reverse("linkding:settings.import"), follow=True)

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertNoSuccessMessage(response)
        self.assertErrorMessage(response, "Please select a file to import.")

    @disable_logging
    def test_preview_invalid_file_shows_error(self):
        response = self.post_preview("invalid_import_file.png", mode="rb")

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertNoSuccessMessage(response)
        self.assertErrorMessage(response, "An error occurred during bookmark import.")
        # No preview is stashed for an unreadable file
        self.assertNotIn("import_preview", self.client.session)

    # --- Confirm (write step) ---

    def test_confirm_imports_new_bookmarks(self):
        self.post_preview("simple_valid_import_file.html")
        response = self.confirm()

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertEqual(Bookmark.objects.count(), 3)
        self.assertSuccessMessage(
            response, "Import complete: 3 added, 0 updated, 0 skipped."
        )
        self.assertNoErrorMessage(response)
        # The stashed preview is cleared after confirming
        self.assertNotIn("import_preview", self.client.session)

    def test_confirm_update_strategy_updates_duplicates(self):
        user = self.get_or_create_test_user()
        self.setup_bookmark(url="https://example.com/1", title="Original", user=user)

        self.post_preview("simple_valid_import_file.html")
        response = self.confirm(strategy="update")

        # 1 updated in place + 2 created = 3 total
        self.assertEqual(Bookmark.objects.count(), 3)
        updated = Bookmark.objects.get(url="https://example.com/1")
        self.assertEqual(updated.title, "test title 1")
        self.assertSuccessMessage(
            response, "Import complete: 2 added, 1 updated, 0 skipped."
        )

    def test_confirm_skip_strategy_skips_duplicates(self):
        user = self.get_or_create_test_user()
        existing = self.setup_bookmark(
            url="https://example.com/1", title="Original", user=user
        )

        self.post_preview("simple_valid_import_file.html")
        response = self.confirm(strategy="skip")

        # 2 created, 1 skipped (left untouched) = 3 total
        self.assertEqual(Bookmark.objects.count(), 3)
        existing.refresh_from_db()
        self.assertEqual(existing.title, "Original")
        self.assertSuccessMessage(
            response, "Import complete: 2 added, 0 updated, 1 skipped."
        )

    @disable_logging
    def test_confirm_reports_failed_bookmarks(self):
        self.post_preview("simple_valid_import_file_with_one_invalid_bookmark.html")
        response = self.confirm()

        self.assertEqual(Bookmark.objects.count(), 2)
        self.assertSuccessMessage(
            response, "Import complete: 2 added, 0 updated, 0 skipped."
        )
        self.assertErrorMessage(
            response,
            "1 bookmarks could not be imported. Please check the logs for more details.",
        )

    def test_confirm_without_preview_shows_error(self):
        response = self.confirm(strategy="update")

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertNoSuccessMessage(response)
        self.assertErrorMessage(
            response, "No import to confirm. Please upload a file again."
        )
        self.assertEqual(Bookmark.objects.count(), 0)

    def test_confirm_invalid_strategy_falls_back_to_update(self):
        user = self.get_or_create_test_user()
        self.setup_bookmark(url="https://example.com/1", title="Original", user=user)

        self.post_preview("simple_valid_import_file.html")
        response = self.confirm(strategy="not-a-real-strategy")

        # Falls back to the default UPDATE strategy
        updated = Bookmark.objects.get(url="https://example.com/1")
        self.assertEqual(updated.title, "test title 1")
        self.assertSuccessMessage(
            response, "Import complete: 2 added, 1 updated, 0 skipped."
        )

    def test_confirm_respects_map_private_flag_option(self):
        # Without the flag, bookmarks are imported as private
        self.post_preview("simple_valid_import_file.html")
        self.confirm()

        self.assertEqual(Bookmark.objects.count(), 3)
        for bookmark in Bookmark.objects.all():
            self.assertFalse(bookmark.shared)

        Bookmark.objects.all().delete()

        # With the flag, the choice made at upload time is carried through to confirm
        self.post_preview("simple_valid_import_file.html", map_private_flag="on")
        self.confirm()

        self.assertEqual(Bookmark.objects.count(), 3)
        for bookmark in Bookmark.objects.all():
            self.assertTrue(bookmark.shared)
