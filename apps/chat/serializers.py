"""Chat serializers for the REST history/read-receipt endpoints (doc 04 §8)."""

from rest_framework import serializers

from .models import ChatMessage, ChatRoom


class ChatRoomSerializer(serializers.ModelSerializer):
    society = serializers.SerializerMethodField()

    class Meta:
        model = ChatRoom
        fields = ['id', 'room_name', 'is_active', 'society', 'created_at']

    def get_society(self, obj):
        return {'id': obj.society_id, 'name': obj.society.name, 'subdomain': obj.society.subdomain}


class ChatMessageSerializer(serializers.ModelSerializer):
    sender = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = ['id', 'room', 'content', 'sender', 'created_at']
        read_only_fields = ['id', 'room', 'sender', 'created_at']

    def get_sender(self, obj):
        if not obj.sender_id:
            return {'id': None, 'name': 'Former resident'}
        user = obj.sender
        return {'id': user.id, 'name': user.get_full_name() or user.username, 'role': user.role}
