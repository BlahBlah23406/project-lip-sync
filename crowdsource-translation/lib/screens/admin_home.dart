import 'review_screen.dart';
import 'package:flutter/material.dart';
import '../services/auth_service.dart';
import '../services/firestore_service.dart';
import '../models/sentence.dart';
import '../utils/download_helper.dart';
// import '../models/translation.dart'; // Unused
import 'package:file_picker/file_picker.dart';
import 'dart:convert';
import 'dart:typed_data';

class AdminHome extends StatefulWidget {
  const AdminHome({super.key});

  @override
  State<AdminHome> createState() => _AdminHomeState();
}

class _AdminHomeState extends State<AdminHome> {
  final TextEditingController _sentenceController = TextEditingController();
  final FirestoreService _firestoreService = FirestoreService();

  @override
  void dispose() {
    _sentenceController.dispose();
    super.dispose();
  }

  void _addSentence() async {
    if (_sentenceController.text.trim().isEmpty) return;

    try {
      await _firestoreService.addSentence(_sentenceController.text.trim());
      _sentenceController.clear();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Sentence added successfully!')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Error adding sentence: $e')),
        );
      }
    }
  }

  void _pickAndImportFile() async {
    FilePickerResult? result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['txt'],
      withData: true, // Important for Web
    );

    if (result != null) {
      // On Web, result.files.first.path is null, use bytes
      Uint8List? fileBytes = result.files.first.bytes;

      if (fileBytes != null) {
        String content = utf8.decode(fileBytes);
        // Split by period, question mark, exclamation point followed by space or end of line
        // Keeping the punctuation.
        // Lookbehind matching is cleaner but Dart regex support varies.
        // Simple approach: split and rejoin or regex replace.

        // This regex matches [.!?] followed by whitespace
        RegExp exp = RegExp(r'([.!?])\s+');

        // We want to keep the punctuation.
        // 1. Replace "Punctuation + Space" with "Punctuation|SplitHere|"
        String processed = content.replaceAllMapped(exp, (Match m) {
          return '${m.group(1)}|SPLIT|';
        });

        // 2. Split by the delimiter
        List<String> rawSentences = processed.split('|SPLIT|');

        // 3. Clean up
        List<String> sentences = rawSentences
            .map((s) => s.trim())
            .where((s) => s.isNotEmpty)
            .toList();

        if (mounted) {
          _showReviewDialog(sentences);
        }
      }
    }
  }

  void _showReviewDialog(List<String> initialSentences) {
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => SentenceReviewDialog(
        sentences: initialSentences,
        onConfirm: (List<String> confirmedSentences) async {
          Navigator.pop(dialogContext); // Close dialog
          try {
            await _firestoreService.batchAddSentences(confirmedSentences);
            if (mounted) {
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(
                    content: Text(
                        'Successfully imported ${confirmedSentences.length} sentences.')),
              );
            }
          } catch (e) {
            if (mounted) {
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(content: Text('Error importing sentences: $e')),
              );
            }
          }
        },
      ),
    );
  }

  void _exportAllTranslations() async {
    try {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Preparing export...')),
      );

      final sentences = await _firestoreService.getSentences().first;

      final List<String> jsonLines = [];
      for (final sentence in sentences) {
        if (sentence.translations.isNotEmpty) {
          for (final t in sentence.translations) {
            final jsonStr = jsonEncode({
              "english": sentence.text,
              "bangla": t.transcription,
              "audio_url": t.audioUrl,
            });
            jsonLines.add(jsonStr);
          }
        }
      }

      if (jsonLines.isEmpty) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
                content: Text('No translations available to export.')),
          );
        }
        return;
      }

      final fileContent = jsonLines.join('\n');
      downloadFile(fileContent, 'all_translations.jsonl');

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Export completed.')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Error exporting: $e')),
        );
      }
    }
  }

  void _openDatasetDialog() {
    showDialog(
      context: context,
      builder: (_) => DatasetExportDialog(firestoreService: _firestoreService),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Crowdsource Admin'),
        actions: [
          TextButton.icon(
            onPressed: () => Navigator.of(context)
                .push(MaterialPageRoute(builder: (_) => const ReviewScreen())),
            icon: const Icon(Icons.rate_review),
            label: const Text('Review translations'),
          ),
          TextButton.icon(
            onPressed: () => AuthService().signOut(),
            icon: const Icon(Icons.logout),
            label: const Text('Sign out'),
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          children: [
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _sentenceController,
                    decoration: const InputDecoration(
                      labelText: 'Enter English Sentence',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
                const SizedBox(width: 16),
                ElevatedButton(
                  onPressed: _addSentence,
                  child: const Text('Add'),
                ),
                const SizedBox(width: 16),
                ElevatedButton.icon(
                  onPressed: _pickAndImportFile,
                  icon: const Icon(Icons.upload_file),
                  label: const Text('Import TXT'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.teal.shade100,
                    foregroundColor: Colors.teal.shade900,
                  ),
                ),
                const SizedBox(width: 8),
                ElevatedButton.icon(
                  onPressed: _exportAllTranslations,
                  icon: const Icon(Icons.download),
                  label: const Text('Export All'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.amber.shade100,
                    foregroundColor: Colors.amber.shade900,
                  ),
                ),
                const SizedBox(width: 8),
                FilledButton.icon(
                  onPressed: _openDatasetDialog,
                  icon: const Icon(Icons.dataset),
                  label: const Text('Download dataset'),
                ),
              ],
            ),
            const SizedBox(height: 24),
            Expanded(
              child: StreamBuilder<List<Sentence>>(
                stream: _firestoreService.getSentences(),
                builder: (context, snapshot) {
                  if (snapshot.hasError) {
                    return Center(child: Text('Error: ${snapshot.error}'));
                  }

                  if (snapshot.connectionState == ConnectionState.waiting) {
                    return const Center(child: CircularProgressIndicator());
                  }

                  final sentences = snapshot.data ?? [];

                  return ListView.builder(
                    itemCount: sentences.length,
                    itemBuilder: (context, index) {
                      final sentence = sentences[index];
                      return AdminSentenceTile(
                        sentence: sentence,
                        firestoreService: _firestoreService,
                      );
                    },
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class SentenceReviewDialog extends StatefulWidget {
  final List<String> sentences;
  final Function(List<String>) onConfirm;

  const SentenceReviewDialog({
    super.key,
    required this.sentences,
    required this.onConfirm,
  });

  @override
  State<SentenceReviewDialog> createState() => _SentenceReviewDialogState();
}

class _SentenceReviewDialogState extends State<SentenceReviewDialog> {
  late List<TextEditingController> _controllers;

  @override
  void initState() {
    super.initState();
    _controllers =
        widget.sentences.map((s) => TextEditingController(text: s)).toList();
  }

  @override
  void dispose() {
    for (var controller in _controllers) {
      controller.dispose();
    }
    super.dispose();
  }

  void _removeSentence(int index) {
    setState(() {
      _controllers[index].dispose();
      _controllers.removeAt(index);
    });
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text('Review Sentences (${_controllers.length})'),
      content: SizedBox(
        width: 600,
        height: 600,
        child: _controllers.isEmpty
            ? const Center(child: Text('No sentences to import.'))
            : ListView.builder(
                itemCount: _controllers.length,
                itemBuilder: (context, index) {
                  return Padding(
                    padding: const EdgeInsets.only(bottom: 8.0),
                    child: Row(
                      children: [
                        Text('${index + 1}.',
                            style: const TextStyle(color: Colors.grey)),
                        const SizedBox(width: 8),
                        Expanded(
                          child: TextField(
                            controller: _controllers[index],
                            decoration: const InputDecoration(
                              border: OutlineInputBorder(),
                              contentPadding: EdgeInsets.symmetric(
                                  horizontal: 8, vertical: 4),
                            ),
                          ),
                        ),
                        IconButton(
                          icon: const Icon(Icons.delete, color: Colors.red),
                          onPressed: () => _removeSentence(index),
                        ),
                      ],
                    ),
                  );
                },
              ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        ElevatedButton(
          onPressed: _controllers.isEmpty
              ? null
              : () {
                  final finalSentences = _controllers
                      .map((c) => c.text.trim())
                      .where((s) => s.isNotEmpty)
                      .toList();
                  widget.onConfirm(finalSentences);
                },
          child: const Text('Import All'),
        ),
      ],
    );
  }
}

class AdminSentenceTile extends StatelessWidget {
  final Sentence sentence;
  final FirestoreService firestoreService;

  const AdminSentenceTile({
    super.key,
    required this.sentence,
    required this.firestoreService,
  });

  void _editSentence(BuildContext context) {
    final TextEditingController controller =
        TextEditingController(text: sentence.text);
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Edit Sentence'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(labelText: 'Sentence Text'),
          maxLines: 3,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            onPressed: () async {
              if (controller.text.trim().isNotEmpty) {
                Navigator.pop(context);
                try {
                  await firestoreService.updateSentence(
                      sentence.id, controller.text.trim());
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(
                          content: Text('Sentence updated successfully')),
                    );
                  }
                } catch (e) {
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(content: Text('Error updating sentence: $e')),
                    );
                  }
                }
              }
            },
            child: const Text('Save'),
          ),
        ],
      ),
    );
  }

  void _deleteSentence(BuildContext context) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete Sentence'),
        content: const Text(
            'Are you sure you want to delete this sentence? This action cannot be undone.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
                backgroundColor: Colors.red, foregroundColor: Colors.white),
            onPressed: () async {
              Navigator.pop(context);
              try {
                await firestoreService.deleteSentence(sentence.id);
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(
                        content: Text('Sentence deleted successfully')),
                  );
                }
              } catch (e) {
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(content: Text('Error deleting sentence: $e')),
                  );
                }
              }
            },
            child: const Text('Delete'),
          ),
        ],
      ),
    );
  }

  void _exportSentence(BuildContext context) {
    if (sentence.translations.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('No translations to export.')),
      );
      return;
    }

    final List<String> jsonLines = [];
    for (final t in sentence.translations) {
      final jsonStr = jsonEncode({
        "english": sentence.text,
        "bangla": t.transcription,
        "audio_url": t.audioUrl,
      });
      jsonLines.add(jsonStr);
    }

    final fileContent = jsonLines.join('\n');
    downloadFile(fileContent, 'translation_${sentence.id}.jsonl');
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 8, horizontal: 0),
      child: Padding(
        padding: const EdgeInsets.all(12.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                CircleAvatar(
                  backgroundColor: Colors.teal.shade100,
                  foregroundColor: Colors.teal.shade900,
                  child: Text('${sentence.translationCount}'),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Text(
                    sentence.text,
                    style: const TextStyle(
                        fontSize: 16, fontWeight: FontWeight.bold),
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.download, color: Colors.green),
                  onPressed: () => _exportSentence(context),
                  tooltip: 'Export',
                ),
                IconButton(
                  icon: const Icon(Icons.edit, color: Colors.blue),
                  onPressed: () => _editSentence(context),
                  tooltip: 'Edit',
                ),
                IconButton(
                  icon: const Icon(Icons.delete, color: Colors.red),
                  onPressed: () => _deleteSentence(context),
                  tooltip: 'Delete',
                ),
              ],
            ),
            const SizedBox(height: 8),
            const Divider(),
            if (sentence.transcription != null &&
                sentence.transcription!.isNotEmpty) ...[
              const Text(
                'Latest Translation:',
                style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.bold,
                    color: Colors.teal),
              ),
              const SizedBox(height: 4),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(Icons.star, size: 16, color: Colors.orange),
                  const SizedBox(width: 8),
                  Expanded(child: Text(sentence.transcription!)),
                  if (sentence.audioUrl != null &&
                      sentence.audioUrl!.isNotEmpty)
                    const Icon(Icons.audiotrack, size: 16, color: Colors.teal),
                ],
              ),
              const SizedBox(height: 8),
            ],
            const Text(
              'History:',
              style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.bold,
                  color: Colors.grey),
            ),
            const SizedBox(height: 4),
            if (sentence.translations.isEmpty)
              const Text('No translations yet.',
                  style: TextStyle(
                      fontStyle: FontStyle.italic, color: Colors.grey))
            else
              Column(
                children: sentence.translations
                    .map((t) => Padding(
                          padding: const EdgeInsets.symmetric(vertical: 4.0),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Icon(Icons.record_voice_over,
                                  size: 16, color: Colors.teal),
                              const SizedBox(width: 8),
                              Expanded(
                                  child: Text(t.transcription.isNotEmpty
                                      ? t.transcription
                                      : '[No Transcription]')),
                              if (t.audioUrl.isNotEmpty)
                                const Icon(Icons.audiotrack,
                                    size: 16, color: Colors.grey),
                            ],
                          ),
                        ))
                    .toList(),
              ),
          ],
        ),
      ),
    );
  }
}

enum DatasetFormat {
  chat(
      'Fine-tuning (chat JSONL)',
      'One conversation per translation: system prompt, English with its context, human Bangla. Loads directly into most fine-tuning tools.',
      'jsonl'),
  full(
      'Full records (JSONL)',
      "Every field per translation, including the dubbing model's Bangla and contributor notes. For filtering or building your own format.",
      'jsonl'),
  csv('Spreadsheet (CSV)',
      'The full records as a table, for review in Sheets or Excel.', 'csv');

  const DatasetFormat(this.label, this.description, this.extension);

  final String label;
  final String description;
  final String extension;
}

/// The instruction every fine-tuning example carries. It tells the model the
/// context lines are there to choose word senses, not to be translated.
const String kDatasetSystemPrompt =
    'You translate English Islamic lectures into natural spoken Bangla for dubbing. '
    'Use the surrounding context to choose the right word for this setting '
    '(religious terms, tone, who is speaking). Translate only the sentence marked Translate.';

/// Builds a fine-tuning dataset from every human translation.
class DatasetExportDialog extends StatefulWidget {
  const DatasetExportDialog({super.key, required this.firestoreService});

  final FirestoreService firestoreService;

  @override
  State<DatasetExportDialog> createState() => _DatasetExportDialogState();
}

class _DatasetExportDialogState extends State<DatasetExportDialog> {
  DatasetFormat _format = DatasetFormat.chat;
  bool _includeContext = true;
  bool _includeLowQuality = false;
  bool _busy = false;
  String? _result;

  Future<void> _export() async {
    setState(() {
      _busy = true;
      _result = null;
    });
    try {
      final sentences = await widget.firestoreService.getTranslatedSentences();
      final rows = <Map<String, dynamic>>[];
      for (final sentence in sentences) {
        if (sentence.lowQuality && !_includeLowQuality) continue;
        for (final t in sentence.translations) {
          final bangla = t.transcription.trim();
          if (bangla.isEmpty) continue;
          rows.add({
            'translation_id': t.id,
            'sentence_id': sentence.id,
            'english': sentence.text,
            'context_before':
                _includeContext ? (sentence.contextBefore ?? '') : '',
            'context_after':
                _includeContext ? (sentence.contextAfter ?? '') : '',
            'bangla': bangla,
            'model_bangla': sentence.modelBangla ?? '',
            'note': t.note,
            'input_method': t.inputMethod,
            'audio_url': t.audioUrl,
            'source_video_id': sentence.sourceVideoId ?? '',
            'low_quality': sentence.lowQuality,
            'timestamp': t.timestamp.toUtc().toIso8601String(),
          });
        }
      }
      if (rows.isEmpty) {
        setState(() => _result = 'No translations match these options yet.');
        return;
      }
      final stamp = DateTime.now().toIso8601String().substring(0, 10);
      downloadFile(_encode(rows),
          'bangla_dataset_${_format.name}_$stamp.${_format.extension}');
      setState(() => _result =
          'Downloaded ${rows.length} examples from ${sentences.length} sentences.');
    } catch (e) {
      setState(() => _result = 'Export failed: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _encode(List<Map<String, dynamic>> rows) {
    switch (_format) {
      case DatasetFormat.chat:
        return rows.map((r) {
          final user = StringBuffer();
          if ((r['context_before'] as String).isNotEmpty) {
            user.writeln('Context before: ${r['context_before']}');
          }
          if ((r['context_after'] as String).isNotEmpty) {
            user.writeln('Context after: ${r['context_after']}');
          }
          if (user.isNotEmpty) user.writeln();
          user.write('Translate: ${r['english']}');
          return jsonEncode({
            'messages': [
              {'role': 'system', 'content': kDatasetSystemPrompt},
              {'role': 'user', 'content': user.toString()},
              {'role': 'assistant', 'content': r['bangla']},
            ],
          });
        }).join('\n');
      case DatasetFormat.full:
        return rows.map(jsonEncode).join('\n');
      case DatasetFormat.csv:
        String cell(Object? v) =>
            '"${(v ?? '').toString().replaceAll('"', '""')}"';
        final header = rows.first.keys.toList();
        // BOM so Excel opens the Bangla as UTF-8 instead of mojibake.
        return '﻿${header.map(cell).join(',')}\n'
            '${rows.map((r) => header.map((k) => cell(r[k])).join(',')).join('\n')}';
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Download dataset'),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              RadioGroup<DatasetFormat>(
                  groupValue: _format,
                  onChanged: (value) {
                    if (!_busy && value != null) {
                      setState(() => _format = value);
                    }
                  },
                  child: Column(children: [
                    for (final f in DatasetFormat.values)
                      RadioListTile<DatasetFormat>(
                        value: f,
                        enabled: !_busy,
                        title: Text(f.label),
                        subtitle: Text(f.description),
                      ),
                  ])),
              const Divider(),
              CheckboxListTile(
                value: _includeContext,
                onChanged:
                    _busy ? null : (v) => setState(() => _includeContext = v!),
                title: const Text('Include context lines'),
                subtitle: const Text(
                    'Teaches the model to pick words from the surrounding talk.'),
              ),
              CheckboxListTile(
                value: _includeLowQuality,
                onChanged: _busy
                    ? null
                    : (v) => setState(() => _includeLowQuality = v!),
                title: const Text('Include very short sentences'),
                subtitle: const Text('Sentences under 4 words, e.g. "Allah."'),
              ),
              if (_result != null)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(_result!),
                ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Close'),
        ),
        FilledButton.icon(
          onPressed: _busy ? null : _export,
          icon: _busy
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.download),
          label: const Text('Download'),
        ),
      ],
    );
  }
}
