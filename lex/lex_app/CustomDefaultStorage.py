from urllib.parse import urljoin

from django.core.files.storage import FileSystemStorage


class CustomDefaultStorage(FileSystemStorage):
    def url(self, name):
        if self.base_url is None:
            raise ValueError("This file is not accessible via a URL.")
        url = name
        if url is not None:
            url = url.lstrip("/")
        return urljoin(self.base_url, url)