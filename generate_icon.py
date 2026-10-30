"""
generate_icon.py
================
app.ico を «採用したデザイン» の原画から作り直すスクリプト（r146）。

原画: resources/icons/app_icon_source.png（1024px・透過。2026-10-30 採用の X-3）
出力: resources/icons/app.ico（16〜256px のマルチサイズ）
      resources/icons/app_icon.png（256px。ウィンドウのアイコン用）

以前は Qt で «古いデザイン» を描いていたため、ビルド手順どおりに実行すると
新しいアイコンが古いものに戻ってしまう。原画から作るだけにした。

実行:
    python generate_icon.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
ICON_DIR = ROOT / "resources" / "icons"
SOURCE = ICON_DIR / "app_icon_source.png"
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def build(source=SOURCE, icon_dir=ICON_DIR):
    from PIL import Image
    src = Image.open(str(source)).convert("RGBA")
    ico = Path(icon_dir) / "app.ico"
    png = Path(icon_dir) / "app_icon.png"
    src.save(str(ico), format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    src.resize((256, 256), Image.LANCZOS).save(str(png))
    return ico, png


def main():
    if not SOURCE.is_file():
        print("source not found: %s" % SOURCE)
        return 1
    ico, png = build()
    print("ICO : %s" % ico)
    print("PNG : %s" % png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
