"""Static file URLs that change whenever the file changes, so browsers never run stale CSS or JS."""
import os

from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage


class VersionedStaticStorage(StaticFilesStorage):
    def url(self, name):
        url = super().url(name)
        path = finders.find(name) or self.path(name)
        try:
            return f"{url}?v={int(os.path.getmtime(path))}"
        except OSError:
            return url
