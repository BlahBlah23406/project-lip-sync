Run with Java 21+, Node, and the Firebase CLI:

```sh
npm ci --prefix test/firebase_rules
firebase emulators:exec --only storage,firestore --project demo-providence-repairs --config firebase.test.json 'node test/firebase_rules/test.mjs'
```

The demo project prevents accidental live database access. These checks exercise
Storage owner/admin access, upload size/type restrictions, missing draft reads,
and atomic submission/retry under the real Firestore rules. Flutter tests cover
service behavior, legacy redo, missing sources, quotas, ink and recording bytes.
