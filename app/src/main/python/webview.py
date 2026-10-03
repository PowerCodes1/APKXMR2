"""
Fake pywebview module to satisfy `import webview` on Android without modifying original code.
"""

class Window:
    def __init__(self, *args, **kwargs):
        self.title = kwargs.get("title", "")
        self.url = kwargs.get("url", "")
        self.js_api = kwargs.get("js_api", None)

def create_window(*args, **kwargs):
    return Window(*args, **kwargs)

def start(*args, **kwargs):
    pass
