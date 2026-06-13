from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from bookmarks.models import BookmarkBundle
from bookmarks.tests.helpers import BookmarkFactoryMixin


class BundleQuickSaveViewTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self) -> None:
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def create_form_data(self, overrides=None):
        if overrides is None:
            overrides = {}
        form_data = {
            "name": "Test Filter",
            "q": "search terms #tag1 #tag2",
            "unread": BookmarkBundle.FILTER_STATE_OFF,
            "shared": BookmarkBundle.FILTER_STATE_OFF,
            "return_url": reverse("linkding:bookmarks.index"),
        }
        return {**form_data, **overrides}

    # --- Create new filter tests ---

    def test_should_create_new_filter_from_search_state(self):
        form_data = self.create_form_data()

        response = self.client.post(
            reverse("linkding:bundles.quick_save"), form_data
        )

        self.assertEqual(BookmarkBundle.objects.count(), 1)
        bundle = BookmarkBundle.objects.first()
        self.assertEqual(bundle.owner, self.user)
        self.assertEqual(bundle.name, "Test Filter")
        self.assertEqual(bundle.search, "search terms")
        self.assertEqual(bundle.all_tags, "tag1 tag2")

    def test_should_redirect_with_bundle_param_after_create(self):
        form_data = self.create_form_data()

        response = self.client.post(
            reverse("linkding:bundles.quick_save"), form_data
        )

        bundle = BookmarkBundle.objects.first()
        expected_url = f"{reverse('linkding:bookmarks.index')}?bundle={bundle.id}"
        self.assertRedirects(response, expected_url, fetch_redirect_response=False)

    def test_should_map_q_search_terms_to_bundle_search(self):
        form_data = self.create_form_data({"q": "machine learning tutorial"})

        self.client.post(reverse("linkding:bundles.quick_save"), form_data)

        bundle = BookmarkBundle.objects.first()
        self.assertEqual(bundle.search, "machine learning tutorial")
        self.assertEqual(bundle.all_tags, "")

    def test_should_map_q_tags_to_bundle_all_tags(self):
        form_data = self.create_form_data({"q": "#python #django #web"})

        self.client.post(reverse("linkding:bundles.quick_save"), form_data)

        bundle = BookmarkBundle.objects.first()
        self.assertEqual(bundle.search, "")
        self.assertEqual(bundle.all_tags, "python django web")

    def test_should_map_mixed_query_to_search_and_tags(self):
        form_data = self.create_form_data({"q": "tutorial #python basics #web"})

        self.client.post(reverse("linkding:bundles.quick_save"), form_data)

        bundle = BookmarkBundle.objects.first()
        self.assertEqual(bundle.search, "tutorial basics")
        self.assertEqual(bundle.all_tags, "python web")

    def test_should_map_preferences_to_bundle_filters(self):
        form_data = self.create_form_data({
            "unread": BookmarkBundle.FILTER_STATE_YES,
            "shared": BookmarkBundle.FILTER_STATE_NO,
        })

        self.client.post(reverse("linkding:bundles.quick_save"), form_data)

        bundle = BookmarkBundle.objects.first()
        self.assertEqual(bundle.filter_unread, BookmarkBundle.FILTER_STATE_YES)
        self.assertEqual(bundle.filter_shared, BookmarkBundle.FILTER_STATE_NO)

    def test_should_require_name(self):
        form_data = self.create_form_data({"name": ""})

        response = self.client.post(
            reverse("linkding:bundles.quick_save"), form_data
        )

        self.assertEqual(BookmarkBundle.objects.count(), 0)
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("required", str(messages[0]).lower())

    def test_should_require_authentication(self):
        self.client.logout()
        form_data = self.create_form_data()

        response = self.client.post(
            reverse("linkding:bundles.quick_save"), form_data
        )

        # Should redirect to login
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)
        self.assertEqual(BookmarkBundle.objects.count(), 0)

    # --- Update existing filter tests ---

    def test_should_update_existing_filter_when_bundle_id_provided(self):
        existing_bundle = self.setup_bundle(
            name="Old Name",
            search="old search",
            all_tags="old-tag",
        )
        form_data = self.create_form_data({
            "name": "Updated Name",
            "q": "new search #new-tag",
            "save_filter_bundle_id": existing_bundle.id,
        })

        self.client.post(reverse("linkding:bundles.quick_save"), form_data)

        self.assertEqual(BookmarkBundle.objects.count(), 1)
        bundle = BookmarkBundle.objects.get(id=existing_bundle.id)
        self.assertEqual(bundle.name, "Updated Name")
        self.assertEqual(bundle.search, "new search")
        self.assertEqual(bundle.all_tags, "new-tag")

    def test_should_not_allow_updating_other_users_filter(self):
        other_user = self.setup_user()
        other_bundle = self.setup_bundle(user=other_user, name="Other's Bundle")

        form_data = self.create_form_data({
            "name": "Hijacked",
            "save_filter_bundle_id": other_bundle.id,
        })

        response = self.client.post(
            reverse("linkding:bundles.quick_save"), form_data
        )

        self.assertEqual(response.status_code, 404)
        # Verify the other user's bundle is unchanged
        other_bundle.refresh_from_db()
        self.assertEqual(other_bundle.name, "Other's Bundle")

    def test_should_preserve_order_when_updating(self):
        # Create bundles with specific orders
        self.setup_bundle(name="First", order=0)
        bundle_to_update = self.setup_bundle(name="Second", order=1)
        self.setup_bundle(name="Third", order=2)

        form_data = self.create_form_data({
            "name": "Updated Second",
            "save_filter_bundle_id": bundle_to_update.id,
        })

        self.client.post(reverse("linkding:bundles.quick_save"), form_data)

        bundle_to_update.refresh_from_db()
        self.assertEqual(bundle_to_update.order, 1)

    # --- Edge cases ---

    def test_should_handle_empty_search_query(self):
        form_data = self.create_form_data({"q": ""})

        self.client.post(reverse("linkding:bundles.quick_save"), form_data)

        bundle = BookmarkBundle.objects.first()
        self.assertEqual(bundle.search, "")
        self.assertEqual(bundle.all_tags, "")

    def test_should_strip_whitespace_from_name(self):
        form_data = self.create_form_data({"name": "  Trimmed Name  "})

        self.client.post(reverse("linkding:bundles.quick_save"), form_data)

        bundle = BookmarkBundle.objects.first()
        self.assertEqual(bundle.name, "Trimmed Name")

    def test_should_reject_non_post_requests(self):
        response = self.client.get(reverse("linkding:bundles.quick_save"))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(BookmarkBundle.objects.count(), 0)

    def test_should_use_default_return_url_when_not_provided(self):
        form_data = {
            "name": "Test Filter",
            "q": "test",
        }

        response = self.client.post(
            reverse("linkding:bundles.quick_save"), form_data
        )

        bundle = BookmarkBundle.objects.first()
        expected_url = f"{reverse('linkding:bookmarks.index')}?bundle={bundle.id}"
        self.assertRedirects(response, expected_url, fetch_redirect_response=False)

    def test_should_handle_return_url_with_existing_query_params(self):
        form_data = self.create_form_data({
            "return_url": reverse("linkding:bookmarks.archived") + "?sort=title_asc",
        })

        response = self.client.post(
            reverse("linkding:bundles.quick_save"), form_data
        )

        bundle = BookmarkBundle.objects.first()
        expected_url = f"{reverse('linkding:bookmarks.archived')}?sort=title_asc&bundle={bundle.id}"
        self.assertRedirects(response, expected_url, fetch_redirect_response=False)

    def test_should_increment_order_for_new_bundle(self):
        self.setup_bundle(name="Existing", order=5)

        form_data = self.create_form_data({"name": "New Filter"})
        self.client.post(reverse("linkding:bundles.quick_save"), form_data)

        new_bundle = BookmarkBundle.objects.get(name="New Filter")
        self.assertEqual(new_bundle.order, 6)

    def test_should_show_success_message(self):
        form_data = self.create_form_data({"name": "My Filter"})

        response = self.client.post(
            reverse("linkding:bundles.quick_save"), form_data, follow=True
        )

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("My Filter", str(messages[0]))
        self.assertIn("saved", str(messages[0]).lower())

    def test_should_handle_special_search_commands_in_query(self):
        form_data = self.create_form_data({"q": "tutorial !untagged !unread #python"})

        self.client.post(reverse("linkding:bundles.quick_save"), form_data)

        bundle = BookmarkBundle.objects.first()
        # Special commands should not be in search terms
        self.assertEqual(bundle.search, "tutorial")
        self.assertEqual(bundle.all_tags, "python")
