import 'package:flutter_hbb/utils/ohos_ime.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  // The soft keyboard starts on the sentinel the mobile page seeds the
  // controller with, so every case below diffs text that follows it.
  const sentinel = '1111';

  group('softKeyboardCommitDiff', () {
    test('a candidate replaces the Pinyin that was sent before it', () {
      // "a'a'a" becomes "啊啊啊" locally: the length-only diff this replaces
      // sent one VK_BACK and left "a'a'" on the remote.
      expect(
        softKeyboardCommitDiff("${sentinel}a'a'a", '$sentinel啊啊啊'),
        (backspaces: 5, text: '啊啊啊'),
      );
    });

    test('typing extends the text', () {
      expect(
        softKeyboardCommitDiff(sentinel, '${sentinel}ab'),
        (backspaces: 0, text: 'ab'),
      );
    });

    test('deleting only sends backspaces', () {
      expect(
        softKeyboardCommitDiff('${sentinel}ab', '${sentinel}a'),
        (backspaces: 1, text: ''),
      );
    });
  });
}
