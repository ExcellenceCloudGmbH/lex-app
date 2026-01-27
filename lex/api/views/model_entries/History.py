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
        history_qs = HistoryModel.objects.filter(**{pk_name: pk}).order_by('-valid_from', '-history_id')
        
        # 4. Serialize
        # We do custom serialization to include user info and key metadata clearly
        # We could use a serializer, but a simple dict comprehension is faster and more flexible for this mix of fields.
        
        data = []
        User = get_user_model()
        
        # Optimization: Prefetch users could be good if list is long, 
        # but history_user is already a foreign key, so select_related might be automatic or easy.
        # simple_history usually does raw generic or specific FK.
        
        for record in history_qs:
            # Prepare User Info
            user_info = None
            if record.history_user_id:
                # If we have the object available (simple_history usually prefetches if configured key)
                h_user = getattr(record, 'history_user', None)
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
                     # Fallback if FK relation didn't resolve but ID exists
                     user_info = {"id": record.history_user_id, "name": "Unknown User"}
                     
            # Snapshot Data: Exclude internal history fields
            snapshot = {}
            # We want the fields of the original model
            # Excluding history_* fields
            control_fields = {'history_id', 'valid_from', 'valid_to', 'history_type', 'history_change_reason', 'history_user', 'history_user_id', 'history_relation'}
            
            for field in record.__class__._meta.fields:
                 if field.name not in control_fields:
                      val = getattr(record, field.name)
                      # Basic serialization for JSON
                      if hasattr(val, 'isoformat'):
                          val = val.isoformat()
                      elif not isinstance(val, (str, int, float, bool, type(None), list, dict)):
                          # Fallback for unknown types (like Quarter object)
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
