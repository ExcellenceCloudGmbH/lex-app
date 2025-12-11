from django.contrib.postgres.search import SearchVector
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

from lex.process_admin.models.model_collection import ModelCollection
from lex.api.views.permissions.UserPermission import UserPermission

EXCLUDED_MODELS = {'calculationdashboard', 'user', 'group', 'permission', 'contenttype', 'userchangelog',
                   'calculationlog', 'log', 'streamlit'}
EXCLUDED_TYPES = {'FloatField', 'BooleanField', 'IntegerField', "FileField", "ForeignKey", "XLSXField", "PDFField", "ImageField"}


class Search(APIView):
    permission_classes = [HasAPIKey | IsAuthenticated]
    model_collection: ModelCollection = None

    def get(self, request, *args, **kwargs):
        query = self.kwargs['query']
        allMatches = []
        for model in self.model_collection.all_containers:
            temp_view = APIView(kwargs={'model_container': model})
            if model.id not in EXCLUDED_MODELS and UserPermission().has_permission(request=request, view=temp_view):
                fields = model.model_class._meta.get_fields(include_parents=False)
                tempMatch = model.model_class.objects.annotate(search=SearchVector(*[f.name for f in fields if
                                                                                     f.get_internal_type() not in EXCLUDED_TYPES])).filter(
                    search=query)
                for match in tempMatch:
                    if UserPermission().has_object_permission(request=request, view=temp_view, obj=match):
                        matchObj = {"id": str(match.pk), "type": model.title, "model": model.id,
                                    "url": f'/{model.id}/{match.pk}/show', "content": {
                                "id": str(match.pk),
                                "label": 'Model: ' + model.title ,
                                "description": str(match)}}
                        allMatches.append(matchObj)

        if allMatches:
            result = {"data": allMatches, "total": len(allMatches)}
            return Response(result)
        else:
            return Response("No match found")
