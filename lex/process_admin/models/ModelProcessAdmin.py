from lex.utilities.decorators.singleton import LexSingleton


def subtract_from_list(minuend_list, subtrahend_set):
    return [e for e in minuend_list if e not in subtrahend_set]


def is_excluded(field):
    return field.auto_created


def get_all_fields(model):
    return [field.name for field in model._meta.get_fields()]


def get_displayed_fields(model):
    return [field.name for field in model._meta.get_fields() if not is_excluded(field)]


# I made all the models that can be singleton to singleton
# Beware there might be an expected behavior if the logic is reliant of creating a new object everytime
@LexSingleton
class ModelProcessAdmin:
    def __init__(
        self,
        to_display_string=None,
        fields_not_in_table_view=None,
        main_field=None,
        allow_quick_instance_creation=True,
    ) -> None:
        """
        :param to_display_string: can be set to a function that is applied to each instance
        for getting the display string for it. If None, the str-method is used
        :param fields_not_in_table_view: list of field names, that should not be displayed in the data table
        for this model. This dominates the parameter fields_in_table_view. Default: empty set.
        :param main_field: can be set to the name of the main field of the model.
        The main field links to the update-/details-view of an instance. Default: the first field defined in the model.
        :param allow_quick_instance_creation: whether there should be an instance-add-button for a model
        in the tree-view that represents the model structure
        """
        super().__init__()

        if to_display_string is None:
            to_display_string = lambda i: i.__str__()

        if fields_not_in_table_view is None:
            fields_not_in_table_view = []

        self.to_display_string = to_display_string
        self.fields_not_in_table_view = set(fields_not_in_table_view)
        self.main_field = main_field

        self._models2fields_in_table_view = dict()

        self._allow_quick_instance_creation = allow_quick_instance_creation

    def _create_fields_in_table_view(self, model):
        fields = get_displayed_fields(model)
        fields_not_in_table_view = self.fields_not_in_table_view.difference(
            {model._meta.pk.name}
        )
        result = subtract_from_list(fields, fields_not_in_table_view)
        self._models2fields_in_table_view[model] = result
        return result

    def get_fields_in_table_view(self, model):
        if model in self._models2fields_in_table_view:
            return self._models2fields_in_table_view[model]
        else:
            return self._create_fields_in_table_view(model)

    def get_main_field(self, model):
        if self.main_field is not None:
            return self.main_field
        else:
            return get_displayed_fields(model)[0]

    def allow_quick_instance_creation(self, model):
        if self._allow_quick_instance_creation:
            return True
