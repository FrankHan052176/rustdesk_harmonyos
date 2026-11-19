/// Soft-keyboard text handling for HarmonyOS input methods.
///
/// Flutter-OH reports no composing range: whatever the hidden text field holds
/// is text the input method has sent. It also replaces text it sent earlier (a
/// candidate replaces its Pinyin, a correction a word), so a change is a number
/// of backspaces followed by the new tail, never just an appended suffix.

/// The backspaces and the text that turn [previous] into [current].
({int backspaces, String text}) softKeyboardCommitDiff(
    String previous, String current) {
  var common = 0;
  while (common < previous.length &&
      common < current.length &&
      previous[common] == current[common]) {
    ++common;
  }
  return (
    backspaces: previous.length - common,
    text: current.substring(common),
  );
}
