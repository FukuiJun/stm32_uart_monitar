"""
ビルドのバージョン情報を書き出す (GitHub Actions から呼ぶ)。

使い方:
    python version_info.py py  <version> <出力先 .py>   # アプリが読む _version.py を生成
    python version_info.py txt <version> <出力先 .txt>  # 配布フォルダに置く VERSION.txt を生成

<version> はリリース時は "v1.2.3"、それ以外のビルドは "dev-<コミット短縮ハッシュ>"。
コミットとリポジトリは GitHub Actions の環境変数 GITHUB_SHA / GITHUB_SERVER_URL / GITHUB_REPOSITORY から取る。
"""

import os
import sys
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), "JST")


def write_py(version, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# ビルド時に .github/scripts/version_info.py が生成する。リポジトリには含めない\n")
        f.write(f"VERSION = {version!r}\n")


def write_txt(version, path):
    sha = os.environ.get("GITHUB_SHA", "")[:7] or "unknown"
    repo_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}"
    if version.startswith("v"):
        release = f"{repo_url}/releases/tag/{version}"
    else:
        release = "なし (開発ビルド。リリース版ではありません)"
    built = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    lines = [
        f"UartMonitor {version}",
        "",
        f"ビルド日時 : {built}",
        f"コミット   : {sha}",
        f"リリース   : {release}",
        "",
        "起動中のバージョンは、画面右上 (STM32L552VET6 の横) にも表示されます。",
    ]
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    kind, version, path = sys.argv[1:4]
    {"py": write_py, "txt": write_txt}[kind](version, path)
