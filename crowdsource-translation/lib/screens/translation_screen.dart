import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:uuid/uuid.dart';

import '../models/daily_quota.dart';
import '../models/sentence.dart';
import '../models/translation.dart';
import '../models/user_translation.dart';
import '../services/firestore_service.dart';
import '../services/handwriting_recognizer.dart';
import '../services/recording_upload_service.dart';
import '../utils/speech_helper.dart';
import '../widgets/handwriting_pad.dart';

/// Collects one translation, using whichever input method the contributor
/// picked on the home screen.
///
/// All three methods converge on the same thing: a Bangla string in
/// [_transcription] plus an optional recording. Voice fills it via on-device
/// Whisper, typing fills it directly, and handwriting recognises captured ink on device.
/// Contributors can review and edit the resulting text before submitting.
class TranslationScreen extends StatefulWidget {
  const TranslationScreen({
    super.key,
    required this.userId,
    this.inputMethod = TranslationInputMethod.voice,
    this.redoOf,
  });

  final String userId;
  final TranslationInputMethod inputMethod;

  /// When set, this screen revises an existing submission instead of taking a
  /// new assignment. Redoing does not consume the daily allowance.
  final UserTranslation? redoOf;

  @override
  State<TranslationScreen> createState() => _TranslationScreenState();
}

class _TranslationScreenState extends State<TranslationScreen>
    with WidgetsBindingObserver {
  final FirestoreService _firestoreService = FirestoreService();
  final AudioRecorder _audioRecorder = AudioRecorder();
  final SpeechHelper _whisperHelper = SpeechHelper();
  final TextEditingController _textController = TextEditingController();
  final TextEditingController _noteController = TextEditingController();
  final GlobalKey<HandwritingPadState> _padKey =
      GlobalKey<HandwritingPadState>();

  final HandwritingRecognizer _handwritingRecognizer =
      OnDeviceHandwritingRecognizer();
  bool _isRecognising = false;
  bool _hasInk = false;
  int _inkRevision = 0;
  String? _handwritingError;

  bool _isWhisperInitialized = false;
  List<Sentence> _sentences = [];
  int _currentIndex = 0;
  bool _isLoading = true;
  bool _isRecording = false;
  bool _isRecorderBusy = false;
  bool _isSpeechLoading = false;
  String? _speechError;
  bool _isTranscribing = false;
  bool _isSubmitting = false;
  String _transcription = '';
  String? _recordedPath;
  String _submissionId = const Uuid().v4();
  String? _recordingId;
  String? _uploadedAudioUrl;
  final _recordingUpload = RecordingUploadService();

  bool get _isRedo => widget.redoOf != null;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _loadSentences();
    if (widget.inputMethod == TranslationInputMethod.voice) {
      _initSpeech();
    }
  }

  Future<void> _loadSentences() async {
    // A redo already knows its sentence; the text is denormalised onto the
    // submission so it renders even if an admin has since deleted it.
    final redo = widget.redoOf;
    if (redo != null) {
      // Fetch the live sentence for its context; fall back to the
      // denormalised text when it is gone or unreachable.
      Sentence? live;
      try {
        live = await _firestoreService.getSentence(redo.sentenceId);
      } catch (e) {
        debugPrint('Could not load sentence context: $e');
      }
      if (!mounted) return;
      setState(() {
        _sentences = [
          live ?? Sentence(id: redo.sentenceId, text: redo.sentenceText)
        ];
        _transcription = redo.transcription;
        _textController.text = redo.transcription;
        _noteController.text = redo.note;
        _isLoading = false;
      });
      return;
    }

    try {
      final sentences = await _firestoreService.getSentencesToTranslate();
      if (!mounted) return;
      setState(() {
        _sentences = sentences;
        _isLoading = false;
      });
    } catch (e) {
      debugPrint('Error loading sentences: $e');
      if (!mounted) return;
      setState(() => _isLoading = false);
    }
  }

  Future<void> _initSpeech() async {
    if (_isSpeechLoading) return;
    setState(() {
      _isSpeechLoading = true;
      _speechError = null;
    });
    try {
      await _whisperHelper.init();
      if (mounted) {
        setState(() => _isWhisperInitialized = true);
      }
    } catch (e) {
      debugPrint('Speech recognizer init failed: $e');
      if (mounted) {
        setState(() {
          _isWhisperInitialized = false;
          _speechError =
              'Speech model could not load. You can retry or type your translation.';
        });
      }
    } finally {
      if (mounted) setState(() => _isSpeechLoading = false);
    }
  }

  Future<void> _startRecording() async {
    if (_isRecorderBusy || _isRecording || _isTranscribing || _isSubmitting) {
      return;
    }
    setState(() => _isRecorderBusy = true);
    try {
      if (!await _audioRecorder.hasPermission()) {
        _toast('Microphone permission is required to record.');
        return;
      }

      if (!mounted) return;

      // The recorder needs a real writable location. The previous bare
      // 'recording.wav' resolved against the process working directory, which
      // is not writable on Android, so the recording silently never started.
      String path = '';
      if (!kIsWeb) {
        final dir = await getTemporaryDirectory();
        path =
            '${dir.path}/recording_${DateTime.now().millisecondsSinceEpoch}.wav';
      }

      await _audioRecorder.start(
        const RecordConfig(
          // Whisper wants 16 kHz mono PCM; converting later would mean pulling
          // in a resampler, so record in the target format directly.
          encoder: AudioEncoder.wav,
          sampleRate: 16000,
          numChannels: 1,
        ),
        path: path,
      );

      if (!mounted) {
        await _audioRecorder.stop();
        return;
      }
      setState(() {
        _isRecording = true;
        _recordedPath = null;
        _recordingId = const Uuid().v4();
        _uploadedAudioUrl = null;
        _transcription = '';
        _textController.clear();
      });
    } catch (e) {
      debugPrint('Error starting record: $e');
      _toast('Could not start recording. Check microphone access and retry.');
    } finally {
      if (mounted) setState(() => _isRecorderBusy = false);
    }
  }

  Future<void> _stopRecording() async {
    if (!_isRecording || _isRecorderBusy) return;
    setState(() => _isRecorderBusy = true);
    String? path;
    try {
      path = await _audioRecorder.stop();
      if (!mounted) return;
      setState(() {
        _isRecording = false;
        _recordedPath = path;
      });
      if (path == null) _toast('No recording was saved. Please try again.');
    } catch (e) {
      debugPrint('Error stopping recording: $e');
      if (mounted) setState(() => _isRecording = false);
      _toast('Could not save the recording. Please try again.');
    } finally {
      if (mounted) setState(() => _isRecorderBusy = false);
    }
    if (mounted && path != null) await _transcribeRecording();
  }

  Future<void> _transcribeRecording() async {
    final path = _recordedPath;
    if (path == null ||
        _isTranscribing ||
        _isRecording ||
        _isRecorderBusy ||
        _isSubmitting) {
      return;
    }
    setState(() => _isTranscribing = true);
    try {
      // init is shared with startup, so a recording made while the model is
      // loading is retained and processed when initialization completes.
      final result = (await _whisperHelper.transcribe(path, 'bn') ?? '').trim();
      if (!mounted) return;
      setState(() {
        _isWhisperInitialized = true;
        _speechError = null;
        _transcription = result;
        _textController.text = result;
      });
      if (result.isEmpty) {
        _toast(
            'No speech recognised. Retry transcription or type your translation.');
      }
    } catch (e) {
      debugPrint('Whisper transcription error: $e');
      if (mounted) {
        setState(() => _speechError =
            'Transcription failed. Your recording is saved; retry or type your translation.');
      }
    } finally {
      if (mounted) setState(() => _isTranscribing = false);
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused && _isRecording) {
      _stopRecording();
    }
  }

  bool get _canSubmit {
    if (_isSubmitting ||
        _isTranscribing ||
        _isRecording ||
        _isRecorderBusy ||
        _isRecognising) {
      return false;
    }
    if (_sentences.isEmpty) return false;
    return _transcription.trim().isNotEmpty;
  }

  Future<void> _submitTranslation() async {
    if (!_canSubmit) return;
    final sentence = _sentences[_currentIndex];

    setState(() => _isSubmitting = true);
    try {
      var audioUrl = widget.redoOf?.audioUrl ?? '';
      if (_recordedPath != null) {
        _uploadedAudioUrl ??= await _recordingUpload.upload(
          userId: widget.userId,
          sentenceId: sentence.id,
          recordingId: _recordingId!,
          path: _recordedPath!,
        );
        audioUrl = _uploadedAudioUrl!;
      }
      await _firestoreService.submitUserTranslation(
        userId: widget.userId,
        sentence: sentence,
        transcription: _transcription.trim(),
        inputMethod: widget.inputMethod,
        audioUrl: audioUrl,
        redoOf: widget.redoOf?.id,
        submissionId: _submissionId,
        note: _noteController.text,
      );

      if (!mounted) return;
      _toast(_isRedo ? 'Translation updated!' : 'Translation submitted!');

      if (_isRedo) {
        Navigator.of(context).pop();
        return;
      }

      setState(() {
        _recordedPath = null;
        _submissionId = const Uuid().v4();
        _recordingId = null;
        _uploadedAudioUrl = null;
        _transcription = '';
        _textController.clear();
        _noteController.clear();
        _isSubmitting = false;
        if (_currentIndex < _sentences.length - 1) {
          _currentIndex++;
        } else {
          _currentIndex = 0;
          _isLoading = true;
        }
      });
      _padKey.currentState?.clear();
      if (_isLoading) await _loadSentences();
    } on DailyLimitReachedException catch (e) {
      if (!mounted) return;
      setState(() => _isSubmitting = false);
      _toast(e.toString());
    } catch (e, stackTrace) {
      debugPrint('CRITICAL ERROR in _submitTranslation: $e');
      debugPrint('Stack trace: $stackTrace');
      if (!mounted) return;
      setState(() => _isSubmitting = false);
      _toast('Error: $e');
    }
  }

  Future<void> _recogniseHandwriting() async {
    if (_isRecognising || !_hasInk || _isSubmitting) return;
    final revision = _inkRevision;
    final strokes = _padKey.currentState!.strokes;
    setState(() {
      _isRecognising = true;
      _handwritingError = null;
    });
    try {
      final result = await _handwritingRecognizer.recognise(strokes);
      if (!mounted || revision != _inkRevision) return;
      if (result == null || result.isEmpty) {
        setState(() => _handwritingError =
            'No handwriting recognised. Try again or type your translation.');
      } else {
        setState(() {
          _transcription = result;
          _textController.text = result;
        });
      }
    } catch (e) {
      debugPrint('Handwriting recognition failed: $e');
      if (mounted) {
        setState(() => _handwritingError =
            'Could not recognise handwriting. Check your connection for the first model download, then retry. You can also type.');
      }
    } finally {
      if (mounted) {
        setState(() => _isRecognising = false);
      } else {
        await _handwritingRecognizer.close();
      }
    }
  }

  void _toast(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _audioRecorder.dispose();
    _textController.dispose();
    _noteController.dispose();
    // Recognition may be awaiting a model download; close after it completes.
    if (!_isRecognising) _handwritingRecognizer.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }

    if (_sentences.isEmpty) {
      return Scaffold(
        appBar: AppBar(title: const Text('Translate')),
        body: const Center(child: Text('No sentences to translate!')),
      );
    }

    final sentence = _sentences[_currentIndex];

    return Scaffold(
      appBar: AppBar(
        title: Text(_isRedo ? 'Redo translation' : 'Translate to Bangla'),
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            Text(
              'Translate this:',
              style: Theme.of(context).textTheme.labelLarge,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 16),
            Text(
              sentence.text,
              style: Theme.of(context).textTheme.headlineSmall,
              textAlign: TextAlign.center,
            ),
            if (sentence.hasContext) ...[
              const SizedBox(height: 16),
              _ContextCard(sentence: sentence),
            ],
            const SizedBox(height: 32),
            _buildInput(),
            const SizedBox(height: 16),
            TextField(
              controller: _noteController,
              maxLines: 2,
              enabled: !_isSubmitting,
              decoration: const InputDecoration(
                labelText: 'Note on word choice (optional)',
                hintText:
                    'e.g. used ইবাদত, not পূজা, because this is about worshipping Allah',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 24),
            if (_isSubmitting)
              const Center(child: CircularProgressIndicator())
            else
              ElevatedButton(
                onPressed: _canSubmit ? _submitTranslation : null,
                child: Text(_isRedo ? 'Save changes' : 'Submit Translation'),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildInput() {
    switch (widget.inputMethod) {
      case TranslationInputMethod.text:
        return TextField(
          controller: _textController,
          maxLines: 4,
          autofocus: true,
          enabled: !_isSubmitting,
          decoration: const InputDecoration(
            labelText: 'Bangla translation',
            border: OutlineInputBorder(),
          ),
          onChanged: (value) => setState(() => _transcription = value),
        );

      case TranslationInputMethod.handwriting:
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            IgnorePointer(
              ignoring: _isRecognising || _isSubmitting,
              child: HandwritingPad(
                  key: _padKey,
                  onStrokesChanged: (strokes) {
                    setState(() {
                      _hasInk = strokes.isNotEmpty;
                      _inkRevision++;
                    });
                  }),
            ),
            const SizedBox(height: 8),
            if (_handwritingRecognizer.isAvailable)
              FilledButton.icon(
                onPressed: _hasInk && !_isRecognising && !_isSubmitting
                    ? _recogniseHandwriting
                    : null,
                icon: const Icon(Icons.text_fields),
                label: Text(_isRecognising
                    ? 'Recognising handwriting...'
                    : 'Recognise handwriting'),
              ),
            const SizedBox(height: 8),
            Text(_handwritingRecognizer.isAvailable
                ? 'Write in Bangla, then tap Recognise handwriting. The first use downloads a language model. Review the text before submitting.'
                : 'Handwriting recognition requires Android or iOS. You can type your translation below.'),
            if (_handwritingError != null) Text(_handwritingError!),
            const SizedBox(height: 8),
            TextField(
              controller: _textController,
              maxLines: 3,
              enabled: !_isRecognising && !_isSubmitting,
              decoration: const InputDecoration(
                labelText: 'Bangla translation',
                border: OutlineInputBorder(),
              ),
              onChanged: (value) => setState(() => _transcription = value),
            ),
          ],
        );

      case TranslationInputMethod.voice:
        return Column(
          children: [
            TextField(
              controller: _textController,
              maxLines: 4,
              enabled: !_isRecording &&
                  !_isRecorderBusy &&
                  !_isTranscribing &&
                  !_isSubmitting,
              decoration: const InputDecoration(
                labelText: 'Bangla translation',
                hintText: 'Record your voice or type your translation',
                border: OutlineInputBorder(),
              ),
              onChanged: (value) => setState(() => _transcription = value),
            ),
            const SizedBox(height: 24),
            FilledButton.icon(
              onPressed: _isRecorderBusy || _isTranscribing || _isSubmitting
                  ? null
                  : (_isRecording ? _stopRecording : _startRecording),
              icon: Icon(_isRecording ? Icons.stop : Icons.mic),
              label: Text(_isRecording ? 'Stop recording' : 'Start recording'),
            ),
            const SizedBox(height: 12),
            if (_isTranscribing)
              const Text('Transcribing...')
            else if (_isRecorderBusy)
              const Text('Preparing recording...')
            else if (_isRecording)
              const Text('Recording... tap Stop recording when finished',
                  style: TextStyle(color: Colors.red))
            else if (_isSpeechLoading)
              const Text(
                  'Loading speech model... You can record while it loads.'),
            if (_speechError != null) Text(_speechError!),
            if (!_isWhisperInitialized &&
                !_isSpeechLoading &&
                !_isTranscribing &&
                !_isRecording)
              TextButton(
                  onPressed: _initSpeech,
                  child: const Text('Retry speech model')),
            if (_recordedPath != null && !_isRecording && !_isRecorderBusy)
              TextButton(
                onPressed: _isTranscribing || _isSubmitting
                    ? null
                    : _transcribeRecording,
                child: const Text('Retry transcription'),
              ),
            if (_recordedPath != null && !_isRecording)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text('Recording captured',
                    style: TextStyle(color: Colors.green)),
              ),
          ],
        );
    }
  }
}

/// The lines around the sentence in the source video, so the contributor can
/// pick the Bangla word that fits what is actually being talked about.
class _ContextCard extends StatelessWidget {
  const _ContextCard({required this.sentence});

  final Sentence sentence;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final muted = theme.textTheme.bodyMedium
        ?.copyWith(color: theme.colorScheme.onSurfaceVariant);
    return Card(
      child: ExpansionTile(
        initiallyExpanded: true,
        leading: const Icon(Icons.subject),
        title: const Text('Context'),
        subtitle: const Text('What is said around this line. Translate only the line above.'),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if ((sentence.contextBefore ?? '').isNotEmpty)
            Text('... ${sentence.contextBefore}', style: muted),
          const SizedBox(height: 8),
          Text(sentence.text,
              style: theme.textTheme.bodyMedium
                  ?.copyWith(fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          if ((sentence.contextAfter ?? '').isNotEmpty)
            Text('${sentence.contextAfter} ...', style: muted),
        ],
      ),
    );
  }
}
