"""Copy only public UI assets into the local extension, and make a clean ZIP."""
from pathlib import Path
import shutil
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent
ASSETS = ["index.html", "style.css", "app.js", "profile.js", "runner.js"]


def build():
    directory = ROOT / "extension" / "web"
    directory.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        shutil.copyfile(ROOT / "web" / name, directory / name)
    archive = ROOT / "connection-queue.zip"
    names = ["manifest.json", "background.js"] + ["web/" + name for name in ASSETS]
    with ZipFile(archive, "w", ZIP_DEFLATED) as handle:
        for name in names:
            handle.write(ROOT / "extension" / name, arcname=name)
    print(f"Extension folder: {ROOT / 'extension'}\nFirefox temporary add-on ZIP: {archive}")


if __name__ == "__main__":
    build()
