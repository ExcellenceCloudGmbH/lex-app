from django.db.models import Model
from lex.core.models.HTMLReport import HTMLReport
from lex.core.models.Process import Process
from lex.process_admin.models.constants import RELATION_FIELD_TYPES


# TODO: 2
def get_relation_fields(model: Model):
    """Get relation fields of a model."""
    return [field for field in model._meta.get_fields() if
            field.get_internal_type() in RELATION_FIELD_TYPES and not field.one_to_many]

def title_for_model(model: Model) -> str:
    """Get the title for a model."""
    return model._meta.verbose_name.title()


def get_node_styling(node_name, model_collection, parent_styling):
    node_styling = parent_styling.get(node_name)

    if isinstance(node_styling, dict):
        return node_styling

    global_node_styling = model_collection.model_styling.get(node_name)
    if isinstance(global_node_styling, dict):
        return global_node_styling

    return {}


def get_readable_name_for(node_name, model_collection, parent_styling):
    node_styling = get_node_styling(node_name, model_collection, parent_styling)

    if 'name' in node_styling:
        return node_styling['name']

    if node_name in model_collection.all_model_ids:
        return model_collection.get_container(node_name).title

    return node_name


def enrich_model_structure_with_readable_names_and_types(node_name, model_tree, model_collection, parent_styling=None):
    if parent_styling is None:
        parent_styling = model_collection.model_styling

    readable_name = get_readable_name_for(node_name, model_collection, parent_styling)

    current_node_styling = get_node_styling(node_name, model_collection, parent_styling)

    if not model_tree:
        model_class = model_collection.get_container(node_name).model_class
        node_type = "Model"
        if isinstance(model_class, type):
            if issubclass(model_class, HTMLReport):
                node_type = "HTMLReport"
            elif issubclass(model_class, Process):
                node_type = "Process"
        return {'readable_name': readable_name, 'type': node_type}

    return {
        'readable_name': readable_name,
        'type': "Folder",
        'children': {
            sub_node: enrich_model_structure_with_readable_names_and_types(
                sub_node, sub_tree, model_collection, current_node_styling
            )
            for sub_node, sub_tree in model_tree.items()
        }
    }
