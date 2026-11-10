import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'review_screen.dart' show reviewLabel;

import '../models/translation.dart';
import '../models/user_translation.dart';
import '../services/firestore_service.dart';
import 'translation_screen.dart';

/// The contributor's own translation history, and the entry point for redoing
/// any of them.
///
/// Only translations recorded in the top level `translations` collection appear
/// here. Submissions made before that collection existed live only inside the
/// sentence documents and cannot be attributed to a contributor, so they are
/// not listed - see DEPLOYMENT.md for the optional backfill.
class MyTranslationsScreen extends StatelessWidget {
  const MyTranslationsScreen({super.key, required this.userId});

  final String userId;

  @override
  Widget build(BuildContext context) {
    final firestoreService = FirestoreService();

    return Scaffold(
      appBar: AppBar(title: const Text('My translations')),
      body: SafeArea(
        child: StreamBuilder<List<UserTranslation>>(
          stream: firestoreService.watchMyTranslations(userId),
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              return Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text(
                      'Could not load your translations: ${snapshot.error}'),
                ),
              );
            }
            if (snapshot.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }

            final translations = snapshot.data ?? const <UserTranslation>[];
            if (translations.isEmpty) {
              return const Center(
                child: Padding(
                  padding: EdgeInsets.all(32),
                  child: Text(
                    'You have not submitted any translations yet.\n'
                    'Once you do, they will show up here and you can redo them.',
                    textAlign: TextAlign.center,
                  ),
                ),
              );
            }

            return ListView.builder(
              padding: const EdgeInsets.all(16),
              itemCount: translations.length + 1,
              itemBuilder: (context, index) {
                if (index == 0) {
                  final approved = translations
                      .where((t) => t.reviewStatus == 'approved')
                      .length;
                  final changes = translations
                      .where((t) => t.reviewStatus == 'changesRequested')
                      .length;
                  return Padding(
                      padding: const EdgeInsets.only(bottom: 16),
                      child: Text(
                          '${translations.length} submitted | $approved approved | $changes changes requested | ${translations.length - approved - changes} pending'));
                }
                return _TranslationCard(
                    translation: translations[index - 1], userId: userId);
              },
            );
          },
        ),
      ),
    );
  }
}

class _TranslationCard extends StatelessWidget {
  const _TranslationCard({required this.translation, required this.userId});

  final UserTranslation translation;
  final String userId;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              translation.sentenceText.isEmpty
                  ? '[original sentence unavailable]'
                  : translation.sentenceText,
              style: theme.textTheme.titleSmall,
            ),
            const Divider(height: 20),
            Text(
              translation.transcription.isEmpty
                  ? '[no translation text]'
                  : translation.transcription,
              style: theme.textTheme.bodyLarge,
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 4,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Chip(label: Text(reviewLabel(translation.reviewStatus))),
                Chip(
                  visualDensity: VisualDensity.compact,
                  label: Text(translation.inputMethod.label),
                ),
                if (translation.hasBeenRedone)
                  Chip(
                    visualDensity: VisualDensity.compact,
                    label: Text('Revision ${translation.revision}'),
                  ),
                Text(
                  _formatDate(translation.updatedAt),
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
            if (translation.reviewNote.isNotEmpty)
              Text('Reviewer: ${translation.reviewNote}'),
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton.tonalIcon(
                onPressed: () => _chooseRedoMethod(context),
                icon: const Icon(Icons.refresh),
                label: const Text('Redo'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Redoing has to offer at least voice and text; handwriting is offered too
  /// since the main screen supports it.
  Future<void> _chooseRedoMethod(BuildContext context) async {
    final method = await showModalBottomSheet<TranslationInputMethod>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text('How do you want to redo this translation?'),
            ),
            if (!kIsWeb)
              ListTile(
                leading: const Icon(Icons.mic),
                title: const Text('Speak it again'),
                onTap: () =>
                    Navigator.pop(sheetContext, TranslationInputMethod.voice),
              ),
            ListTile(
              leading: const Icon(Icons.keyboard),
              title: const Text('Type it'),
              onTap: () =>
                  Navigator.pop(sheetContext, TranslationInputMethod.text),
            ),
            if (!kIsWeb)
              ListTile(
                leading: const Icon(Icons.draw),
                title: const Text('Write it by hand'),
                onTap: () => Navigator.pop(
                    sheetContext, TranslationInputMethod.handwriting),
              ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );

    if (method == null || !context.mounted) return;

    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => TranslationScreen(
          userId: userId,
          inputMethod: method,
          redoOf: translation,
        ),
      ),
    );
  }

  String _formatDate(DateTime value) {
    final local = value.toLocal();
    String two(int n) => n.toString().padLeft(2, '0');
    return '${local.year}-${two(local.month)}-${two(local.day)} '
        '${two(local.hour)}:${two(local.minute)}';
  }
}
