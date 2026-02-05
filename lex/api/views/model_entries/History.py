from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework_api_key.permissions import HasAPIKey
from rest_framework.exceptions import NotFound
from django.contrib.auth import get_user_model
from lex.api.views.permissions.UserPermission import UserPermission

class HistoryModelEntry(ListAPIView):
    """
    API View to retrieve the full bitemporal history timeline of a specific record.
    Returns chronological versions of the object.
    """
    permission_classes = [HasAPIKey | IsAuthenticated, UserPermission]

    def list(self, request, *args, **kwargs):
        # 1. Resolve Model and instance PK
        model_container = kwargs["model_container"]
        model_class = model_container.model_class
        pk = kwargs["pk"]
        
        # 2. Check Permissions (Read on Main Model)
        # We check if user can read the *current* version.
        try:
             # Basic existence check
             # Note: We might want to allow seeing history of deleted records?
             # For now, let's allow it even if main record is gone, as long as we have history.
             pass
        except Exception:
             pass

        if not hasattr(model_class, 'history'):
             return Response(
                 {"error": f"Model {model_class.__name__} does not track history."},
                 status=status.HTTP_400_BAD_REQUEST
             )
             
        HistoryModel = model_class.history.model
        
        # 3. Fetch History
        # Filter by the original object's PK (which is stored in the history model with the same name)
        pk_name = model_class._meta.pk.name
        
        from lex.core.services.bitemporal import get_queryset_as_of
        from django.utils.dateparse import parse_datetime

        as_of_param = request.query_params.get("as_of")
        if as_of_param:
            as_of_date = parse_datetime(as_of_param)
            if as_of_date:
                # Use bitemporal helper to get system time view (MetaModel records)
                try:
                    history_qs = get_queryset_as_of(HistoryModel, as_of_date)
                    
                    # Logic:
                    # 1. We want the list of HistoryVersions (Valid Time) that were known to the system at `as_of_date`.
                    # 2. Each HistoryVersion has a timeline of System Knowledge in MetaHistory.
                    # 3. MetaHistoryModel contains a copy of the fields of HistoryModel, including the business PK.
                    # So we can filter directly by `pk_name` on the MetaModel Querset.
                    
                    history_qs = history_qs.filter(**{pk_name: pk}).order_by('-valid_from')
                    
                except ValueError:
                    # Fallback if meta history not configured
                    history_qs = HistoryModel.objects.none()
            else:
                history_qs = HistoryModel.objects.filter(**{pk_name: pk}).order_by('-valid_from', '-history_id')
        else:
            history_qs = HistoryModel.objects.filter(**{pk_name: pk}).order_by('-valid_from', '-history_id')
        
        # 4. Serialize
        # ...
        
        data = []
        User = get_user_model()
        
        for record in history_qs:
            # If we are in 'as_of' mode, 'record' is a MetaHistory instance. 
            # It WRAPS the actual history_object (Valid Time Record).
            # But it MIGHT override fields if they were changed in system time (e.g. data correction).
            # MetaLevelHistoricalRecords.load_history_instance ensures we get a "hybrid" object?
            # Or we just use the Meta record which copies fields?
            
            # Use 'effective_record' abstraction
            if hasattr(record, 'history_object'):
                # This is a MetaHistory record
                # It contains copies of the fields at that system time.
                effective_record = record
                
                # We need to map 'history_user' from meta if reserved?
                # Meta usually has 'meta_history_user'.
                # The 'history_user' on Meta is likely the one who made the system change (meta_history_user).
                # The original 'history_user' of the business event should be on the record.
                
                # FIX: effective_record.history_user might be missing if not copied to Meta.
                # Let's check `get_extra_fields` in `model_registration.py`.
                # It does NOT seem to copy all fields by default? 
                # "latest.save()" updates fields in `trigger_meta_history`.
                # Yes, it copies `fields_included(instance)`.
                pass
            else:
                effective_record = record

            # Prepare User Info
            user_info = None
            
            # Determine which user to show? 
            # Usually we show the Valid-Time Author (who made the business change).
            # If `effective_record` is Meta, it has the fields copied.
            
            h_user_id = getattr(effective_record, 'history_user_id', None)
            
            if h_user_id:
                # ... fetch user ...
                h_user = getattr(effective_record, 'history_user', None)
                if h_user:
                     name_str = f"{getattr(h_user, 'first_name', '')} {getattr(h_user, 'last_name', '')}".strip()
                     if not name_str:
                         name_str = getattr(h_user, 'username', '') or getattr(h_user, 'email', '') or str(h_user).strip()
                     
                     user_info = {
                         "id": h_user.id,
                         "email": getattr(h_user, 'email', ''),
                         "name": name_str
                     }
                else:
                     user_info = {"id": h_user_id, "name": "Unknown User"}
                     
            # Snapshot Data
            # Use the registered serializer for the ModelContainer if available
            # This ensures we get computed fields like 'short_description' and 'lex_reserved_scopes'
            
            # Retrieves serializer class (e.g. QuarterSerializer)
            serializer_class = model_container.serializers_map.get('default')
            
            if serializer_class:
                # We need to construct a context that might be expected by the serializer
                # e.g. request, view
                context = {'request': request, 'view': self}
                
                # The serializer usually expects a Main Model instance.
                # 'effective_record' is a HistoryModel (or MetaModel) instance.
                # However, it has the attributes of the Main Model (name, etc.)
                # AND it has 'id' (business key) usually as a field or we map it.
                # For SimpleHistory, the history instance has an 'id' field which IS the business key.
                # So it should be compatible with the serializer for read-only fields.
                
                # We simply instantiate the serializer with the history record
                serializer = serializer_class(effective_record, context=context)
                snapshot = serializer.data
            else:
                # Fallback to manual serialization
                snapshot = {}
                control_fields = {
                    'history_id', 'valid_from', 'valid_to', 'history_type', 'history_change_reason', 
                    'history_user', 'history_user_id', 'history_relation',
                    'meta_history_id', 'sys_from', 'sys_to', 'meta_history_type', 'meta_history_change_reason',
                    'meta_history_user', 'meta_history_user_id', 'history_object', 'history_object_id',
                    'meta_task_name', 'meta_task_status', 'instance_type'
                }
                
                for field in record.__class__._meta.fields:
                     if field.name not in control_fields:
                          val = getattr(record, field.name)
                          if hasattr(val, 'isoformat'):
                              val = val.isoformat()
                          elif not isinstance(val, (str, int, float, bool, type(None), list, dict)):
                              val = str(val)
                          snapshot[field.name] = val
            
            # 5. Fetch Meta-History (System Time)
            system_history = []
            if hasattr(record, 'meta_history'):
                # meta_history is the related manager provided by simple_history on the HistoryModel
                # pointing to the MetaHistoryModel.
                # We sort by sys_from desc to show latest system knowledge first.
                for meta in record.meta_history.all().order_by('-sys_from'):
                    system_history.append({
                        "sys_from": meta.sys_from,
                        "sys_to": meta.sys_to,
                        "task_status": getattr(meta, 'meta_task_status', 'NONE'),
                        "task_name": getattr(meta, 'meta_task_name', None),
                        "change_reason": getattr(meta, 'meta_history_change_reason', None),
                    })

            entry = {
                "history_id": record.history_id,
                "valid_from": record.valid_from,
                "valid_to": record.valid_to,
                "history_type": record.history_type, # +, ~, -
                "change_reason": record.history_change_reason,
                "user": user_info,
                "snapshot": snapshot,
                "system_history": system_history
            }
            data.append(entry)
            
        return Response(data)
