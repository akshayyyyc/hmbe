"""Unit registry and vehicle serializers (doc 04 §6)."""

from rest_framework import serializers

from apps.users.utils import normalize_phone

from .models import Unit, Vehicle


class UnitSerializer(serializers.ModelSerializer):
    """Read shape — includes derived fields the dashboards and lists need."""

    full_unit_id = serializers.CharField(read_only=True)
    vehicle_count = serializers.IntegerField(read_only=True)
    tenant_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    owner_phone = serializers.SerializerMethodField()
    owner_email = serializers.SerializerMethodField()
    tenant_phone = serializers.SerializerMethodField()
    tenant_email = serializers.SerializerMethodField()

    class Meta:
        model = Unit
        fields = [
            'id', 'building', 'floor', 'unit_number', 'full_unit_id', 'unit_type',
            'area_sqft', 'occupancy_type', 'is_rented', 'base_maintenance',
            'owner_name', 'owner_phone', 'owner_email',
            'tenant_name', 'tenant_phone', 'tenant_email',
            'parking_number', 'vehicle_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'is_rented']

    # Neighbours may see that a flat is occupied and by which name, but not the
    # resident's phone or email — only RWA admins get unmasked contact details.
    def get_owner_phone(self, obj):
        return _mask_phone(obj.owner_phone, self.context)

    def get_owner_email(self, obj):
        return _mask_email(obj.owner_email, self.context)

    def get_tenant_phone(self, obj):
        return _mask_phone(obj.tenant_phone, self.context)

    def get_tenant_email(self, obj):
        return _mask_email(obj.tenant_email, self.context)


def _contacts_visible(context):
    request = context.get('request')
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return bool(getattr(user, 'is_rwa_admin', False)) or context.get('unmask_contacts')


def _mask_phone(value, context):
    """Keep only the last four digits — enough to recognise a number, not to dial it."""
    if not value or _contacts_visible(context):
        return value
    digits = ''.join(ch for ch in value if ch.isdigit())
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    return f'***{digits[-4:]}' if len(digits) >= 7 else '***'


def _mask_email(value, context):
    if not value or _contacts_visible(context):
        return value
    name, _, domain = value.partition('@')
    return f'{name[:1]}***@{domain}' if domain else '***'


class UnitWriteSerializer(serializers.ModelSerializer):
    """Create/update input. ``society`` is never taken from the client."""

    class Meta:
        model = Unit
        fields = [
            'building', 'floor', 'unit_number', 'unit_type', 'area_sqft',
            'occupancy_type', 'base_maintenance',
            'owner_name', 'owner_phone', 'owner_email',
            'tenant_name', 'tenant_phone', 'tenant_email',
            'parking_number',
        ]

    def validate_building(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Building is required')
        return value

    def validate_unit_number(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Unit number is required')
        return value

    def validate_owner_name(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Owner name is required')
        return value

    def validate_owner_phone(self, value):
        normalized = normalize_phone(value)
        if not normalized:
            raise serializers.ValidationError('Enter a valid Indian mobile number')
        return normalized

    def validate_tenant_phone(self, value):
        if not value:
            return None
        normalized = normalize_phone(value)
        if not normalized:
            raise serializers.ValidationError('Enter a valid Indian mobile number')
        return normalized

    def _society(self):
        """Tenant for the row: the instance's society, else request/explicit context.

        The CSV importer passes ``society`` directly (there is no request there),
        so both paths have to resolve.
        """
        if self.instance:
            return self.instance.society
        society = self.context.get('society')
        if society:
            return society
        return getattr(self.context.get('request'), 'society', None)

    def validate(self, attrs):
        instance = self.instance
        society = self._society()
        if society is None:
            raise serializers.ValidationError('No society context for this unit')
        building = attrs.get('building', getattr(instance, 'building', ''))
        unit_number = attrs.get('unit_number', getattr(instance, 'unit_number', ''))

        # Mirror the DB constraint exactly: it is conditional on is_deleted=False,
        # so a soft-deleted flat must be re-registerable (doc 04 §6.4). Checking
        # live rows only is what keeps that promise at the API boundary.
        clash = Unit.objects.filter(
            society=society, building__iexact=building, unit_number__iexact=unit_number,
            is_deleted=False,
        )
        if instance:
            clash = clash.exclude(pk=instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                {'unit_number': f'Unit {building}-{unit_number} already exists in this society'}
            )

        occupancy = attrs.get('occupancy_type', getattr(instance, 'occupancy_type', None))
        if occupancy == Unit.OccupancyType.RENTED and not (
            attrs.get('tenant_phone') or attrs.get('tenant_name') or getattr(instance, 'tenant_phone', None)
        ):
            raise serializers.ValidationError(
                {'tenant_phone': 'A rented unit needs tenant name and phone'}
            )
        return attrs

    def save(self, **kwargs):
        """Keep the derived ``is_rented`` flag in step with occupancy type."""
        occupancy = self.validated_data.get('occupancy_type')
        if occupancy is not None:
            self.validated_data['is_rented'] = occupancy == Unit.OccupancyType.RENTED
        return super().save(**kwargs)


class VehicleSerializer(serializers.ModelSerializer):
    unit_label = serializers.CharField(source='unit.full_unit_id', read_only=True)
    owner_name = serializers.CharField(source='owner.get_full_name', read_only=True)

    class Meta:
        model = Vehicle
        fields = [
            'id', 'unit', 'unit_label', 'owner', 'owner_name', 'vehicle_type',
            'vehicle_number', 'color', 'is_primary', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def validate_vehicle_number(self, value):
        value = (value or '').strip().upper().replace(' ', '')
        if not value:
            raise serializers.ValidationError('Vehicle number is required')
        return value

    def validate(self, attrs):
        unit = attrs.get('unit') or (self.instance.unit if self.instance else None)
        number = attrs.get('vehicle_number') or (self.instance.vehicle_number if self.instance else None)
        clash = Vehicle.objects.filter(unit=unit, vehicle_number__iexact=number or '')
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if unit and number and clash.exists():
            raise serializers.ValidationError({'vehicle_number': 'This vehicle is already registered to the unit'})
        return attrs


class UnitQuerySerializer(serializers.Serializer):
    """Filters for ``GET /api/v1/units/`` (§6.1)."""

    building = serializers.CharField(required=False)
    floor = serializers.IntegerField(required=False)
    type = serializers.CharField(required=False)
    occupancy = serializers.CharField(required=False)
    search = serializers.CharField(required=False)
