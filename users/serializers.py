from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework import serializers


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ("id", "email", "password", "first_name", "last_name")
        read_only_fields = ("id",)
        extra_kwargs = {
            "password": {"write_only": True, "trim_whitespace": False},
        }

    def validate(self, attrs):
        password = attrs.get("password")
        if password is not None:
            user = get_user_model()(
                **{
                    field: attrs.get(field, getattr(self.instance, field, ""))
                    for field in ("email", "first_name", "last_name")
                }
            )
            try:
                validate_password(password, user=user)
            except ValidationError as error:
                raise serializers.ValidationError({"password": error.messages})
        return attrs

    def create(self, validated_data):
        return get_user_model().objects.create_user(**validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password is not None:
            instance.set_password(password)
        instance.save()
        return instance
