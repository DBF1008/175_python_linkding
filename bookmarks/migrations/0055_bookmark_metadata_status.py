# Generated migration for metadata status tracking

from django.db import migrations, models


def backfill_metadata_status(apps, schema_editor):
    Bookmark = apps.get_model("bookmarks", "Bookmark")
    # Bookmarks with existing favicon files → complete
    Bookmark.objects.exclude(favicon_file="").update(favicon_status="complete")
    # Bookmarks with existing preview image files → complete
    Bookmark.objects.exclude(preview_image_file="").update(
        preview_image_status="complete"
    )


def reverse_backfill(apps, schema_editor):
    Bookmark = apps.get_model("bookmarks", "Bookmark")
    Bookmark.objects.all().update(favicon_status="", preview_image_status="")


class Migration(migrations.Migration):
    dependencies = [
        ("bookmarks", "0054_bookmarkbundle_filter_shared_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookmark",
            name="favicon_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Not active"),
                    ("pending", "Pending"),
                    ("complete", "Complete"),
                    ("failure", "Failure"),
                ],
                default="",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="bookmark",
            name="preview_image_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Not active"),
                    ("pending", "Pending"),
                    ("complete", "Complete"),
                    ("failure", "Failure"),
                ],
                default="",
                max_length=10,
            ),
        ),
        migrations.RunPython(
            backfill_metadata_status,
            reverse_backfill,
        ),
    ]
