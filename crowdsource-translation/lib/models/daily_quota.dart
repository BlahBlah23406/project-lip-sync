/// Maximum number of translation assignments a single contributor may submit
/// per day.
///
/// IMPORTANT: this constant is mirrored in `firestore.rules`
/// (`users/{userId}/dailyQuota/{dayKey}`). If you change it here you must
/// change it there too, otherwise the client and the server disagree about the
/// cap and submissions start failing with a permission error.
const int kDailyAssignmentLimit = 10;

/// How many translations a single sentence collects before it stops being
/// handed out. This was previously an unnamed `5` inside the Firestore query.
const int kMaxTranslationsPerSentence = 5;

/// A contributor's assignment usage for one calendar day, stored at
/// `users/{userId}/dailyQuota/{dayKey}`.
///
/// The day key is the contributor's *local* calendar date. Using local time
/// keeps "your daily 10" aligned with the contributor's own day, at the cost of
/// letting someone gain extra assignments by changing the device timezone. That
/// trade is documented in DEPLOYMENT.md; closing it requires a server side
/// (Cloud Functions) counter.
class DailyQuota {
  final String dayKey;
  final int used;

  const DailyQuota({required this.dayKey, required this.used});

  const DailyQuota.empty(this.dayKey) : used = 0;

  int get limit => kDailyAssignmentLimit;

  int get remaining {
    final left = kDailyAssignmentLimit - used;
    return left < 0 ? 0 : left;
  }

  bool get isExhausted => remaining == 0;

  /// `yyyy-MM-dd` for the given local time, used as the quota document id.
  static String keyFor(DateTime when) {
    final local = when.toLocal();
    final month = local.month.toString().padLeft(2, '0');
    final day = local.day.toString().padLeft(2, '0');
    return '${local.year}-$month-$day';
  }

  factory DailyQuota.fromMap(Map<String, dynamic>? data, String dayKey) {
    if (data == null) return DailyQuota.empty(dayKey);
    return DailyQuota(
      dayKey: dayKey,
      used: (data['count'] as num?)?.toInt() ?? 0,
    );
  }
}

/// Thrown when a contributor tries to take on more work than
/// [kDailyAssignmentLimit] allows for the current day.
class DailyLimitReachedException implements Exception {
  final int used;
  final int limit;

  const DailyLimitReachedException({required this.used, this.limit = kDailyAssignmentLimit});

  @override
  String toString() =>
      'Daily assignment limit reached ($used/$limit). Try again tomorrow.';
}
