import datetime
import json

from django.core.exceptions import ValidationError
from django.test import TestCase

from bookmarks.models import Bookmark, BookmarkBundle, Tag
from bookmarks.services import workspace
from bookmarks.services.workspace import WorkspaceImportError
from bookmarks.tests.helpers import BookmarkFactoryMixin, disable_logging


def bookmark_entry(url, **overrides):
    entry = {
        "url": url,
        "title": "Title",
        "description": "",
        "notes": "",
        "unread": False,
        "is_archived": False,
        "shared": False,
        "web_archive_snapshot_url": "",
        "date_added": "2023-01-01T00:00:00+00:00",
        "date_modified": "2023-01-01T00:00:00+00:00",
        "date_accessed": None,
        "tag_names": [],
    }
    entry.update(overrides)
    return entry


def bundle_entry(name, **overrides):
    entry = {
        "name": name,
        "search": "",
        "any_tags": "",
        "all_tags": "",
        "excluded_tags": "",
        "filter_unread": BookmarkBundle.FILTER_STATE_OFF,
        "filter_shared": BookmarkBundle.FILTER_STATE_OFF,
        "order": 0,
        "date_created": None,
        "date_modified": None,
    }
    entry.update(overrides)
    return entry


def backup_payload(bookmarks=None, tags=None, bundles=None, version=1):
    return {
        "version": version,
        "tags": tags or [],
        "bundles": bundles or [],
        "bookmarks": bookmarks or [],
    }


class WorkspaceExportTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()

    def test_export_includes_all_fields_states_tags_and_bundles(self):
        tag1 = self.setup_tag(name="alpha")
        tag2 = self.setup_tag(name="beta")
        # A tag not attached to any bookmark must still be exported
        self.setup_tag(name="orphan")
        added = datetime.datetime(2023, 1, 2, 3, 4, 5, 123456, tzinfo=datetime.UTC)
        modified = datetime.datetime(2023, 2, 3, 4, 5, 6, 654321, tzinfo=datetime.UTC)
        self.setup_bookmark(
            url="https://example.com/1",
            title="Title",
            description="Desc",
            notes="Notes",
            unread=True,
            shared=True,
            is_archived=True,
            web_archive_snapshot_url="https://web.archive.org/x",
            tags=[tag1, tag2],
            added=added,
            modified=modified,
        )
        self.setup_bundle(
            name="Bundle 1",
            search="foo",
            any_tags="alpha",
            all_tags="beta",
            excluded_tags="gamma",
            filter_unread=BookmarkBundle.FILTER_STATE_YES,
            filter_shared=BookmarkBundle.FILTER_STATE_NO,
            order=2,
        )

        data = workspace.export_workspace(self.user)

        self.assertEqual(data["version"], workspace.WORKSPACE_VERSION)
        self.assertIn("date_exported", data)

        tag_names = {t["name"] for t in data["tags"]}
        self.assertEqual(tag_names, {"alpha", "beta", "orphan"})

        self.assertEqual(len(data["bookmarks"]), 1)
        bookmark = data["bookmarks"][0]
        self.assertEqual(bookmark["url"], "https://example.com/1")
        self.assertEqual(bookmark["title"], "Title")
        self.assertEqual(bookmark["description"], "Desc")
        self.assertEqual(bookmark["notes"], "Notes")
        self.assertTrue(bookmark["unread"])
        self.assertTrue(bookmark["shared"])
        self.assertTrue(bookmark["is_archived"])
        self.assertEqual(
            bookmark["web_archive_snapshot_url"], "https://web.archive.org/x"
        )
        self.assertEqual(bookmark["tag_names"], ["alpha", "beta"])
        self.assertEqual(bookmark["date_added"], added.isoformat())
        self.assertEqual(bookmark["date_modified"], modified.isoformat())
        self.assertIsNone(bookmark["date_accessed"])

        self.assertEqual(len(data["bundles"]), 1)
        bundle = data["bundles"][0]
        self.assertEqual(bundle["name"], "Bundle 1")
        self.assertEqual(bundle["search"], "foo")
        self.assertEqual(bundle["any_tags"], "alpha")
        self.assertEqual(bundle["all_tags"], "beta")
        self.assertEqual(bundle["excluded_tags"], "gamma")
        self.assertEqual(bundle["filter_unread"], BookmarkBundle.FILTER_STATE_YES)
        self.assertEqual(bundle["filter_shared"], BookmarkBundle.FILTER_STATE_NO)
        self.assertEqual(bundle["order"], 2)

    def test_export_json_is_parseable(self):
        self.setup_bookmark(url="https://example.com/1", tags=[self.setup_tag()])
        content = workspace.export_workspace_json(self.user)
        data = json.loads(content)
        self.assertEqual(data["version"], workspace.WORKSPACE_VERSION)
        self.assertEqual(len(data["bookmarks"]), 1)

    def test_export_only_includes_own_data(self):
        other_user = self.setup_user()
        self.setup_bookmark(url="https://example.com/mine")
        self.setup_bookmark(url="https://example.com/theirs", user=other_user)
        self.setup_bundle(name="Mine")
        self.setup_bundle(name="Theirs", user=other_user)

        data = workspace.export_workspace(self.user)

        urls = {b["url"] for b in data["bookmarks"]}
        self.assertEqual(urls, {"https://example.com/mine"})
        names = {b["name"] for b in data["bundles"]}
        self.assertEqual(names, {"Mine"})


class WorkspaceRoundTripTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()

    def test_full_round_trip_restores_everything(self):
        tag1 = self.setup_tag(name="alpha")
        tag2 = self.setup_tag(name="beta")
        self.setup_tag(name="orphan")
        accessed = datetime.datetime(2023, 5, 1, 12, 0, 0, 1, tzinfo=datetime.UTC)
        rich = self.setup_bookmark(
            url="https://example.com/round",
            title="Round",
            description="d",
            notes="n",
            unread=True,
            shared=True,
            is_archived=True,
            web_archive_snapshot_url="https://web.archive.org/r",
            tags=[tag1, tag2],
        )
        rich.date_accessed = accessed
        rich.save()
        self.setup_bookmark(url="https://example.com/plain", title="Plain")
        self.setup_bundle(name="B1", any_tags="alpha", order=0)
        self.setup_bundle(
            name="B2",
            search="x",
            filter_unread=BookmarkBundle.FILTER_STATE_YES,
            order=1,
        )

        exported = workspace.export_workspace_json(self.user)

        # Wipe the workspace
        Bookmark.objects.all().delete()
        Tag.objects.all().delete()
        BookmarkBundle.objects.all().delete()

        result = workspace.import_workspace(exported, self.user)

        # Bookmarks restored faithfully
        self.assertEqual(Bookmark.objects.count(), 2)
        restored = Bookmark.objects.get(url="https://example.com/round")
        self.assertEqual(restored.title, "Round")
        self.assertEqual(restored.description, "d")
        self.assertEqual(restored.notes, "n")
        self.assertTrue(restored.unread)
        self.assertTrue(restored.shared)
        self.assertTrue(restored.is_archived)
        self.assertEqual(restored.web_archive_snapshot_url, "https://web.archive.org/r")
        self.assertEqual(restored.date_accessed, accessed)
        self.assertEqual(sorted(t.name for t in restored.tags.all()), ["alpha", "beta"])

        # Tags restored, including the orphan tag
        self.assertEqual(
            set(Tag.objects.values_list("name", flat=True)),
            {"alpha", "beta", "orphan"},
        )

        # Bundles restored faithfully
        self.assertEqual(BookmarkBundle.objects.count(), 2)
        b1 = BookmarkBundle.objects.get(name="B1")
        self.assertEqual(b1.any_tags, "alpha")
        self.assertEqual(b1.order, 0)
        b2 = BookmarkBundle.objects.get(name="B2")
        self.assertEqual(b2.search, "x")
        self.assertEqual(b2.filter_unread, BookmarkBundle.FILTER_STATE_YES)
        self.assertEqual(b2.order, 1)

        self.assertEqual(result.bookmarks_created, 2)
        self.assertEqual(result.bookmarks_updated, 0)
        self.assertEqual(result.bundles_created, 2)
        self.assertEqual(result.tags_created, 3)

    def test_round_trip_output_is_stable(self):
        tag1 = self.setup_tag(name="alpha")
        self.setup_tag(name="orphan")
        self.setup_bookmark(url="https://example.com/a", title="A", tags=[tag1])
        self.setup_bookmark(url="https://example.com/b", title="B")
        self.setup_bundle(name="B1", any_tags="alpha", order=0)

        exported = workspace.export_workspace_json(self.user)

        Bookmark.objects.all().delete()
        Tag.objects.all().delete()
        BookmarkBundle.objects.all().delete()

        workspace.import_workspace(exported, self.user)
        re_exported = workspace.export_workspace_json(self.user)

        # Re-exporting a restored workspace yields identical data (ignoring the
        # per-call export timestamp)
        first = json.loads(exported)
        second = json.loads(re_exported)
        first.pop("date_exported")
        second.pop("date_exported")
        self.assertEqual(first, second)


class WorkspaceMergeRestoreTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()

    def test_merge_restore(self):
        keep_tag = self.setup_tag(name="keep")
        remove_tag = self.setup_tag(name="willberemoved")
        to_update = self.setup_bookmark(
            url="https://example.com/shared",
            title="Old Title",
            notes="old",
            unread=False,
            tags=[keep_tag, remove_tag],
        )
        untouched = self.setup_bookmark(
            url="https://example.com/untouched", title="Untouched"
        )
        existing_bundle = self.setup_bundle(name="Shared Bundle", search="old", order=0)

        payload = backup_payload(
            bookmarks=[
                bookmark_entry(
                    "https://example.com/shared",
                    title="New Title",
                    notes="new",
                    unread=True,
                    tag_names=["keep"],
                ),
                bookmark_entry(
                    "https://example.com/new",
                    title="Brand New",
                    tag_names=["brandnewtag"],
                ),
            ],
            bundles=[
                bundle_entry("Shared Bundle", search="new", order=0),
                bundle_entry("New Bundle", any_tags="x", order=1),
            ],
        )

        result = workspace.import_workspace(json.dumps(payload), self.user)

        # Matched bookmark overwritten, including its tag set (shrunk)
        to_update.refresh_from_db()
        self.assertEqual(to_update.title, "New Title")
        self.assertEqual(to_update.notes, "new")
        self.assertTrue(to_update.unread)
        self.assertEqual(sorted(t.name for t in to_update.tags.all()), ["keep"])

        # Bookmark not present in the backup is left untouched
        untouched.refresh_from_db()
        self.assertEqual(untouched.title, "Untouched")
        self.assertEqual(
            Bookmark.objects.filter(url="https://example.com/untouched").count(), 1
        )

        # New bookmark added
        self.assertTrue(Bookmark.objects.filter(url="https://example.com/new").exists())
        self.assertEqual(Bookmark.objects.count(), 3)

        # Bundle updated in place (not duplicated), new bundle added
        existing_bundle.refresh_from_db()
        self.assertEqual(existing_bundle.search, "new")
        self.assertEqual(BookmarkBundle.objects.filter(name="Shared Bundle").count(), 1)
        self.assertTrue(BookmarkBundle.objects.filter(name="New Bundle").exists())
        self.assertEqual(BookmarkBundle.objects.count(), 2)

        # No duplicate tags; brandnewtag created, existing tags reused
        self.assertEqual(Tag.objects.filter(name="keep").count(), 1)
        self.assertTrue(Tag.objects.filter(name="brandnewtag").exists())

        self.assertEqual(result.bookmarks_created, 1)
        self.assertEqual(result.bookmarks_updated, 1)
        self.assertEqual(result.bundles_created, 1)
        self.assertEqual(result.bundles_updated, 1)
        self.assertEqual(result.tags_created, 1)

    def test_import_matches_existing_bookmark_by_normalized_url(self):
        existing = self.setup_bookmark(url="https://example.com/page", title="Old")

        payload = backup_payload(
            bookmarks=[
                # Same URL but with a trailing slash; should match the existing one
                bookmark_entry("https://example.com/page/", title="New"),
            ]
        )
        workspace.import_workspace(json.dumps(payload), self.user)

        self.assertEqual(Bookmark.objects.count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.title, "New")

    def test_import_assigns_to_target_user_only(self):
        other_user = self.setup_user()
        other_bookmark = self.setup_bookmark(
            url="https://example.com/shared", user=other_user, title="Other"
        )

        payload = backup_payload(
            bookmarks=[bookmark_entry("https://example.com/shared", title="Mine")]
        )
        workspace.import_workspace(json.dumps(payload), self.user)

        # The other user's bookmark with the same URL is untouched
        other_bookmark.refresh_from_db()
        self.assertEqual(other_bookmark.title, "Other")
        # The importing user got their own copy
        self.assertTrue(
            Bookmark.objects.filter(
                owner=self.user, url="https://example.com/shared"
            ).exists()
        )
        self.assertEqual(
            Bookmark.objects.filter(url="https://example.com/shared").count(), 2
        )

    def test_import_deduplicates_entries_with_same_normalized_url(self):
        payload = backup_payload(
            bookmarks=[
                bookmark_entry("https://example.com/dup", title="First"),
                bookmark_entry("https://example.com/dup/", title="Second"),
            ]
        )
        result = workspace.import_workspace(json.dumps(payload), self.user)

        self.assertEqual(Bookmark.objects.count(), 1)
        self.assertEqual(result.bookmarks_created, 1)
        # Last entry wins
        self.assertEqual(Bookmark.objects.first().title, "Second")


class WorkspaceImportValidationTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()

    def test_invalid_json_raises(self):
        with self.assertRaises(WorkspaceImportError):
            workspace.import_workspace("this is not json", self.user)

    def test_non_object_payload_raises(self):
        with self.assertRaises(WorkspaceImportError):
            workspace.import_workspace(json.dumps([1, 2, 3]), self.user)

    def test_missing_version_raises(self):
        payload = {"tags": [], "bundles": [], "bookmarks": []}
        with self.assertRaises(WorkspaceImportError):
            workspace.import_workspace(json.dumps(payload), self.user)

    def test_unsupported_version_raises(self):
        payload = backup_payload(version=workspace.WORKSPACE_VERSION + 1)
        with self.assertRaises(WorkspaceImportError):
            workspace.import_workspace(json.dumps(payload), self.user)

    def test_invalid_datetime_raises(self):
        payload = backup_payload(
            bookmarks=[bookmark_entry("https://example.com/x", date_added="nonsense")]
        )
        with self.assertRaises(WorkspaceImportError):
            workspace.import_workspace(json.dumps(payload), self.user)

    @disable_logging
    def test_import_skips_overlong_tag_name(self):
        long_name = "a" * 65
        payload = backup_payload(
            bookmarks=[
                bookmark_entry("https://example.com/x", tag_names=["okay", long_name])
            ]
        )
        workspace.import_workspace(json.dumps(payload), self.user)

        bookmark = Bookmark.objects.get(url="https://example.com/x")
        self.assertEqual(sorted(t.name for t in bookmark.tags.all()), ["okay"])
        self.assertFalse(Tag.objects.filter(name=long_name).exists())

    @disable_logging
    def test_validation_failure_aborts_whole_restore(self):
        # Pre-existing data that must survive a failed restore
        self.setup_bookmark(url="https://example.com/pre", title="Pre")

        payload = backup_payload(
            tags=[{"name": "newtag", "date_added": None}],
            bookmarks=[
                # Title exceeds the 512 char limit, failing field validation
                bookmark_entry("https://example.com/bad", title="x" * 600)
            ],
        )
        with self.assertRaises(ValidationError):
            workspace.import_workspace(json.dumps(payload), self.user)

        # Transaction rolled back: no partial writes
        self.assertFalse(Tag.objects.filter(name="newtag").exists())
        self.assertFalse(
            Bookmark.objects.filter(url="https://example.com/bad").exists()
        )
        self.assertEqual(Bookmark.objects.count(), 1)
