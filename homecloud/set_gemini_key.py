"""Safely set GEMINI_API_KEY in the project .env without printing the key."""
from __future__ import annotations

import getpass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"


def update_env(path: Path, name: str, value: str):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    result = []
    replaced = False
    for line in lines:
        if line.strip().startswith(name + "="):
            result.append(f"{name}={value}")
            replaced = True
        else:
            result.append(line)
    if not replaced:
        result.append(f"{name}={value}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)


def main():
    key = getpass.getpass("請貼上 Gemini API Key（畫面不會顯示）：").strip()
    if len(key) < 20 or any(ch.isspace() for ch in key):
        raise SystemExit("API Key 格式不合理，未修改 .env。")
    update_env(ENV_PATH, "GEMINI_API_KEY", key)
    print(f"已保存到 {ENV_PATH}；請重新啟動居家照護服務。")


if __name__ == "__main__":
    main()

