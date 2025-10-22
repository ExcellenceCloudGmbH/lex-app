class ObjectsToRecalculateStore:
    instance = None

    def __init__(self):
        # calculated_model_id --> (defining_field_tuple --> object_to_recalculate)
        self.objects_to_recalculate = {}
        ObjectsToRecalculateStore.instance = self

    @staticmethod
    def insert(obj):
        model_id = obj._meta.model_name
        defining_fields = tuple(map(lambda field: obj.__getattribute__(field.name), obj.defining_fields))
        o2r = ObjectsToRecalculateStore.instance.objects_to_recalculate

        if model_id not in o2r:
            o2r[model_id] = {}

        if defining_fields not in o2r[model_id]:
            o2r[model_id][defining_fields] = obj

        ObjectsToRecalculateStore.instance.objects_to_recalculate = o2r

    @staticmethod
    def do_recalculations():
        o2r = ObjectsToRecalculateStore.instance.objects_to_recalculate
        for model_id in o2r.keys():
            for def_field_tuple in o2r[model_id].keys():
                obj = o2r[model_id][def_field_tuple]
                obj.calculate()
                obj.save()

        ObjectsToRecalculateStore.instance.objects_to_recalculate = {}
