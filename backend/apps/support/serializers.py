"""Validation for support tickets and user feedback."""
from __future__ import annotations

from rest_framework import serializers

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

CATEGORIES = ("bug", "data_request", "access", "feature", "other")
PRIORITIES = ("low", "medium", "high", "urgent")
TICKET_STATUSES = ("open", "in_progress", "resolved", "closed")
FEEDBACK_STATUSES = ("new", "reviewed", "actioned")


class TicketCreateSerializer(serializers.Serializer):
    subject = serializers.CharField(max_length=200)
    description = serializers.CharField(max_length=8000)
    category = serializers.ChoiceField(choices=CATEGORIES, required=False, default="other")
    priority = serializers.ChoiceField(choices=PRIORITIES, required=False, default="medium")
    page = serializers.CharField(required=False, allow_blank=True, max_length=200)
    page_context = serializers.CharField(required=False, allow_blank=True, max_length=200)
    attached_dataset_id = serializers.CharField(required=False, allow_blank=True, max_length=24)

    def validate_subject(self, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise serializers.ValidationError("Give the ticket a subject of at least 5 characters.")
        return value

    def validate_description(self, value: str) -> str:
        value = value.strip()
        if len(value) < 10:
            raise serializers.ValidationError(
                "Describe what happened (at least 10 characters) so it can be triaged.")
        return value

    def validated_page(self) -> str:
        return (self.validated_data.get("page")
                or self.validated_data.get("page_context") or "")


class TicketUpdateSerializer(serializers.Serializer):
    """Staff workflow fields. Role restrictions are applied by the view, not here."""

    status = serializers.ChoiceField(required=False, choices=TICKET_STATUSES)
    priority = serializers.ChoiceField(required=False, choices=PRIORITIES)
    category = serializers.ChoiceField(required=False, choices=CATEGORIES)
    assigned_to = serializers.CharField(required=False, allow_null=True, allow_blank=True,
                                        max_length=24)
    subject = serializers.CharField(required=False, max_length=200)
    description = serializers.CharField(required=False, max_length=8000)

    def validate_assigned_to(self, value):
        if value in (None, ""):
            return None
        value = str(value).strip()
        if not value:
            return None
        if len(value) != 24 or not all(character in "0123456789abcdefABCDEF" for character in value):
            raise serializers.ValidationError("assigned_to must be an account identifier.")
        return value

    def validate_subject(self, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise serializers.ValidationError("The subject is too short.")
        return value


class TicketCommentSerializer(serializers.Serializer):
    """``body`` is the web client's name; ``message`` is accepted as a synonym."""

    body = serializers.CharField(required=False, max_length=8000)
    message = serializers.CharField(required=False, max_length=8000)
    comment = serializers.CharField(required=False, max_length=8000)
    internal = serializers.BooleanField(required=False, default=False)

    def validated_body(self) -> str:
        for key in ("body", "message", "comment"):
            value = self.validated_data.get(key)
            if value:
                return str(value).strip()
        raise serializers.ValidationError({"body": "A comment body is required."})

    def validate_internal(self, value: bool) -> bool:
        return bool(value)


class TicketCloseSerializer(serializers.Serializer):
    resolution = serializers.CharField(required=False, max_length=4000)
    note = serializers.CharField(required=False, max_length=4000)
    status = serializers.ChoiceField(required=False, choices=("resolved", "closed"),
                                     default="closed")

    def validated_note(self) -> str:
        return (self.validated_data.get("resolution")
                or self.validated_data.get("note") or "").strip()


class FeedbackCreateSerializer(serializers.Serializer):
    rating = serializers.IntegerField(min_value=1, max_value=5)
    category = serializers.ChoiceField(choices=CATEGORIES, required=False, default="other")
    area = serializers.ChoiceField(choices=CATEGORIES, required=False)
    message = serializers.CharField(required=False, max_length=8000)
    comment = serializers.CharField(required=False, max_length=8000)
    page_context = serializers.CharField(required=False, allow_blank=True, max_length=200)

    def validated_message(self) -> str:
        text = (self.validated_data.get("message") or self.validated_data.get("comment") or "")
        text = str(text).strip()
        if len(text) < 3:
            raise serializers.ValidationError(
                {"message": "Tell us what you think (at least 3 characters)."})
        return text

    def validated_category(self) -> str:
        return (self.validated_data.get("category") or self.validated_data.get("area")
                or "other")


class FeedbackUpdateSerializer(serializers.Serializer):
    """Administrator triage fields only."""

    status = serializers.ChoiceField(required=False, choices=FEEDBACK_STATUSES)
    reply = serializers.CharField(required=False, max_length=4000)
    administrator_reply = serializers.CharField(required=False, max_length=4000)
    category = serializers.ChoiceField(required=False, choices=CATEGORIES)

    def validated_reply(self) -> str:
        return (self.validated_data.get("reply")
                or self.validated_data.get("administrator_reply") or "").strip()
