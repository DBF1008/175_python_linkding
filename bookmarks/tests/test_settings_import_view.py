from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from bookmarks.models import Bookmark
from bookmarks.tests.helpers import (
    BookmarkFactoryMixin,
    BookmarkHtmlTag,
    ImportTestMixin,
    disable_logging,
)


class SettingsImportViewTestCase(TestCase, BookmarkFactoryMixin, ImportTestMixin):
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

    def _make_upload_file(self, html_content: str, filename: str = "bookmarks.html"):
        return SimpleUploadedFile(filename, html_content.encode("utf-8"), "text/html")

    def _precheck(self, html_content: str, map_private_flag: bool = False):
        """Helper: perform step 1 (precheck) and return response."""
        data = {
            "action": "precheck",
            "import_file": self._make_upload_file(html_content),
        }
        if map_private_flag:
            data["map_private_flag"] = "on"
        return self.client.post(reverse("linkding:settings.import"), data)

    def _confirm(
        self,
        duplicate_handling: str = "update",
        map_private_flag: bool = False,
    ):
        """Helper: perform step 2 (confirm) and return response."""
        data = {
            "action": "confirm",
            "duplicate_handling": duplicate_handling,
        }
        if map_private_flag:
            data["map_private_flag"] = "on"
        return self.client.post(
            reverse("linkding:settings.import"), data, follow=True
        )

    # --- Full flow tests (backward compatibility) ---

    def test_should_import_successfully(self):
        with open(
            "bookmarks/tests/resources/simple_valid_import_file.html"
        ) as import_file:
            content = import_file.read()

        # Step 1: precheck
        precheck_response = self._precheck(content)
        self.assertEqual(precheck_response.status_code, 200)

        # Step 2: confirm
        response = self._confirm()
        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertSuccessMessage(
            response,
            "Import complete: 3 imported out of 3 bookmarks.",
        )
        self.assertEqual(Bookmark.objects.count(), 3)

    def test_should_check_authentication(self):
        self.client.logout()
        response = self.client.get(reverse("linkding:settings.import"), follow=True)

        self.assertRedirects(
            response, reverse("login") + "?next=" + reverse("linkding:settings.import")
        )

    def test_should_show_hint_if_there_is_no_file(self):
        response = self.client.post(
            reverse("linkding:settings.import"),
            {"action": "precheck"},
            follow=True,
        )

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertNoSuccessMessage(response)
        self.assertErrorMessage(response, "Please select a file to import.")

    @disable_logging
    def test_should_show_hint_if_import_raises_exception(self):
        with open(
            "bookmarks/tests/resources/invalid_import_file.png", "rb"
        ) as import_file:
            response = self.client.post(
                reverse("linkding:settings.import"),
                {
                    "action": "precheck",
                    "import_file": import_file,
                },
                follow=True,
            )

            self.assertRedirects(response, reverse("linkding:settings.general"))
            self.assertNoSuccessMessage(response)
            self.assertErrorMessage(
                response,
                "Could not read the uploaded file. Please ensure it is a valid UTF-8 encoded file.",
            )

    @disable_logging
    def test_should_show_respective_hints_if_not_all_bookmarks_were_imported_successfully(
        self,
    ):
        with open(
            "bookmarks/tests/resources/simple_valid_import_file_with_one_invalid_bookmark.html"
        ) as import_file:
            content = import_file.read()

        # Step 1: precheck (shows summary with 2 new + 1 skip)
        precheck_response = self._precheck(content)
        self.assertEqual(precheck_response.status_code, 200)

        # Step 2: confirm
        response = self._confirm()
        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertSuccessMessage(
            response,
            "Import complete: 2 imported, 1 failed out of 3 bookmarks.",
        )

    def test_should_respect_map_private_flag_option(self):
        with open(
            "bookmarks/tests/resources/simple_valid_import_file.html"
        ) as import_file:
            content = import_file.read()

        # Without map_private_flag
        self._precheck(content)
        self._confirm()

        self.assertEqual(Bookmark.objects.count(), 3)
        self.assertEqual(Bookmark.objects.all()[0].shared, False)
        self.assertEqual(Bookmark.objects.all()[1].shared, False)
        self.assertEqual(Bookmark.objects.all()[2].shared, False)

        Bookmark.objects.all().delete()

        # With map_private_flag
        self._precheck(content, map_private_flag=True)
        self._confirm(map_private_flag=True)

        self.assertEqual(Bookmark.objects.count(), 3)
        self.assertEqual(Bookmark.objects.all()[0].shared, True)
        self.assertEqual(Bookmark.objects.all()[1].shared, True)
        self.assertEqual(Bookmark.objects.all()[2].shared, True)

    # --- Precheck step tests ---

    def test_precheck_renders_summary_page(self):
        html_tags = [
            BookmarkHtmlTag(href="https://new.example.com/1", title="New One"),
            BookmarkHtmlTag(href="https://new.example.com/2", title="New Two"),
        ]
        import_html = self.render_html(tags=html_tags)

        response = self._precheck(import_html)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Import Preview", content)
        self.assertIn("2", content)  # total / new count

    def test_precheck_shows_duplicate_count(self):
        user = self.get_or_create_test_user()
        self.setup_bookmark(url="https://existing.example.com", title="Existing", user=user)

        html_tags = [
            BookmarkHtmlTag(href="https://existing.example.com", title="Updated"),
            BookmarkHtmlTag(href="https://new.example.com", title="New"),
        ]
        import_html = self.render_html(tags=html_tags)

        response = self._precheck(import_html)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Duplicate Handling", content)

    def test_precheck_stores_file_in_session(self):
        html_tags = [
            BookmarkHtmlTag(href="https://example.com/1", title="One"),
        ]
        import_html = self.render_html(tags=html_tags)

        self._precheck(import_html)

        session = self.client.session
        self.assertIn("import_file_content", session)

    def test_precheck_empty_file_shows_error(self):
        import_html = self.render_html(tags_html="")

        response = self._precheck(import_html)

        # Should redirect with error
        self.assertEqual(response.status_code, 302)

    # --- Confirm step tests ---

    def test_confirm_creates_bookmarks(self):
        html_tags = [
            BookmarkHtmlTag(href="https://example.com/1", title="One"),
            BookmarkHtmlTag(href="https://example.com/2", title="Two"),
        ]
        import_html = self.render_html(tags=html_tags)

        self._precheck(import_html)
        response = self._confirm()

        self.assertEqual(Bookmark.objects.count(), 2)
        self.assertRedirects(response, reverse("linkding:settings.general"))

    def test_confirm_with_skip_strategy(self):
        user = self.get_or_create_test_user()
        existing = self.setup_bookmark(
            url="https://dup.example.com",
            title="Keep Me",
            user=user,
        )

        html_tags = [
            BookmarkHtmlTag(href="https://dup.example.com", title="Overwrite?"),
            BookmarkHtmlTag(href="https://new.example.com", title="New"),
        ]
        import_html = self.render_html(tags=html_tags)

        # Step 1
        self._precheck(import_html)
        # Step 2 with skip
        response = self._confirm(duplicate_handling="skip")

        # Existing bookmark is untouched
        existing.refresh_from_db()
        self.assertEqual(existing.title, "Keep Me")

        # New bookmark was created
        self.assertEqual(Bookmark.objects.count(), 2)
        self.assertTrue(Bookmark.objects.filter(url="https://new.example.com").exists())

        # Message reflects skip
        self.assertSuccessMessage(
            response,
            "Import complete: 1 imported, 1 skipped out of 2 bookmarks.",
        )

    def test_confirm_with_update_strategy(self):
        user = self.get_or_create_test_user()
        existing = self.setup_bookmark(
            url="https://dup.example.com",
            title="Old Title",
            user=user,
        )

        html_tags = [
            BookmarkHtmlTag(href="https://dup.example.com", title="New Title"),
        ]
        import_html = self.render_html(tags=html_tags)

        self._precheck(import_html)
        response = self._confirm(duplicate_handling="update")

        existing.refresh_from_db()
        self.assertEqual(existing.title, "New Title")
        self.assertSuccessMessage(
            response,
            "Import complete: 1 imported out of 1 bookmarks.",
        )

    def test_confirm_without_session_data_shows_error(self):
        """Confirm without a prior precheck should show error."""
        response = self._confirm()

        self.assertRedirects(response, reverse("linkding:settings.general"))
        self.assertErrorMessage(
            response, "No import file found. Please upload a file again."
        )

    def test_fallback_action_redirects_to_settings(self):
        """POST with no action or unknown action redirects to settings."""
        response = self.client.post(
            reverse("linkding:settings.import"), follow=True
        )

        self.assertRedirects(response, reverse("linkding:settings.general"))
