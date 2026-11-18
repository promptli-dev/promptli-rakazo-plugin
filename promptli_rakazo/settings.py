"""Where the Rakazo side of the bridge lives: ~/.promptli/promptli-rakazo/rakazo.json (mode 600).

It holds the bot's webhook URL and key. $RAKAZO_WEBHOOK_URL and $RAKAZO_WEBHOOK_KEY override it (for
containers and secret managers). The key is never printed; status shows only whether it is set.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from promptli_gateway.config import home_dir

from promptli_rakazo.adapter import RUNTIME

URL_ENV = "RAKAZO_WEBHOOK_URL"
KEY_ENV = "RAKAZO_WEBHOOK_KEY"


def settings_path() -> Path:
    return home_dir(RUNTIME) / "rakazo.json"


@dataclass(frozen=True)
class RakazoSettings:
    webhook_url: str
    webhook_key: str

    @classmethod
    def load(cls) -> RakazoSettings | None:
        data: dict[str, str] = {}
        p = settings_path()
        if p.exists():
            data = json.loads(p.read_text())
        url = os.environ.get(URL_ENV) or data.get("webhook_url", "")
        key = os.environ.get(KEY_ENV) or data.get("webhook_key", "")
        return cls(url, key) if url and key else None

    def save(self) -> Path:
        p = settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"webhook_url": self.webhook_url, "webhook_key": self.webhook_key}, f)
        os.chmod(p, 0o600)
        return p

    def redacted(self) -> dict[str, str]:
        return {"webhook_url": self.webhook_url, "webhook_key": "set" if self.webhook_key else "missing"}
