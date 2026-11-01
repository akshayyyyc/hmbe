"""Every society gets exactly one chat room, created with the society."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.society.models import Society

from .models import ChatRoom


@receiver(post_save, sender=Society)
def ensure_chat_room(sender, instance, created, **kwargs):
    if created:
        ChatRoom.objects.get_or_create(society=instance)
