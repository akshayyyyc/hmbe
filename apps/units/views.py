"""Unit registry endpoints, including CSV bulk import (doc 04 §6)."""

import logging

from django.db.models import Count, Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from apps.core.pagination import paginated
from apps.core.permissions import HasSociety, IsRWAAdmin, IsRWAAdminOrReadOnly
from apps.core.responses import error, ok, validation_error

from .models import Unit, Vehicle
from .serializers import UnitQuerySerializer, UnitSerializer, UnitWriteSerializer, VehicleSerializer
from .services import ImportError_, bulk_import_units, parse_upload, refresh_registered_units

logger = logging.getLogger(__name__)

ACTIVE_INVOICE_STATUSES = ['GENERATED', 'PARTIAL', 'OVERDUE']


def _tenant(request):
    """The caller's society — the only tenant a scoped caller can touch."""
    return request.user.society


def unit_list(request):
    """Units in the caller's society, with building/floor/occupancy filters (§6.1)."""
    query = UnitQuerySerializer(data=request.query_params)
    if not query.is_valid():
        return validation_error(query.errors)

    units = (
        Unit.objects.filter(society=_tenant(request), is_deleted=False)
        .annotate(vehicle_count=Count('vehicles', distinct=True))
        .order_by('building', 'floor', 'unit_number')
    )

    filters = query.validated_data
    if filters.get('building'):
        units = units.filter(building__iexact=filters['building'])
    if filters.get('floor') is not None:
        units = units.filter(floor=filters['floor'])
    if filters.get('type'):
        units = units.filter(unit_type=filters['type'])
    if filters.get('occupancy'):
        units = units.filter(occupancy_type=filters['occupancy'])
    search = filters.get('search')
    if search:
        units = units.filter(
            Q(unit_number__icontains=search)
            | Q(owner_name__icontains=search)
            | Q(tenant_name__icontains=search)
            | Q(parking_number__icontains=search)
        )

    return paginated(units, request, lambda rows: UnitSerializer(rows, many=True, context={'request': request}).data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, HasSociety])
def unit_collection(request):
    """``GET`` lists the registry, ``POST`` registers a unit (§6.1, §6.2).

    Both verbs live on ``/api/v1/units/``, which is why one view dispatches them;
    registering stays an RWA action, listing stays open to every member. The two
    handlers below are plain functions on purpose — wrapping them in ``@api_view``
    again would hand a DRF request to ``Request.__init__`` and blow up.
    """
    if request.method == 'GET':
        return unit_list(request)
    if not request.user.is_rwa_admin:
        return error('PERMISSION_DENIED', 'Only RWA admins can register units', status_code=403)
    return unit_create(request)


def unit_create(request):
    """Register a single unit (§6.2)."""
    serializer = UnitWriteSerializer(data=request.data, context={'request': request})
    if not serializer.is_valid():
        return validation_error(serializer.errors)

    unit = serializer.save(society=_tenant(request))
    refresh_registered_units(unit.society)
    return ok(UnitSerializer(unit, context={'request': request, 'unmask_contacts': True}).data, status_code=201)


@api_view(['GET', 'PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, HasSociety])
def unit_detail(request, pk):
    """Read by any society member; write and soft-delete by RWA admins (§6.3, §6.4)."""
    unit = Unit.objects.filter(pk=pk, society=_tenant(request), is_deleted=False).first()
    if not unit:
        return error('UNIT_NOT_FOUND', 'Unit not found', status_code=404)

    if request.method == 'GET':
        return ok(UnitSerializer(unit, context={'request': request}).data)

    if not request.user.is_rwa_admin:
        return error('PERMISSION_DENIED', 'Only RWA admins can change the unit registry', status_code=403)

    if request.method == 'DELETE':
        active = unit.invoices.filter(status__in=ACTIVE_INVOICE_STATUSES).exists()
        if active:
            return error(
                'UNIT_HAS_ACTIVE_INVOICES',
                'Settle or cancel this unit’s open invoices before removing it',
                status_code=409,
            )
        unit.soft_delete()
        refresh_registered_units(unit.society)
        return ok({'message': 'Unit removed', 'id': unit.id})

    serializer = UnitWriteSerializer(unit, data=request.data, partial=(request.method == 'PATCH'),
                                     context={'request': request})
    if not serializer.is_valid():
        return validation_error(serializer.errors)
    unit = serializer.save()
    refresh_registered_units(unit.society)
    return ok(UnitSerializer(unit, context={'request': request, 'unmask_contacts': True}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsRWAAdmin, HasSociety])
def bulk_import(request):
    """CSV upload to seed the registry in one go (§6.5).

    Row-level validation only — one bad row cannot lose the whole spreadsheet.
    """
    upload = request.FILES.get('file')
    if not upload:
        return validation_error({'file': ['Upload a CSV file in the "file" field']})

    try:
        rows = parse_upload(upload)
    except ImportError_ as exc:
        return error('INVALID_FILE', str(exc), status_code=400)

    result = bulk_import_units(_tenant(request), rows, UnitWriteSerializer)
    logger.info('Bulk unit import: %s', {k: result[k] for k in ('imported', 'failed')})

    if not result['imported']:
        return error(
            'NO_ROWS_IMPORTED',
            'No rows could be imported — see the per-row errors',
            status_code=400,
            details={'rows': result['errors']},
        )
    return ok(result, status_code=201)


# ── Vehicles (doc 03 §2.3) ────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, HasSociety])
def unit_vehicles(request, pk):
    """List or register vehicles for one unit."""
    unit = Unit.objects.filter(pk=pk, society=_tenant(request), is_deleted=False).first()
    if not unit:
        return error('UNIT_NOT_FOUND', 'Unit not found', status_code=404)

    if request.method == 'GET':
        vehicles = unit.vehicles.select_related('owner').order_by('-is_primary', 'vehicle_number')
        return ok(VehicleSerializer(vehicles, many=True).data)

    if not (request.user.is_rwa_admin or _unit_belongs_to_user(unit, request)):
        return error('PERMISSION_DENIED', 'Only RWA admins or the unit holder can add vehicles', status_code=403)

    data = {**request.data, 'unit': unit.id}
    serializer = VehicleSerializer(data=data, context={'request': request})
    if not serializer.is_valid():
        return validation_error(serializer.errors)
    # An admin registering a vehicle for a flat links it to that flat's account
    # when one exists; a resident adding their own vehicle links it to them.
    from apps.users.models import CustomUser

    owner = request.user
    if request.user.is_rwa_admin:
        owner = (
            CustomUser.objects.filter(phone=unit.tenant_phone or '').first()
            or CustomUser.objects.filter(phone=unit.owner_phone or '').first()
        )
    vehicle = serializer.save(unit=unit, owner=owner)
    return ok(VehicleSerializer(vehicle).data, status_code=201)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, HasSociety])
def vehicle_detail(request, vehicle_id):
    """Edit or remove a vehicle (RWA admin, or the unit holder on their own flat)."""
    vehicle = Vehicle.objects.filter(
        pk=vehicle_id, unit__society=_tenant(request), unit__is_deleted=False
    ).select_related('unit').first()
    if not vehicle:
        return error('VEHICLE_NOT_FOUND', 'Vehicle not found', status_code=404)
    if not (request.user.is_rwa_admin or _unit_belongs_to_user(vehicle.unit, request)):
        return error('PERMISSION_DENIED', 'Not your vehicle', status_code=403)

    if request.method == 'DELETE':
        vehicle.delete()
        return ok({'message': 'Vehicle removed', 'id': vehicle.id})

    serializer = VehicleSerializer(vehicle, data=request.data, partial=True)
    if not serializer.is_valid():
        return validation_error(serializer.errors)
    return ok(serializer.data)


def _unit_belongs_to_user(unit, request):
    """True when the caller's verified phone matches the unit's owner or tenant."""
    phone = getattr(request.user, 'phone', None)
    if not phone:
        return False
    return phone in {unit.owner_phone, unit.tenant_phone}
