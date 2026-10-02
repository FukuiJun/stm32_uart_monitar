"""
旧形式のリリース zip (UartMonitor.zip) を新形式 (UartMonitor_vX.Y.Z.zip + VERSION.txt) に差し替える。
中の exe 等は当時ビルドしたものをそのまま使い、VERSION.txt を追加して zip 名を変えるだけ。
GitHub Actions の「Repackage old releases」から呼ぶ (gh CLI と GH_TOKEN が必要)。

使い方: python repackage_release.py <タグ> [<タグ> ...]
        タグを省略すると、UartMonitor.zip が付いている全リリースが対象。
"""

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from version_info import version_text  # noqa: E402

OLD_ASSET = "UartMonitor.zip"
NOTE = "※ この zip は、当時のリリースに VERSION.txt を追加して再パッケージしたものです (exe は当時のまま)。"


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def repackage_zip(old_zip, new_zip, version_txt):
    """old_zip の中身をそのままコピーし、最上位フォルダ(あれば)に VERSION.txt を足して new_zip に書く。"""
    with zipfile.ZipFile(old_zip) as src, zipfile.ZipFile(new_zip, "w", zipfile.ZIP_DEFLATED) as dst:
        names = src.namelist()
        sep = "\\" if any("\\" in n for n in names) else "/"  # 古い PowerShell は "\\" 区切りで書く
        tops = {n.split(sep)[0] for n in names}
        has_single_folder = len(tops) == 1 and all(sep in n for n in names)
        target = f"{tops.pop()}{sep}VERSION.txt" if has_single_folder else "VERSION.txt"
        if target in names:
            raise RuntimeError(f"{old_zip} already contains {target}")
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        dst.writestr(target, version_txt.encode("utf-8"))
    return target


def repackage_release(tag):
    assets = json.loads(gh("release", "view", tag, "--json", "assets"))["assets"]
    old = next((a for a in assets if a["name"] == OLD_ASSET), None)
    new_name = f"UartMonitor_{tag}.zip"
    if old is None:
        print(f"{tag}: {OLD_ASSET} なし。スキップ")
        return

    sha = gh("api", f"repos/{{owner}}/{{repo}}/commits/{tag}", "--jq", ".sha").strip()
    built = datetime.fromisoformat(old["createdAt"].replace("Z", "+00:00"))
    text = version_text(tag, sha, built, shows_in_app=False, note=NOTE)

    with tempfile.TemporaryDirectory() as tmp:
        gh("release", "download", tag, "--pattern", OLD_ASSET, "--dir", tmp)
        new_path = os.path.join(tmp, new_name)
        target = repackage_zip(os.path.join(tmp, OLD_ASSET), new_path, text)
        # 新しい zip を上げてから古い zip を消す (途中で失敗しても zip が無くならないように)
        gh("release", "upload", tag, new_path, "--clobber")
        gh("release", "delete-asset", tag, OLD_ASSET, "--yes")
    print(f"{tag}: {OLD_ASSET} -> {new_name} ({target} を追加)")
    print(text)


def main():
    tags = sys.argv[1:] or json.loads(gh("release", "list", "--limit", "100", "--json", "tagName"))
    for tag in tags:
        repackage_release(tag if isinstance(tag, str) else tag["tagName"])


if __name__ == "__main__":
    main()
