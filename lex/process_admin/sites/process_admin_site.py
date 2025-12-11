from django.db.models.base import ModelBase
from django.db.models.signals import post_save
from django.http import HttpResponse
from django.urls import path, register_converter

from lex.core.mixins.calculated import CalculatedModelMixin
from lex.process_admin.models.model_process_admin import ModelProcessAdmin
from lex.api.utils import create_model_converter
from lex.process_admin.models.model_collection import ModelCollection
from lex.core.signals import do_post_save
from lex.api.views.calculations.CleanCalculations import CleanCalculations
from lex.api.views.calculations.InitCalculationLogs import InitCalculationLogs
from lex.api.views.file_operations.FileDownload import FileDownloadView
from lex.api.views.file_operations.ModelExport import ModelExportView
from lex.api.views.global_search_for_models.Search import Search
from lex.api.views.model_entries.List import ListModelEntries
from lex.api.views.model_entries.Many import ManyModelEntries
from lex.api.views.model_entries.One import OneModelEntry
from lex.api.views.model_info.Fields import Fields
from lex.api.views.model_info.Widgets import Widgets
from lex.process_admin.views.model_relation_views import (
    ModelStructureObtainView,
    Overview,
    ProcessStructure,
)
from lex.api.views.permissions.ModelPermissions import ModelPermissions
from lex.api.views.process_flow.CreateOrUpdate import CreateOrUpdate
from lex.api.views.project_info.ProjectInfo import ProjectInfo
from lex.api.views.sharepoint.SharePointFileDownload import SharePointFileDownload
from lex.api.views.sharepoint.SharePointPreview import SharePointPreview
from lex.api.views.sharepoint.SharePointShareLink import SharePointShareLink
from lex.utilities.decorators.singleton import LexSingleton
from lex.api.views.LexLoggerView.LexLoggerView import LexLoggerView
from lex.api.views.model_entries.CalculationLogTreeView import CalculationLogTreeView
from lex.api.views.calculations.DownloadMarkdownPdf import DownloadMarkdownPdf
from lex.authentication.views.permissions import UserPermissionsView
from lex.authentication.views.token_views import StreamlitTokenView


@LexSingleton
class ProcessAdminSite:
    """
    Used as instance, i.e. inheriting this class is not necessary in order to use it.
    """

    name = "process_admin_rest_api"

    def __init__(self) -> None:
        super().__init__()

        self.registered_models = {}  # Model-classes to ModelProcessAdmin-instances
        self.model_structure = {}
        self.model_styling = {}
        self.html_reports = {}
        self.processes = {}
        self.widget_structure = []

        self.initialized = False
        self.model_collection = None

    def register_model_styling(self, model_styling):
        """
        :param model_styling: dict that contains styling parameters for each model
        """
        self.model_styling = model_styling

    def register_widget_structure(self, widget_structure):
        """
        :param model_styling: dict that contains styling parameters for each model
        """
        self.widget_structure = widget_structure

    def registerHTMLReport(self, name, report):
        self.html_reports[name] = report

    def registerProcess(self, name, process):
        self.processes[name] = process

    def register_model_structure(self, model_structure):
        """
        :param model_structure: multiple trees that structure the registered models, i.e. the leaves of the trees
        must correspond to the model-names, and all other nodes are interpreted as model categories.
        The roots have a special meaning, i.e. their categorization should be the most general one,
        and is represented in a special way.
        E.g.:
        {
            'Main_1': {
                'Sub_1_1': {
                    'Model_1_1_1': None,
                    'Model_1_1_2': None
                }
            },
            'Main2': {
                'Sub_2_1': {
                    'Model_2_1_1': None,
                    'Model_2_1_2': None
                },
                'Sub_2_2': {
                    'Model_2_2_1': None,
                    'Model_2_2_2': None
                }
            }
        }
        Hint: not every model has to be contained in this tree
        :return:
        """
        self.model_structure = model_structure

    def register(self, model_or_iterable, process_admin=None):
        if process_admin is None:
            process_admin = ModelProcessAdmin()

        if isinstance(model_or_iterable, ModelBase):
            model_or_iterable = [model_or_iterable]

        for model in model_or_iterable:
            if model in self.registered_models:
                # raise Exception('Model %s already registered' % model._meta.model_name)
                pass
            else:
                self.registered_models[model] = process_admin
                # TODO why was this in here in the first place?
                # if not issubclass(model, CalculatedModelMixin):
                post_save.connect(do_post_save, sender=model)

    def create_model_objects(self, request):
        for model in self.registered_models:
            if issubclass(model, CalculatedModelMixin):
                model.create()
        return HttpResponse("Created")

    def get_container_func(self, model_container):
        return self.model_collection.get_container(model_container)

    def get_model_structure_func(self):
        return self.model_collection.model_structure_with_readable_names

    def get_model_styling_func(self):
        return self.model_collection.model_styling

    def get_html_report_func(self, report_name, user):
        # Define the function that returns the HTML report
        return self.html_reports[report_name].get_html(user)

    def get_process_structure_func(self, process_name):
        return self.processes[process_name]()

    # TODO: Put urls definitions in the correct place (e.g. in a urls.py file)
    def _get_urls(self):
        register_converter(
            create_model_converter(self.model_collection), "model"
        )

        urlpatterns = [
            path(
                "api/model-structure",
                ModelStructureObtainView.as_view(
                    get_container_func=self.get_container_func,
                    get_model_structure_func=self.get_model_structure_func,
                ),
                name="model-structure",
            ),
            path(
                "api/<model:model_container>/file-download",
                FileDownloadView.as_view(model_collection=self.model_collection),
                name="file-download",
            ),
            path(
                "api/<model:model_container>/export",
                ModelExportView.as_view(model_collection=self.model_collection),
                name="model-export",
            ),
            path(
                "api/htmlreport/<str:report_name>",
                Overview.as_view(HTML_reports=self.html_reports),
                name="htmlreports",
            ),
            path(
                "api/process/<str:process_name>",
                ProcessStructure.as_view(processes=self.processes),
                name="process",
            ),
            path('api/auth/streamlit-token/', StreamlitTokenView.as_view(), name='streamlit_token'),

        ]

        url_patterns_for_react_admin = [
            path(
                "api/model_entries/<model:model_container>/list",
                ListModelEntries.as_view(),
                name="model-entries-list",
            ),
            path(
                "api/model_entries/<model:model_container>/<str:calculationId>/one/<int:pk>",
                OneModelEntry.as_view(),
                name="model-one-entry-read-update-delete",
            ),
            path(
                "api/model_entries/<model:model_container>/<str:calculationId>/one",
                OneModelEntry.as_view(),
                name="model-one-entry-create",
            ),
            path(
                "api/run_step/<model:model_container>/<str:pk>",
                CreateOrUpdate.as_view(),
                name="run_step",
            ),
            path(
                "api/model_entries/<model:model_container>/many",
                ManyModelEntries.as_view(),
                name="model-many-entries",
            ),
            path(
                "api/calculationlog/tree/",
                CalculationLogTreeView.as_view(),
                name="calculationlog-tree",
            ),
            path(
                "api/global-search/<str:query>",
                Search.as_view(model_collection=self.model_collection),
                name="global-search",
            ),
            path(
                "api/<model:model_container>/model-permissions",
                ModelPermissions.as_view(),
                name="model-restrictions",
            ),
            path("api/project-info", ProjectInfo.as_view(), name="project-info"),
            path("api/widget_structure", Widgets.as_view(), name="widget-structure"),
            path(
                "api/init-calculation-logs",
                InitCalculationLogs.as_view(),
                name="init-calculation-logs",
            ),
            path(
                "api/clean-calculations",
                CleanCalculations.as_view(),
                name="clean-calculations",
            ),
            path("api/logs", LexLoggerView.as_view(), name="log"),
            path(
                "api/download-pdf/<int:pk>/",
                DownloadMarkdownPdf.as_view(),
                name="download-markdown-pdf",
            ),
            path(
                "api/user_permissions/",
                UserPermissionsView.as_view(),
                name="user-permissions",
            ),
        ]

        url_patterns_for_model_info = [
            path(
                "api/model_info/<model:model_container>/fields",
                Fields.as_view(),
                name="model-info-fields",
            ),
        ]

        url_patterns_for_sharepoint = [
            path(
                "api/<model:model_container>/sharepoint-file-download",
                SharePointFileDownload.as_view(),
                name="sharepoint-file-download",
            ),
            path(
                "api/<model:model_container>/sharepoint-file-share-link",
                SharePointShareLink.as_view(),
                name="sharepoint-file-share-link",
            ),
            path(
                "api/<model:model_container>/sharepoint-file-preview-link",
                SharePointPreview.as_view(),
                name="sharepoint-file-preview-link",
            ),

        ]

        return (
            urlpatterns
            + url_patterns_for_react_admin
            + url_patterns_for_model_info
            + url_patterns_for_sharepoint
        )

    @property
    def urls(self):
        # TODO: Move this to a logically more appropriate place
        # TODO: remove tree induction
        if not self.initialized:
            self.model_collection = ModelCollection(
                self.registered_models, self.model_structure, self.model_styling
            )
            self.initialized = True

        return (
            self._get_urls(),
            "process_admin",
            self.name,
        )  # TODO: what is the name exactly for??
