import os
from os.path import exists
from django.conf import settings
from django.core.files import File
from django.core.files.storage import Storage

ROOT_DIR = os.path.join(settings.BASE_DIR, 'creature/images/')

class ImagesStorage(Storage):
    def __init__(self, option=None):
        if not option:
            option = settings.CUSTOM_STORAGE_OPTIONS
    def _open(self, name, mode='rb'):
        file = os.path.join(ROOT_DIR, name)
        if exists(file):
            return File(open(file, 'r'))
    def _save(self, name, content):
        file = os.path.join(ROOT_DIR, name)
        if exists(file):
            with open(file, "w") as f:
                f.write(content) 
            return name
    def path():
