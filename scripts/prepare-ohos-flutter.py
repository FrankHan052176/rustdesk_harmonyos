#!/usr/bin/env python3
"""Select Flutter-OH dependencies at build time; the committed pubspec stays the author's.

--check asserts the committed pubspec is still unprepared (the author's constraints).
"""

import argparse
import re
from pathlib import Path


# name, constraint in the committed pubspec, HarmonyOS fork, fork revision
GIT_DEPENDENCIES = (
    ('settings_ui', '^2.0.2', 'https://github.com/FrankHan052176/flutter-settings-ui',
     '79b5c4712fc4594e94aacd9e2bc2996d8de1fd5d'),
    ('flex_color_picker', '^3.3.0', 'https://github.com/FrankHan052176/flex_color_picker',
     '29d06e3368448ebd2eebdad4766d1cb4d688a0c2'),
    ('xterm', '4.0.0', 'https://github.com/FrankHan052176/xterm.dart',
     'e901d133ceba2b5e3a54f690cd45b4b0988ff158'),
)
# name, constraint in the committed pubspec, constraint Flutter-OH needs
VERSIONED_DEPENDENCIES = (
    # Flutter-OH 3.41 requires the newer SelectionHandler implementation.
    ('extended_text', '14.0.0', '^15.0.2'),
    # FontWeight has non-primitive equality in Flutter-OH 3.41.
    ('google_fonts', '^6.2.1', '^8.1.0'),
)


def git_block(name: str, url: str, ref: str) -> str:
    return f'  {name}:\n    git:\n      url: {url}\n      ref: {ref}'


def prepare(source: str) -> str:
    for name, constraint, url, ref in GIT_DEPENDENCIES:
        prepared = git_block(name, url, ref)
        pattern = rf'(?m)^(?:  {re.escape(name)}: {re.escape(constraint)}|{re.escape(prepared)})$'
        source, count = re.subn(pattern, lambda _: prepared, source)
        if count != 1:
            raise ValueError(f'Expected one supported {name} Git dependency')
    for name, constraint, selected in VERSIONED_DEPENDENCIES:
        pattern = rf'(?m)^  {re.escape(name)}: (?:{re.escape(constraint)}|{re.escape(selected)})$'
        source, count = re.subn(pattern, f'  {name}: {selected}', source)
        if count != 1:
            raise ValueError(f'Expected one supported {name} dependency')
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pubspec', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'flutter/pubspec.yaml')
    parser.add_argument('--check', action='store_true',
                        help='fail if the committed pubspec is already prepared for Flutter-OH')
    args = parser.parse_args()
    source = args.pubspec.read_text(encoding='utf-8')
    try:
        patched = prepare(source)
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}; pubspec was not changed\n')
    if args.check:
        if patched == source:
            parser.exit(1, 'The committed pubspec is already prepared for Flutter-OH; '
                           'keep the standard dependencies committed\n')
        print('The committed pubspec selects the standard dependencies.')
        return
    if patched != source:
        args.pubspec.write_text(patched, encoding='utf-8')
    print('Flutter-OH dependency constraints are ready.')


if __name__ == '__main__':
    main()
