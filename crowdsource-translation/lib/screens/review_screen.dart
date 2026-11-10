import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';
import '../models/user_translation.dart';
import '../services/firestore_service.dart';

String reviewLabel(String status) => switch (status) {
      'approved' => 'Approved',
      'changesRequested' => 'Changes requested',
      _ => 'Pending review',
    };

class ReviewScreen extends StatefulWidget {
  const ReviewScreen({super.key});
  @override
  State<ReviewScreen> createState() => _ReviewScreenState();
}

class _ReviewScreenState extends State<ReviewScreen> {
  final _service = FirestoreService();
  late final _stream = _service.watchReviewQueue();
  String? _busy;

  Future<void> _review(UserTranslation item, String status) async {
    var feedback = '';
    final note = await showDialog<String>(
        context: context,
        builder: (context) => AlertDialog(
              title: Text(status == 'approved'
                  ? 'Approve translation'
                  : 'Request changes'),
              content: TextField(
                  onChanged: (value) => feedback = value,
                  maxLines: 3,
                  decoration: InputDecoration(
                      labelText: status == 'approved'
                          ? 'Reviewer note (optional)'
                          : 'Explain the changes needed')),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context),
                    child: const Text('Cancel')),
                FilledButton(
                    onPressed: () => Navigator.pop(context, feedback),
                    child: const Text('Save review')),
              ],
            ));
    if (note == null || !mounted) return;
    setState(() => _busy = item.id);
    try {
      final uid = FirebaseAuth.instance.currentUser?.uid;
      if (uid == null) throw StateError('Sign in again to review.');
      await _service.reviewTranslation(item, uid, status, note);
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Review failed: $error')));
      }
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Review translations')),
        body: StreamBuilder<List<UserTranslation>>(
          stream: _stream,
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              return Center(
                  child: Text('Could not load reviews: ${snapshot.error}'));
            }
            if (!snapshot.hasData) {
              return const Center(child: CircularProgressIndicator());
            }
            final items = snapshot.data!;
            final pending =
                items.where((t) => t.reviewStatus == 'pending').toList();
            final approved =
                items.where((t) => t.reviewStatus == 'approved').length;
            final changes =
                items.where((t) => t.reviewStatus == 'changesRequested').length;
            return Column(children: [
              Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(children: [
                    Text(
                        '${items.length} submitted · ${pending.length} pending · $approved approved · $changes changes requested'),
                    const SizedBox(height: 8),
                    LinearProgressIndicator(
                        value: items.isEmpty
                            ? 0
                            : (approved + changes) / items.length),
                  ])),
              Expanded(
                  child: pending.isEmpty
                      ? const Center(
                          child: Text('No translations awaiting review.'))
                      : ListView.builder(
                          itemCount: pending.length,
                          itemBuilder: (context, index) {
                            final item = pending[index];
                            return Card(
                              margin: const EdgeInsets.symmetric(
                                  horizontal: 16, vertical: 8),
                              child: Padding(
                                  padding: const EdgeInsets.all(16),
                                  child: Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      children: [
                                        Text(item.sentenceText,
                                            style: Theme.of(context)
                                                .textTheme
                                                .titleMedium),
                                        const Divider(),
                                        SelectableText(item.transcription),
                                        if (item.note.isNotEmpty)
                                          Text(
                                              'Contributor note: ${item.note}'),
                                        Text('Revision ${item.revision}'),
                                        Wrap(spacing: 12, children: [
                                          FilledButton(
                                              onPressed: _busy != null
                                                  ? null
                                                  : () =>
                                                      _review(item, 'approved'),
                                              child: const Text('Approve')),
                                          OutlinedButton(
                                              onPressed: _busy != null
                                                  ? null
                                                  : () => _review(
                                                      item, 'changesRequested'),
                                              child: const Text(
                                                  'Request changes')),
                                        ]),
                                      ])),
                            );
                          })),
            ]);
          },
        ),
      );
}
