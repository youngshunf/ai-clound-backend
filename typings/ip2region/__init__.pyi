from typing import Any

class Searcher:
    def search(self, ip: str) -> str | None: ...

class _SearcherModule:
    def new_with_buffer(self, version: Any, content: bytes) -> Searcher: ...

class _UtilModule:
    IPv4: Any
    def load_content_from_file(self, path: str) -> bytes: ...

searcher: _SearcherModule
util: _UtilModule
