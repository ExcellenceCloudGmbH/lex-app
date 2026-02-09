class FilterTreeNode:
    def __init__(self, node_id, model_container, parent_to_self_fk_name, selection, children):
        self.node_id = node_id
        self.model_container = model_container
        self.parent_to_self_fk_name = parent_to_self_fk_name
        self.selection = selection
        self.children = children

        # QuerySet of objects filtered at this node
        self.filtered_objects = {}

    # Finds all currently filtered objects at this node and writes them into 'self.filtered_objects'
    def evaluate(self):
        # In case the current node has no more children, set 'self.filtered_objects' to
        #  'model.objects.all()'
        if not self.children:
            self.filtered_objects = self.model_container.model_class.objects.all()
            return

        child_to_selected_filtered_objects_dict = {}
        for child in self.children:
            child.evaluate()
            # Fill 'child_to_selected_filtered_objects_dict[child]' with
            #  'child.filtered_objects' intersected with the child's selection
            child_to_selected_filtered_objects_dict[
                child] = child.filtered_objects if child.selection == 'noSelection' else child.filtered_objects.filter(
                **{
                    self.model_container.pk_name + '__in': child.selection
                })

        # Aggregate all selected filtered objects for each child and filter accordingly
        filters = {
            child.parent_to_self_fk_name + '__in': child_to_selected_filtered_objects_dict[child]
            for child
            in self.children
        }
        self.filtered_objects = self.model_container.model_class.objects.filter(**filters)

    # Writes for this node an entry of type 'self.node_id -> list(pk) at that id filtered at that node'
    # into the passed dictionary
    def write_self_to_dict(self, d):
        d[self.node_id] = list(map(
            lambda obj: obj.pk,
            list(set(self.filtered_objects))
        ))

        for child in self.children:
            child.write_self_to_dict(d)
