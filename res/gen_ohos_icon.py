#!/usr/bin/env python3
"""Copy the original RustDesk icon into the HarmonyOS application resources.

The source artwork is used unchanged, without rounding or an unofficial badge.
"""

import argparse
from pathlib import Path
import shutil

LOGO_TARGETS = (
    'AppScope/resources/base/media/app_icon.png',
    'entry/src/main/resources/base/media/icon.png',
    'entry/src/ohosTest/resources/base/media/icon.png',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument('--source', type=Path,
                        default=Path(__file__).resolve().parent / 'icon.png')
    parser.add_argument('--ohos', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'flutter/ohos')
    parser.add_argument('--preview', type=Path,
                        help='write a preview instead of the application resources')
    args = parser.parse_args()

    if args.preview:
        from PIL import Image
        with Image.open(args.source) as icon:
            icon.resize((256, 256), Image.LANCZOS).save(args.preview, format='PNG')
        print(args.preview)
        return

    for relative in LOGO_TARGETS:
        target = args.ohos / relative
        shutil.copyfile(args.source, target)
        print(f'{relative}: {target.stat().st_size} bytes')


if __name__ == '__main__':
    main()
