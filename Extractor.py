# Dummy Extractor (Render Safe)

class Extractor:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None
