from storages.backends.azure_storage import AzureStorage

Static = lambda: AzureStorage(location="static")
Media = lambda: AzureStorage(location="uploads")
