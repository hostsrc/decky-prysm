"""
Type stubs for the `decky` module available to Decky Loader plugins.
"""

DECKY_HOME: str
DECKY_PLUGIN_DIR: str
DECKY_PLUGIN_LOG_DIR: str
DECKY_PLUGIN_SETTINGS_DIR: str
DECKY_PLUGIN_RUNTIME_DIR: str
DECKY_PLUGIN_LOG: str
DECKY_PLUGIN_NAME: str
DECKY_PLUGIN_VERSION: str
DECKY_PLUGIN_AUTHOR: str
DECKY_USER: str
DECKY_USER_HOME: str

logger: "DeckyLogger"

class DeckyLogger:
    def info(self, msg: str) -> None: ...
    def debug(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...

async def emit(event: str, *args: object) -> None:
    """Emit an event to the frontend."""
    ...
