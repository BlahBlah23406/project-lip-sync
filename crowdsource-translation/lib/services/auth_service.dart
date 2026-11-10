import 'package:firebase_auth/firebase_auth.dart';

/// Identity for the app.
///
/// Contributors on mobile sign in anonymously: the anonymous uid is what makes
/// "10 assignments per person per day" and "my previous translations"
/// meaningful, and it avoids asking field contributors for an account.
///
/// The admin web console signs in with email and password and additionally
/// needs the `admin: true` custom claim; `firestore.rules` grants sentence
/// management only to holders of that claim. See DEPLOYMENT.md for how to set
/// the claim.
class AuthService {
  AuthService({FirebaseAuth? auth}) : _auth = auth ?? FirebaseAuth.instance;

  final FirebaseAuth _auth;

  User? get currentUser => _auth.currentUser;

  String? get uid => _auth.currentUser?.uid;

  Stream<User?> get authStateChanges => _auth.authStateChanges();

  Future<User> signInAnonymously() async {
    final existing = _auth.currentUser;
    if (existing != null) return existing;
    final credential = await _auth.signInAnonymously();
    final user = credential.user;
    if (user == null) {
      throw Exception('Anonymous sign-in failed: user is null');
    }
    return user;
  }

  Future<User> signInWithEmail(String email, String password) async {
    final credential = await _auth.signInWithEmailAndPassword(
      email: email.trim(),
      password: password,
    );
    final user = credential.user;
    if (user == null) {
      throw Exception('Email sign-in failed: user is null');
    }
    return user;
  }

  Future<void> signOut() => _auth.signOut();

  /// Whether the signed in user carries the `admin` custom claim.
  ///
  /// [forceRefresh] re-fetches the ID token, which is required right after a
  /// claim has been granted server side (claims are baked into the token and
  /// otherwise only refresh roughly hourly).
  Future<bool> hasAdminClaim({bool forceRefresh = false}) async {
    final user = _auth.currentUser;
    if (user == null) return false;
    final token = await user.getIdTokenResult(forceRefresh);
    return token.claims?['admin'] == true;
  }
}
