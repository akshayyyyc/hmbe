"""Digital notice board model."""

from django.db import models
from django.conf import settings


class Notice(models.Model):
    """A statutory / community notice pinned to the digital notice board."""

    class NoticeCategory(models.TextChoices):
        EMERGENCY = 'EMERGENCY', 'Emergency'
        GENERAL = 'GENERAL', 'General'
        MEETING = 'MEETING', 'Meeting'
        EVENT = 'EVENT', 'Event'
        POLICY = 'POLICY', 'Policy'

    society = models.ForeignKey(
        'society.Society', on_delete=models.CASCADE, related_name='notices'
    )
    title = models.CharField(max_length=255)
    content = models.TextField()
    category = models.CharField(max_length=20, choices=NoticeCategory)
    is_pinned = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)  # auto-archive after expiry
    attachment = models.FileField(upload_to='notices/', null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_notices',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notice'
        ordering = ['-is_pinned', '-created_at']
        indexes = [
            models.Index(fields=['society', '-created_at']),
            models.Index(fields=['society', 'is_pinned']),
        ]

    def __str__(self):
        return f"{self.society.name} – {self.title}"
