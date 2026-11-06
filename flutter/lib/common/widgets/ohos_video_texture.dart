import 'package:flutter/material.dart';
import 'package:flutter_hbb/common.dart';
import 'package:flutter_hbb/models/model.dart';
import 'package:flutter_hbb/models/ohos_video_texture.dart';
import 'package:provider/provider.dart';

/// Draws the session's HarmonyOS video texture where, and at the size, the
/// memory picture is drawn.
///
/// The memory picture stays what the user looks at until the core reports that
/// a frame really came out of the texture. It is also the picture a codec
/// without hardware Surface support keeps producing, so a fallback needs no
/// separate path, and a stale memory frame can never cover a live Surface.
/// Everywhere but HarmonyOS this is exactly the widget it was given.
class OhosVideoTextureLayer extends StatelessWidget {
  const OhosVideoTextureLayer({
    super.key,
    required this.ffi,
    required this.picture,
    this.offset,
    this.scale,
  });

  final FFI ffi;

  /// The memory picture, drawn where the texture would be.
  final Widget picture;
  final Offset? offset;
  final double? scale;

  @override
  Widget build(BuildContext context) {
    if (!isOhos) return picture;
    // Both notify when the display, its size or the canvas geometry changes,
    // which is what keeps the declared target and its placement in step.
    final ffiModel = Provider.of<FfiModel>(context);
    final canvas = Provider.of<CanvasModel>(context);
    final video = ffi.ohosVideoTexture;
    final target = ohosVideoTargetOf(ffi);
    // Declaring on every build is idempotent: only a changed target reaches
    // the core.
    video.showTarget(target);
    if (target == null) return picture;
    var sizeScale = scale ?? canvas.scale;
    if (scale == null && ffiModel.isPeerLinux) {
      final displays = ffiModel.pi.getCurDisplays();
      if (displays.isNotEmpty) {
        sizeScale = sizeScale / displays[0].scale;
      }
    }
    final origin = offset ?? Offset(canvas.x, canvas.y + canvas.getAdjustY());
    return AnimatedBuilder(
      animation: video,
      builder: (context, _) {
        final textureId = video.textureId;
        if (textureId < 0) {
          return picture;
        }
        return Stack(clipBehavior: Clip.none, children: [
          Positioned(
            left: origin.dx,
            top: origin.dy,
            width: target.width * sizeScale,
            height: target.height * sizeScale,
            // Hover, cursor and touch handling belong to the session page.
            child: IgnorePointer(
              child: Texture(
                textureId: textureId,
                filterQuality: FilterQuality.low,
              ),
            ),
          ),
          // Keep the texture mounted and consuming buffers even before its
          // first frame; an existing RGBA frame covers it until then.
          if (!video.isSurfaceOutput(target.display)) picture,
        ]);
      },
    );
  }
}
