from django.db.models import Model

from lex.core.models.process import Process
from lex.core.models.html_report import HTMLReport
from lex.process_admin.models.constants import RELATION_FIELD_TYPES


# TODO: 2
def get_relation_fields(model: Model):
    """Get relation fields of a model."""
    return [field for field in model._meta.get_fields() if
            field.get_internal_type() in RELATION_FIELD_TYPES and not field.one_to_many]

def title_for_model(model: Model) -> str:
    """Get the title for a model."""
    return model._meta.verbose_name.title()

def get_readable_name_for(node_name, model_collection, parent_styling):
    node_styling = parent_styling.get(node_name)

    if isinstance(node_styling, dict) and 'name' in node_styling:
        return node_styling['name']

    if node_name in model_collection.all_model_ids:
        return model_collection.get_container(node_name).title

    return node_name


def enrich_model_structure_with_readable_names_and_types(node_name, model_tree, model_collection, parent_styling=None):
    if parent_styling is None:
        parent_styling = model_collection.model_styling

    readable_name = get_readable_name_for(node_name, model_collection, parent_styling)

    current_node_styling = parent_styling.get(node_name, {})

    if not model_tree:
        model_class = model_collection.get_container(node_name).model_class
        node_type = "HTMLReport" if HTMLReport in model_class.__bases__ else "Process" if Process in model_class.__bases__ else "Model"
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
