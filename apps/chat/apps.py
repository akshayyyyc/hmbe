from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.chat'
    verbose_name = 'Community Chat'

    def ready(self):
        from . import signals  # noqa: F401  — registers the ChatRoom auto-creation
