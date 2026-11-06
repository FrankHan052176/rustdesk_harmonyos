import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_hbb/models/ohos_video_texture.dart';
import 'package:flutter_test/flutter_test.dart';

const _session = 'session-a';

OhosVideoTarget _target({
  int display = 0,
  int width = 1920,
  int height = 1080,
}) =>
    OhosVideoTarget(
      sessionId: _session,
      display: display,
      width: width,
      height: height,
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late List<MethodCall> calls;
  late Future<Object?> Function(MethodCall call) respond;
  late int nextTextureId;
  late int surfaceFrames;
  late OhosVideoTextureModel model;

  List<MethodCall> callsTo(String method) =>
      calls.where((call) => call.method == method).toList();
  Object? argOf(MethodCall call, String key) => (call.arguments as Map)[key];

  setUp(() {
    calls = <MethodCall>[];
    nextTextureId = 7;
    surfaceFrames = 0;
    respond = (call) async => call.method == 'createVideoTexture'
        ? {'textureId': nextTextureId++}
        : true;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(OhosVideoTextureModel.platformChannel,
            (call) async {
      calls.add(call);
      return respond(call);
    });
    model = OhosVideoTextureModel(
      sessionId: _session,
      onSurfaceFrame: () async {
        surfaceFrames++;
      },
    );
  });

  tearDown(() {
    model.dispose();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(OhosVideoTextureModel.platformChannel, null);
  });

  test('binds the shown display to a freshly created texture', () async {
    model.showTarget(_target());
    await pumpEventQueue();

    expect(callsTo('createVideoTexture').single.arguments,
        {'width': 1920, 'height': 1080});
    final register = callsTo('registerVideoTexture').single;
    expect(argOf(register, 'sessionId'), _session);
    expect(argOf(register, 'display'), 0);
    expect(argOf(register, 'textureId'), 7);
    expect(argOf(register, 'width'), 1920);
    expect(argOf(register, 'height'), 1080);
    expect(model.textureId, 7);
    // Registering proves nothing about what reaches the screen.
    expect(model.isSurfaceOutput(0), isFalse);
  });

  test('an unchanged target never reaches the core again', () async {
    model.showTarget(_target());
    await pumpEventQueue();
    expect(calls.length, 2);

    model.showTarget(_target());
    await pumpEventQueue();
    expect(calls.length, 2);
  });

  test('another display unbinds the session before it binds again', () async {
    model.showTarget(_target());
    await pumpEventQueue();
    calls.clear();

    model.showTarget(_target(display: 1));
    await pumpEventQueue();

    expect(calls.map((call) => call.method),
        ['detachVideoSurfaces', 'registerVideoTexture']);
    // One texture belongs to one session, whichever display it shows.
    expect(callsTo('createVideoTexture'), isEmpty);
    expect(argOf(calls.last, 'display'), 1);
    expect(argOf(calls.last, 'textureId'), 7);
  });

  test('closing unbinds before it destroys, and only once', () async {
    model.showTarget(_target());
    await pumpEventQueue();
    calls.clear();

    await model.close();
    expect(calls.map((call) => call.method),
        ['detachVideoSurfaces', 'disposeVideoTexture']);
    expect(argOf(calls.last, 'textureId'), 7);
    expect(model.textureId, -1);

    await model.close();
    expect(calls.length, 2);
  });

  test('leaving the screen keeps the texture and coming back binds it again',
      () async {
    model.showTarget(_target());
    await pumpEventQueue();
    calls.clear();

    model.didChangeAppLifecycleState(AppLifecycleState.paused);
    await pumpEventQueue();
    expect(calls.map((call) => call.method), ['detachVideoSurfaces']);
    expect(model.textureId, 7);
    expect(callsTo('disposeVideoTexture'), isEmpty);

    calls.clear();
    model.didChangeAppLifecycleState(AppLifecycleState.resumed);
    await pumpEventQueue();
    expect(calls.map((call) => call.method), ['registerVideoTexture']);
    expect(argOf(calls.single, 'textureId'), 7);
    expect(callsTo('createVideoTexture'), isEmpty);
  });

  test('a texture created after the page closed is still destroyed', () async {
    final created = Completer<Object?>();
    respond = (call) => call.method == 'createVideoTexture'
        ? created.future
        : Future<Object?>.value(true);

    model.showTarget(_target());
    await pumpEventQueue();
    final closing = model.close();
    await pumpEventQueue();

    created.complete({'textureId': 11});
    await closing;

    expect(callsTo('registerVideoTexture'), isEmpty);
    expect(callsTo('disposeVideoTexture').single.arguments, {'textureId': 11});
  });

  test('output reports count only for the bound display', () async {
    model.showTarget(_target());
    await pumpEventQueue();

    await model.handleOutputEvent({'display': '0', 'surface': 'true'});
    expect(model.isSurfaceOutput(0), isTrue);
    expect(surfaceFrames, 1);

    // A report about a registration this page already replaced.
    await model.handleOutputEvent({'display': '1', 'surface': 'false'});
    expect(model.isSurfaceOutput(0), isTrue);
    expect(surfaceFrames, 1);

    await model.handleOutputEvent({'display': '0', 'surface': 'false'});
    expect(model.isSurfaceOutput(0), isFalse);
    expect(surfaceFrames, 1);
  });

  test('a detach the core did not confirm keeps the texture alive', () async {
    model.showTarget(_target());
    await pumpEventQueue();
    respond = (call) => call.method == 'detachVideoSurfaces'
        ? Future<Object?>.error(PlatformException(code: 'timeout'))
        : Future<Object?>.value(true);
    calls.clear();

    await expectLater(model.close(), throwsA(isA<PlatformException>()));
    expect(callsTo('detachVideoSurfaces').length, 1);
    expect(callsTo('disposeVideoTexture'), isEmpty);
    expect(model.textureId, 7);
  });

  test('the next session on the same owner gets its own texture', () async {
    model.showTarget(_target());
    await pumpEventQueue();
    await model.close();

    // The closed session stays closed until the owner resets it.
    model.showTarget(_target());
    await pumpEventQueue();
    expect(callsTo('registerVideoTexture').length, 1);

    calls.clear();
    model.reset();
    model.showTarget(_target());
    await pumpEventQueue();

    expect(callsTo('createVideoTexture').length, 1);
    expect(argOf(callsTo('registerVideoTexture').single, 'textureId'), 8);
  });
}
