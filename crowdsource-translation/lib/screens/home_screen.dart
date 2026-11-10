import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';

import '../models/daily_quota.dart';
import '../models/translation.dart';
import '../services/firestore_service.dart';
import 'my_translations_screen.dart';
import 'translation_screen.dart';

/// Contributor landing screen.
///
/// Two jobs: show how much of today's allowance is left, and let the
/// contributor pick how they want to enter a translation - by voice, by typing,
/// or by writing it out by hand. Reviewing and redoing past work lives here too
/// and is deliberately *not* gated by the daily allowance.
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key, required this.userId});

  final String userId;

  @override
  Widget build(BuildContext context) {
    final firestoreService = FirestoreService();

    return Scaffold(
      appBar: AppBar(title: const Text('Crowdsource Translation')),
      body: SafeArea(
        child: StreamBuilder<DailyQuota>(
          stream: firestoreService.watchDailyQuota(userId),
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              return Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text('Could not load your daily progress: ${snapshot.error}'),
                ),
              );
            }

            final quota = snapshot.data ??
                DailyQuota.empty(DailyQuota.keyFor(DateTime.now()));

            return ListView(
              padding: const EdgeInsets.all(20),
              children: [
                _QuotaCard(quota: quota),
                const SizedBox(height: 24),
                Text(
                  'Start a translation',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 4),
                Text(
                  kIsWeb
                      ? 'Type the Bangla translation. Speaking and handwriting are in the Android and iOS apps.'
                      : 'Choose how you want to enter the Bangla translation.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                const SizedBox(height: 12),
                // Speech and handwriting recognition run on device and have
                // no web build, so the browser offers typing only.
                if (!kIsWeb)
                _ModeTile(
                  icon: Icons.mic,
                  title: 'Speak it',
                  subtitle: 'Record your voice; it is transcribed on the device.',
                  enabled: !quota.isExhausted,
                  onTap: () => _openTranslation(context, TranslationInputMethod.voice),
                ),
                _ModeTile(
                  icon: Icons.keyboard,
                  title: 'Type it',
                  subtitle: 'Enter the translation with the keyboard.',
                  enabled: !quota.isExhausted,
                  onTap: () => _openTranslation(context, TranslationInputMethod.text),
                ),
                if (!kIsWeb)
                _ModeTile(
                  icon: Icons.draw,
                  title: 'Write it by hand',
                  subtitle: 'Write on screen with a finger or stylus.',
                  enabled: !quota.isExhausted,
                  onTap: () =>
                      _openTranslation(context, TranslationInputMethod.handwriting),
                ),
                if (quota.isExhausted)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                      'You have used all $kDailyAssignmentLimit assignments for today. '
                      'New assignments unlock tomorrow.',
                      style: TextStyle(color: Theme.of(context).colorScheme.error),
                    ),
                  ),
                const SizedBox(height: 28),
                Text(
                  'Your work',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 12),
                _ModeTile(
                  icon: Icons.history,
                  title: 'My translations',
                  subtitle:
                      'Review what you have submitted and redo any of them. '
                      'Redoing does not use your daily allowance.',
                  enabled: true,
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute(
                      builder: (_) => MyTranslationsScreen(userId: userId),
                    ),
                  ),
                ),
              ],
            );
          },
        ),
      ),
    );
  }

  void _openTranslation(BuildContext context, TranslationInputMethod method) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => TranslationScreen(userId: userId, inputMethod: method),
      ),
    );
  }
}

class _QuotaCard extends StatelessWidget {
  const _QuotaCard({required this.quota});

  final DailyQuota quota;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final progress = quota.limit == 0 ? 0.0 : quota.used / quota.limit;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.today, color: theme.colorScheme.primary),
                const SizedBox(width: 12),
                Text("Today's assignments", style: theme.textTheme.titleMedium),
              ],
            ),
            const SizedBox(height: 16),
            LinearProgressIndicator(
              value: progress.clamp(0.0, 1.0),
              minHeight: 8,
              borderRadius: BorderRadius.circular(4),
            ),
            const SizedBox(height: 12),
            Text(
              '${quota.used} of ${quota.limit} used  -  ${quota.remaining} remaining',
              style: theme.textTheme.bodyMedium,
            ),
          ],
        ),
      ),
    );
  }
}

class _ModeTile extends StatelessWidget {
  const _ModeTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.enabled,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final bool enabled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ListTile(
        enabled: enabled,
        leading: CircleAvatar(child: Icon(icon)),
        title: Text(title),
        subtitle: Text(subtitle),
        trailing: const Icon(Icons.chevron_right),
        onTap: enabled ? onTap : null,
      ),
    );
  }
}
