# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Test-only desktop server with scriptable native dialogs (never packaged)."""
import argparse
from collections import deque
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeWindow:
    def __init__(self):
        self.dialogs = deque()
        self.confirmations = deque()
        self.calls = []
        self.on_top = False

    def create_file_dialog(self, kind, **kwargs):
        self.calls.append((kind, kwargs))
        return self.dialogs.popleft() if self.dialogs else None

    def create_confirmation_dialog(self, title, message):
        self.calls.append((title, message))
        return self.confirmations.popleft() if self.confirmations else False

    def set_title(self, title):
        self.calls.append(("title", title))

    def destroy(self):
        self.calls.append(("destroy",))
        sys.stdout.write("destroyed\n")
        sys.stdout.flush()

    def restore(self):
        self.calls.append(("restore",))

    def show(self):
        self.calls.append(("show",))


def make_fixture(directory):
    from PIL import Image

    directory = Path(directory)
    for name, regions in (("one", "a\n bounds: 1,2,3,2\nb\n bounds: 5,1,2,3\n"),
                          ("two", "c\n bounds: 1,2,3,2\n"),
                          ("missing", "a\n bounds: 1,2,3,2\n")):
        folder = directory / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.atlas").write_text(
            f"{name}.png\nsize: 9,7\npma: false\n{regions}"
            + ("\nother.png\nsize: 9,7\npma: false\nd\n bounds: 1,2,3,2\n"
               if name == "missing" else ""), encoding="utf-8")
        image = Image.new("RGBA", (9, 7))
        image.putdata([(10 + x * 4, 20 + y * 4, 30 + x + y, 255)
                       for y in range(7) for x in range(9)])
        if name == "missing":
            folder = directory / "missing-src"
            folder.mkdir(parents=True, exist_ok=True)
        image.save(folder / f"{name}.png")
    Image.new("RGBA", (3, 2), (180, 40, 90, 255)).save(directory / "mod.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=Path("dist/desktop"))
    parser.add_argument("--atlas")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--no-update-check", action="store_true")
    parser.add_argument("--fail-startup", action="store_true")
    parser.add_argument("--make-fixture", type=Path)
    args = parser.parse_args()
    if args.make_fixture:
        make_fixture(args.make_fixture)
        return
    if args.config is None:
        parser.error("--config is required when serving")
    args.config.mkdir(parents=True, exist_ok=True)
    from atlas_toolkit.app import config
    config.CONFIG_PATH = args.config / "config.json"
    from atlas_toolkit.app.desktop import build_desktop
    window = FakeWindow()
    urls = []

    def factory(url):
        urls.append(url)
        return window

    server, commands, _ = build_desktop(
        args.site, ["app"] + ([args.atlas] if args.atlas else []), None,
        factory, SimpleNamespace(OPEN=10, FOLDER=20, SAVE=30))
    if args.no_update_check:
        commands._updates.check_for_notification = lambda: None
    if args.fail_startup:
        def fail():
            raise RuntimeError("startup failed for test")
        commands.startup = fail
    print("URL " + urls[0], flush=True)
    try:
        for line in sys.stdin:
            line = line.rstrip("\n")
            if line == "quit":
                break
            if line.startswith("dialog "):
                window.dialogs.append(line[7:])
            elif line == "dialog-cancel":
                window.dialogs.append(None)
            elif line.startswith("confirm "):
                window.confirmations.append(line[8:] == "yes")
            elif line.startswith("drop "):
                parts = line[5:].rsplit(" ", 2)
                path, x, y = line[5:], None, None
                if len(parts) == 3:
                    try:
                        x, y = float(parts[1]), float(parts[2])
                        path = parts[0]
                    except ValueError:
                        pass
                commands.on_drop({"dataTransfer": {"files": [{"pywebviewFullPath": path}]},
                                  "clientX": x, "clientY": y})
            elif line == "close":
                print("vetoed" if commands.on_closing() is False else "closed", flush=True)
                continue
            elif line == "unsaved":
                print(str(server.unsaved).lower(), flush=True)
                continue
            else:
                raise ValueError("Unknown harness command")
            print("ok", flush=True)
    finally:
        server.stop()


if __name__ == "__main__":
    main()
