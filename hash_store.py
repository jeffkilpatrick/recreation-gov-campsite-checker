import hashlib
import json
import os

from pathlib import Path
from typing import Dict

class HashStore:
    STORE_FILE = Path(os.environ["HOME"]) / ".campsite-checker-hashes.json"

    __hashes: Dict[str, Dict[str, str]]

    def __init__(self) -> None:
        self.__hashes = {}
        if self.STORE_FILE.exists():
            self.__hashes = json.loads(self.STORE_FILE.read_text())

    def save(self) -> None:
        self.STORE_FILE.write_text(json.dumps(self.__hashes))

    def check_and_save(self, name: str, content: str) -> bool:
        old_hash = ""
        if name in self.__hashes:
            old_hash = self.__hashes[name].get("sha256", "")
        else:
            self.__hashes[name] = {}
        new_hash = hashlib.sha256(content.encode()).hexdigest()
        self.__hashes[name]['sha256'] = new_hash
        self.save()
        return old_hash != new_hash