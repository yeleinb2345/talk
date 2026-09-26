import 'dart:io';
import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:path_provider/path_provider.dart';
import 'package:video_player/video_player.dart';
import 'package:share_plus/share_plus.dart';
import 'package:cross_file/cross_file.dart';
import 'package:ffmpeg_kit_flutter_new_full/ffmpeg_kit.dart';
import 'package:ffmpeg_kit_flutter_new_full/return_code.dart';

void main() => runApp(const LuminaGradeApp());

class LuminaGradeApp extends StatelessWidget {
  const LuminaGradeApp({super.key});
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'LuminaGrade',
      theme: ThemeData.dark(useMaterial3: true).copyWith(
        scaffoldBackgroundColor: const Color(0xFF101217),
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF8FB9FF), brightness: Brightness.dark),
      ),
      home: const GradePage(),
    );
  }
}

class Preset {
  final double exposure, contrast, saturation, sharpen, glow, radius, warmth, highlights;
  const Preset(this.exposure, this.contrast, this.saturation, this.sharpen, this.glow, this.radius, this.warmth, this.highlights);
}

class GradePage extends StatefulWidget {
  const GradePage({super.key});
  @override
  State<GradePage> createState() => _GradePageState();
}

class _GradePageState extends State<GradePage> {
  final presets = const <String, Preset>{
    '剪辑圈亮透': Preset(0.08, 1.12, 1.08, 1.15, 0.26, 10, -0.02, 0.10),
    '蓝调清透': Preset(0.04, 1.15, 1.04, 1.25, 0.22, 9, -0.10, 0.08),
    '暖白发光': Preset(0.10, 1.08, 1.02, 0.85, 0.34, 13, 0.10, 0.12),
    '柔和高光': Preset(0.05, 1.04, 0.98, 0.55, 0.40, 17, 0.03, 0.16),
  };

  String presetName = '剪辑圈亮透';
  String? inputPath, outputPath, previewPath;
  VideoPlayerController? controller;
  bool busy = false;
  bool showingProcessed = false;
  String status = '选择视频后即可本地调色；不上传云端，不使用 AI。';

  double exposure = 0.08, contrast = 1.12, saturation = 1.08, sharpen = 1.15;
  double glow = 0.26, radius = 10, warmth = -0.02, highlights = 0.10;

  @override
  void dispose() {
    controller?.dispose();
    super.dispose();
  }

  double clamp(double v, double lo, double hi) => v < lo ? lo : (v > hi ? hi : v);
  String q(String s) => '"${s.replaceAll('"', '\\"')}"';

  void applyPreset(String name) {
    final p = presets[name]!;
    setState(() {
      presetName = name;
      exposure = p.exposure; contrast = p.contrast; saturation = p.saturation; sharpen = p.sharpen;
      glow = p.glow; radius = p.radius; warmth = p.warmth; highlights = p.highlights;
      status = '已应用预设：$name';
    });
  }

  Future<void> pickVideo() async {
    final result = await FilePicker.platform.pickFiles(type: FileType.video);
    final path = result?.files.single.path;
    if (path == null) return;
    inputPath = path;
    previewPath = null;
    showingProcessed = false;
    await _loadVideo(path);
    setState(() => status = '已载入：${path.split(Platform.pathSeparator).last}');
  }

  Future<void> _loadVideo(String path) async {
    await controller?.dispose();
    final c = VideoPlayerController.file(File(path));
    await c.initialize();
    c.setLooping(true);
    await c.play();
    if (!mounted) return;
    setState(() => controller = c);
  }

  String filterGraph() {
    final brightness = clamp(exposure, -0.30, 0.30);
    final rs = clamp(warmth * 0.45, -0.25, 0.25);
    final bs = clamp(-warmth * 0.45, -0.25, 0.25);
    final baseBrightness = clamp(brightness + highlights * 0.10, -0.35, 0.35);
    final glowBrightness = clamp(-0.18 + highlights * 0.35, -0.25, 0.08);
    final glowContrast = 1.55 + highlights * 0.9;
    return '[0:v]eq=brightness=${baseBrightness.toStringAsFixed(4)}:contrast=${contrast.toStringAsFixed(4)}:saturation=${saturation.toStringAsFixed(4)},'
        'colorbalance=rs=${rs.toStringAsFixed(4)}:bs=${bs.toStringAsFixed(4)},'
        'unsharp=5:5:${sharpen.toStringAsFixed(4)}:5:5:0,split=2[base][glow];'
        '[glow]eq=brightness=${glowBrightness.toStringAsFixed(4)}:contrast=${glowContrast.toStringAsFixed(4)},'
        'gblur=sigma=${radius.toStringAsFixed(2)}[blur];'
        '[base][blur]blend=all_mode=screen:all_opacity=${glow.toStringAsFixed(4)}[outv]';
  }

  Future<bool> runFFmpeg(String input, String output, {double? previewSeconds}) async {
    final graph = filterGraph();
    final inputArgs = previewSeconds == null ? '-i ${q(input)}' : '-ss 0.5 -i ${q(input)} -t ${previewSeconds.toStringAsFixed(1)}';
    final cmd = '$inputArgs -filter_complex "$graph" -map "[outv]" -map 0:a? '
        '-c:v mpeg4 -q:v 2 -c:a aac -b:a 192k -pix_fmt yuv420p -movflags +faststart -y ${q(output)}';
    final session = await FFmpegKit.execute(cmd);
    final rc = await session.getReturnCode();
    if (ReturnCode.isSuccess(rc)) return true;
    final logs = await session.getAllLogsAsString();
    if (mounted) setState(() => status = '处理失败：${logs.length > 240 ? logs.substring(logs.length - 240) : logs}');
    return false;
  }

  Future<void> makePreview() async {
    if (inputPath == null || busy) return;
    setState(() { busy = true; status = '正在生成 3 秒效果预览…'; });
    final dir = await getTemporaryDirectory();
    final out = '${dir.path}/luminagrade_preview.mp4';
    try {
      final ok = await runFFmpeg(inputPath!, out, previewSeconds: 3);
      if (ok) {
        previewPath = out;
        showingProcessed = true;
        await _loadVideo(out);
        if (mounted) setState(() => status = '效果预览已生成（3 秒循环播放）');
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> showOriginal() async {
    if (inputPath == null) return;
    showingProcessed = false;
    await _loadVideo(inputPath!);
    if (mounted) setState(() => status = '当前显示原片');
  }

  Future<void> exportFull() async {
    if (inputPath == null || busy) return;
    setState(() { busy = true; status = '正在完整导出，请保持应用在前台…'; });
    final dir = await getApplicationDocumentsDirectory();
    final stamp = DateTime.now().millisecondsSinceEpoch;
    final out = '${dir.path}/LuminaGrade_$stamp.mp4';
    try {
      final ok = await runFFmpeg(inputPath!, out);
      if (ok) {
        outputPath = out;
        if (mounted) setState(() => status = '导出完成，可点击“分享/保存”');
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> shareOutput() async {
    if (outputPath == null) return;
    await SharePlus.instance.share(ShareParams(
      title: 'LuminaGrade 导出视频',
      text: 'LuminaGrade 本地调色导出',
      files: [XFile(outputPath!, mimeType: 'video/mp4')],
    ));
  }

  Widget slider(String label, double value, double min, double max, ValueChanged<double> onChanged) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [Expanded(child: Text(label)), Text(value.toStringAsFixed(2), style: const TextStyle(color: Colors.white70))]),
      Slider(value: value.clamp(min, max), min: min, max: max, onChanged: busy ? null : onChanged),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    final c = controller;
    return Scaffold(
      appBar: AppBar(title: const Text('LuminaGrade 本地调色'), actions: [
        Padding(padding: const EdgeInsets.only(right: 12), child: Center(child: Text(showingProcessed ? '效果预览' : '原片', style: const TextStyle(color: Colors.white70)))),
      ]),
      body: SafeArea(child: ListView(padding: const EdgeInsets.all(14), children: [
        Container(
          height: 260,
          decoration: BoxDecoration(color: Colors.black, borderRadius: BorderRadius.circular(14)),
          clipBehavior: Clip.antiAlias,
          child: c != null && c.value.isInitialized
              ? Stack(fit: StackFit.expand, children: [
                  Center(child: AspectRatio(aspectRatio: c.value.aspectRatio, child: VideoPlayer(c))),
                  Positioned(left: 10, bottom: 10, child: FilledButton.tonal(onPressed: () => c.value.isPlaying ? c.pause() : c.play(), child: const Icon(Icons.play_arrow))),
                ])
              : const Center(child: Text('请选择视频')),
        ),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(child: FilledButton.icon(onPressed: busy ? null : pickVideo, icon: const Icon(Icons.video_library_outlined), label: const Text('选择视频'))),
          const SizedBox(width: 8),
          Expanded(child: OutlinedButton.icon(onPressed: inputPath == null || busy ? null : showOriginal, icon: const Icon(Icons.undo), label: const Text('看原片'))),
        ]),
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(
          value: presetName,
          decoration: const InputDecoration(labelText: '预设', border: OutlineInputBorder()),
          items: presets.keys.map((e) => DropdownMenuItem(value: e, child: Text(e))).toList(),
          onChanged: busy ? null : (v) { if (v != null) applyPreset(v); },
        ),
        const SizedBox(height: 10),
        slider('曝光', exposure, -0.30, 0.30, (v) => setState(() => exposure = v)),
        slider('对比度', contrast, 0.80, 1.35, (v) => setState(() => contrast = v)),
        slider('饱和度', saturation, 0.70, 1.40, (v) => setState(() => saturation = v)),
        slider('锐化', sharpen, 0.0, 2.0, (v) => setState(() => sharpen = v)),
        slider('Glow 强度', glow, 0.0, 0.60, (v) => setState(() => glow = v)),
        slider('Glow 半径', radius, 2.0, 24.0, (v) => setState(() => radius = v)),
        slider('冷暖', warmth, -0.20, 0.20, (v) => setState(() => warmth = v)),
        slider('高光亮度', highlights, 0.0, 0.25, (v) => setState(() => highlights = v)),
        const SizedBox(height: 8),
        if (busy) const LinearProgressIndicator(),
        const SizedBox(height: 8),
        Text(status, style: const TextStyle(color: Colors.white70)),
        const SizedBox(height: 12),
        FilledButton.icon(onPressed: inputPath == null || busy ? null : makePreview, icon: const Icon(Icons.auto_awesome), label: const Text('生成 3 秒效果预览')),
        const SizedBox(height: 8),
        FilledButton.icon(onPressed: inputPath == null || busy ? null : exportFull, icon: const Icon(Icons.movie_creation_outlined), label: const Text('完整导出 MP4')),
        const SizedBox(height: 8),
        OutlinedButton.icon(onPressed: outputPath == null || busy ? null : shareOutput, icon: const Icon(Icons.ios_share), label: const Text('分享 / 保存导出文件')),
        const SizedBox(height: 24),
        const Text('全部处理在本机完成。Glow 为传统后期模糊 + Screen 混合，不包含第三方付费插件代码。', style: TextStyle(fontSize: 12, color: Colors.white54)),
      ])),
    );
  }
}
