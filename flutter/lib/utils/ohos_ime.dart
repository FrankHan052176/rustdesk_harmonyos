import 'dart:math' as math;

import 'package:flutter/services.dart';

/// Soft-keyboard text handling for HarmonyOS input methods.
///
/// HarmonyOS IMEs (Pinyin, Kana, Hangul, ...) compose inside the client's
/// invisible text field and only commit a candidate later. The composing buffer
/// belongs to the local IME: sending it would type the raw buffer on the remote
/// and, because a candidate replaces that whole buffer, leave the leftovers
/// behind.

/// The text [value] has committed so far, that is its text without the
/// composing range the local IME still owns.
String committedSoftKeyboardText(TextEditingValue value) {
  final composing = value.composing;
  final text = value.text;
  if (!composing.isValid || composing.isCollapsed) {
    return text;
  }
  final start = math.min(math.max(composing.start, 0), text.length);
  final end = math.min(math.max(composing.end, start), text.length);
  return text.replaceRange(start, end, '');
}

/// The backspaces and the text that turn the committed text [previous] into
/// [current].
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
