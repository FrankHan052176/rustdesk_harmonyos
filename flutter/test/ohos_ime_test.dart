import 'package:flutter/services.dart';
import 'package:flutter_hbb/utils/ohos_ime.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  // The soft keyboard starts on the sentinel the mobile page seeds the
  // controller with, so every case below diffs text that follows it.
  const sentinel = '1111';

  TextEditingValue value(String text, {int start = -1, int end = -1}) =>
      TextEditingValue(
        text: text,
        selection: TextSelection.collapsed(offset: text.length),
        composing: TextRange(start: start, end: end),
      );

  group('committedSoftKeyboardText', () {
    test('drops the composing buffer of a Pinyin input', () {
      expect(
        committedSoftKeyboardText(value('${sentinel}nihao', start: 4, end: 9)),
        sentinel,
      );
    });

    test('keeps text that the IME has committed', () {
      expect(
        committedSoftKeyboardText(value('${sentinel}你好')),
        '${sentinel}你好',
      );
    });

    test('clamps a composing range that outruns the text', () {
      expect(
        committedSoftKeyboardText(value('${sentinel}ab', start: 4, end: 99)),
        sentinel,
      );
    });
  });

  group('softKeyboardCommitDiff', () {
    test('a candidate replaces the whole composing buffer', () {
      // "nihao" becomes "你好" locally: the length-only diff this replaces sent
      // one VK_BACK and left "niha" on the remote.
      expect(
        softKeyboardCommitDiff('${sentinel}nihao', '${sentinel}你好'),
        (backspaces: 5, text: '你好'),
      );
    });

    test('typing extends the committed text', () {
      expect(
        softKeyboardCommitDiff(sentinel, '${sentinel}ab'),
        (backspaces: 0, text: 'ab'),
      );
    });

    test('deleting committed text only sends backspaces', () {
      expect(
        softKeyboardCommitDiff('${sentinel}ab', '${sentinel}a'),
        (backspaces: 1, text: ''),
      );
    });

    test('a composing-only change never reaches the remote', () {
      expect(
        softKeyboardCommitDiff(sentinel, sentinel),
        (backspaces: 0, text: ''),
      );
    });
  });
}
