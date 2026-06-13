from django.urls import reverse
from playwright.sync_api import expect

from bookmarks.models import BookmarkBundle
from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class NamedFilterE2ETestCase(LinkdingE2ETestCase):
    """E2E tests for the named filter workflow (quick save bundles from search)."""

    def open_search_preferences(self, page):
        """Helper to open the search preferences dropdown."""
        dropdown_toggle = page.locator(".search-options .dropdown-toggle")
        dropdown_toggle.click()
        # Wait for menu to be visible
        menu = page.locator(".search-options .menu")
        expect(menu).to_be_visible()
        return menu

    def test_save_current_search_as_new_filter(self):
        """Test creating a new named filter from current search state."""
        # Setup bookmarks with tags
        tag = self.setup_tag(name="python")
        self.setup_bookmark(title="Django Tutorial", tags=[tag])
        self.setup_bookmark(title="Python Guide", tags=[tag])
        self.setup_bookmark(title="JavaScript Docs")  # No tag, different content

        page = self.open(reverse("linkding:bookmarks.index"))

        # Enter search query
        search_input = page.locator('input[name="q"]')
        search_input.fill("tutorial #python")
        search_input.press("Enter")
        page.wait_for_load_state("networkidle")

        # Open search preferences dropdown
        menu = self.open_search_preferences(page)

        # Fill in filter name and save
        name_input = menu.locator('#save_filter_form input[name="name"]')
        name_input.fill("Python Tutorials")

        save_button = menu.locator('#save_filter_form button[type="submit"]')
        save_button.click()
        page.wait_for_load_state("networkidle")

        # Verify: redirected with bundle param
        self.assertIn("bundle=", page.url)

        # Verify: filter appears in side panel
        side_panel = page.locator(".side-panel")
        expect(side_panel.get_by_text("Python Tutorials")).to_be_visible()

        # Verify: active filter chip is shown
        active_chip = page.locator(".active-filter-chip")
        expect(active_chip).to_be_visible()
        expect(active_chip.locator(".chip-label")).to_have_text("Python Tutorials")

        # Verify: bundle was created in database
        bundle = BookmarkBundle.objects.filter(name="Python Tutorials").first()
        self.assertIsNotNone(bundle)
        self.assertEqual(bundle.search, "tutorial")
        self.assertEqual(bundle.all_tags, "python")

    def test_overwrite_existing_filter(self):
        """Test updating an existing filter with new search conditions."""
        # Create initial filter
        initial_bundle = self.setup_bundle(
            name="Tech Articles",
            search="initial",
            all_tags="initial-tag",
        )
        tag = self.setup_tag(name="python")
        self.setup_bookmark(title="Python Article", tags=[tag])

        page = self.open(reverse("linkding:bookmarks.index"))

        # Enter new search query
        search_input = page.locator('input[name="q"]')
        search_input.fill("article #python")
        search_input.press("Enter")
        page.wait_for_load_state("networkidle")

        # Open search preferences dropdown
        menu = self.open_search_preferences(page)

        # Select existing bundle to overwrite
        select = menu.locator('#save_filter_form select[name="save_filter_bundle_id"]')
        select.select_option(str(initial_bundle.id))

        # Enter the same name (required field)
        name_input = menu.locator('#save_filter_form input[name="name"]')
        name_input.fill("Tech Articles")  # Same name

        # Save
        save_button = menu.locator('#save_filter_form button[type="submit"]')
        save_button.click()
        page.wait_for_load_state("networkidle")

        # Verify: bundle was updated
        initial_bundle.refresh_from_db()
        self.assertEqual(initial_bundle.search, "article")
        self.assertEqual(initial_bundle.all_tags, "python")

        # Verify: still only one bundle
        self.assertEqual(BookmarkBundle.objects.count(), 1)

    def test_rename_filter_via_overwrite(self):
        """Test renaming a filter by overwriting with a new name."""
        # Create initial filter
        initial_bundle = self.setup_bundle(
            name="Old Name",
            search="test",
        )
        self.setup_bookmark(title="Test Bookmark")

        page = self.open(reverse("linkding:bookmarks.index"))

        # Open search preferences dropdown
        menu = self.open_search_preferences(page)

        # Select existing bundle to overwrite
        select = menu.locator('#save_filter_form select[name="save_filter_bundle_id"]')
        select.select_option(str(initial_bundle.id))

        # Enter new name
        name_input = menu.locator('#save_filter_form input[name="name"]')
        name_input.fill("New Name")

        # Save
        save_button = menu.locator('#save_filter_form button[type="submit"]')
        save_button.click()
        page.wait_for_load_state("networkidle")

        # Verify: bundle name was updated
        initial_bundle.refresh_from_db()
        self.assertEqual(initial_bundle.name, "New Name")

        # Verify: new name appears in side panel
        side_panel = page.locator(".side-panel")
        expect(side_panel.get_by_text("New Name")).to_be_visible()
        expect(side_panel.get_by_text("Old Name")).not_to_be_visible()

    def test_cross_page_filter_persistence(self):
        """Test that a named filter persists when navigating between pages."""
        # Create a filter and some bookmarks
        tag = self.setup_tag(name="python")
        self.setup_bookmark(title="Active Python Article", tags=[tag], is_archived=False)
        self.setup_bookmark(title="Archived Python Article", tags=[tag], is_archived=True)

        # Create the bundle
        bundle = self.setup_bundle(
            name="Python Filter",
            all_tags="python",
        )

        # Navigate to bookmarks page with bundle selected
        url = reverse("linkding:bookmarks.index") + f"?bundle={bundle.id}"
        page = self.open(url)

        # Verify: filter is active on bookmarks page
        active_chip = page.locator(".active-filter-chip")
        expect(active_chip).to_be_visible()
        expect(active_chip.locator(".chip-label")).to_have_text("Python Filter")

        # Verify: only active bookmark is shown
        expect(page.get_by_text("Active Python Article")).to_be_visible()
        expect(page.get_by_text("Archived Python Article")).not_to_be_visible()

        # Click on bundle link in side panel to navigate to archived page
        # The bundle links in side panel should preserve the bundle selection
        side_panel = page.locator(".side-panel")
        bundle_link = side_panel.locator(f'a[href*="bundle={bundle.id}"]').first()

        # Navigate to archived page with same bundle
        archived_url = reverse("linkding:bookmarks.archived") + f"?bundle={bundle.id}"
        page.goto(self.live_server_url + archived_url)
        page.wait_for_load_state("networkidle")

        # Verify: filter is still active on archived page
        active_chip = page.locator(".active-filter-chip")
        expect(active_chip).to_be_visible()
        expect(active_chip.locator(".chip-label")).to_have_text("Python Filter")

        # Verify: only archived bookmark is shown
        expect(page.get_by_text("Archived Python Article")).to_be_visible()
        expect(page.get_by_text("Active Python Article")).not_to_be_visible()

    def test_active_filter_indicator_shows_and_clears(self):
        """Test that the active filter indicator shows and can be cleared."""
        # Create a bundle
        bundle = self.setup_bundle(
            name="My Filter",
            search="test",
        )
        self.setup_bookmark(title="Test Bookmark")

        # Navigate with bundle selected
        url = reverse("linkding:bookmarks.index") + f"?bundle={bundle.id}"
        page = self.open(url)

        # Verify: active filter chip is visible
        active_chip = page.locator(".active-filter-chip")
        expect(active_chip).to_be_visible()
        expect(active_chip.locator(".chip-label")).to_have_text("My Filter")

        # Click the close button on the chip
        close_button = active_chip.locator(".chip-close")
        close_button.click()
        page.wait_for_load_state("networkidle")

        # Verify: chip is no longer visible
        expect(active_chip).not_to_be_visible()

        # Verify: URL no longer has bundle param
        self.assertNotIn("bundle=", page.url)

    def test_filter_selection_from_dropdown(self):
        """Test selecting a filter from the dropdown applies it."""
        # Create multiple bundles
        bundle1 = self.setup_bundle(name="Filter A", search="alpha")
        bundle2 = self.setup_bundle(name="Filter B", search="beta")

        self.setup_bookmark(title="Alpha Bookmark")
        self.setup_bookmark(title="Beta Bookmark")

        page = self.open(reverse("linkding:bookmarks.index"))

        # Open search preferences dropdown
        menu = self.open_search_preferences(page)

        # Verify: both filters are in the select dropdown
        select = menu.locator('#save_filter_form select[name="save_filter_bundle_id"]')
        expect(select.locator('option[value=""]')).to_have_text("-- Create new --")
        expect(select.locator(f'option[value="{bundle1.id}"]')).to_have_text("Filter A")
        expect(select.locator(f'option[value="{bundle2.id}"]')).to_have_text("Filter B")

    def test_save_filter_shows_success_message(self):
        """Test that saving a filter shows a success toast message."""
        self.setup_bookmark(title="Test Bookmark")

        page = self.open(reverse("linkding:bookmarks.index"))

        # Open search preferences dropdown
        menu = self.open_search_preferences(page)

        # Fill in filter name and save
        name_input = menu.locator('#save_filter_form input[name="name"]')
        name_input.fill("Success Test Filter")

        save_button = menu.locator('#save_filter_form button[type="submit"]')
        save_button.click()
        page.wait_for_load_state("networkidle")

        # Verify: success message is shown (toast)
        toast = page.locator(".toast-success, .toast").first
        expect(toast).to_be_visible()
        expect(toast).to_contain_text("Success Test Filter")

    def test_empty_name_shows_error(self):
        """Test that submitting without a name shows an error message."""
        self.setup_bookmark(title="Test Bookmark")

        page = self.open(reverse("linkding:bookmarks.index"))

        # Open search preferences dropdown
        menu = self.open_search_preferences(page)

        # Leave name empty and try to save
        save_button = menu.locator('#save_filter_form button[type="submit"]')
        save_button.click()
        page.wait_for_load_state("networkidle")

        # Verify: error message is shown
        toast = page.locator(".toast-error, .toast").first
        expect(toast).to_be_visible()
        expect(toast).to_contain_text("required")

        # Verify: no bundle was created
        self.assertEqual(BookmarkBundle.objects.count(), 0)
