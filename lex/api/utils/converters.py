def create_model_converter(model_collection):
    def to_python(self, value):
        return model_collection.get_container(value)

    def to_url(self, value):
        return value.id

    regex = '|'.join([id for id in model_collection.all_model_ids])

    converter = type('ModelConverter', (), dict(regex=regex))
    converter.to_python = to_python
    converter.to_url = to_url
    return converter