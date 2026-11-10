import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_core/firebase_core.dart';
import 'screens/admin_home.dart';
import 'screens/admin_login.dart';
import 'screens/home_screen.dart';
import 'services/auth_service.dart';
import 'firebase_options.dart'; // Uncomment when generated

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  
  try {
    // Try to initialize Firebase. 
    // On Web, options are required. On Mobile, google-services.json is used.
    // If you haven't run flutterfire configure, this might fail or require options.
    await Firebase.initializeApp(
        options: DefaultFirebaseOptions.currentPlatform,
    );
    debugPrint('Firebase initialized successfully');
  } catch (e) {
    debugPrint('Firebase initialization failed: $e');
    // You might want to show an error screen here or handle it gracefully
  }

  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Crowdsource Translation',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal),
        useMaterial3: true,
      ),
      // Web serves both sides from one site: /admin is the console, every
      // other path is the contributor app. Mobile is always the contributor.
      home: kIsWeb && _isAdminPath(Uri.base)
          ? const _AdminGate()
          : const _ContributorGate(),
    );
  }
}

/// True for `/admin` (hosted) and `/#/admin` (local `flutter run`).
bool _isAdminPath(Uri uri) =>
    uri.path.startsWith('/admin') || uri.fragment.startsWith('/admin');

/// Shows the admin console only to an account with the `admin` claim.
///
/// Contributors on the same site sign in anonymously, so an anonymous session
/// is treated as "not signed in" rather than "not an admin".
class _AdminGate extends StatelessWidget {
  const _AdminGate();

  @override
  Widget build(BuildContext context) {
    final auth = AuthService();
    return StreamBuilder<User?>(
      stream: FirebaseAuth.instance.idTokenChanges(),
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return const Scaffold(body: Center(child: CircularProgressIndicator()));
        }
        final user = snapshot.data;
        if (user == null || user.isAnonymous) {
          return AdminLoginScreen(authService: auth);
        }
        return FutureBuilder<bool>(
          key: ValueKey(user.uid),
          future: auth.hasAdminClaim(forceRefresh: true),
          builder: (context, claim) {
            if (!claim.hasData) {
              return const Scaffold(
                  body: Center(child: CircularProgressIndicator()));
            }
            return claim.data!
                ? const AdminHome()
                : AdminLoginScreen(authService: auth, notAdmin: true);
          },
        );
      },
    );
  }
}

/// Signs the contributor in anonymously before showing the home screen.
///
/// Everything on the contributor side is keyed by uid - the daily allowance
/// and "my translations" both need one - so the app cannot render until sign
/// in has resolved.
class _ContributorGate extends StatefulWidget {
  const _ContributorGate();

  @override
  State<_ContributorGate> createState() => _ContributorGateState();
}

class _ContributorGateState extends State<_ContributorGate> {
  late Future<String> _signIn;

  @override
  void initState() {
    super.initState();
    _signIn = AuthService().signInAnonymously().then((user) => user.uid);
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<String>(
      future: _signIn,
      builder: (context, snapshot) {
        if (snapshot.hasError) {
          return Scaffold(
            body: Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text('Could not sign in: ${snapshot.error}'),
                    const SizedBox(height: 16),
                    ElevatedButton(
                      onPressed: () => setState(() {
                        _signIn = AuthService()
                            .signInAnonymously()
                            .then((user) => user.uid);
                      }),
                      child: const Text('Retry'),
                    ),
                  ],
                ),
              ),
            ),
          );
        }
        if (!snapshot.hasData || (snapshot.data ?? '').isEmpty) {
          return const Scaffold(
            body: Center(child: CircularProgressIndicator()),
          );
        }
        return HomeScreen(userId: snapshot.data ?? '');
      },
    );
  }
}
