import {initializeTestEnvironment, assertSucceeds, assertFails} from '@firebase/rules-unit-testing';
import {doc, setDoc, updateDoc, serverTimestamp, deleteField} from 'firebase/firestore';
const env = await initializeTestEnvironment({projectId: 'demo-providence-repairs'});
try {
  const owner = env.authenticatedContext('translator').firestore();
  const admin = env.authenticatedContext('reviewer', {admin: true}).firestore();
  const other = env.authenticatedContext('other').firestore();
  const row = {userId:'translator', sentenceId:'source', transcription:'Bangla', revision:0, reviewStatus:'pending', reviewNote:''};
  await assertSucceeds(setDoc(doc(owner,'translations/review'), row));
  await assertFails(setDoc(doc(owner,'translations/forged'), {...row, reviewStatus:'approved'}));
  const decision = {reviewStatus:'changesRequested', reviewNote:'Please revise.', reviewedBy:'reviewer', reviewedAt:serverTimestamp()};
  await assertFails(updateDoc(doc(owner,'translations/review'), decision));
  await assertFails(updateDoc(doc(other,'translations/review'), decision));
  await assertFails(updateDoc(doc(admin,'translations/review'), {...decision, reviewNote:''}));
  await assertSucceeds(updateDoc(doc(admin,'translations/review'), decision));
  await assertFails(updateDoc(doc(admin,'translations/review'), {...decision, reviewStatus:'approved'}));
  await assertSucceeds(updateDoc(doc(owner,'translations/review'), {
    transcription:'Revised Bangla', revision:1, reviewStatus:'pending', reviewNote:'',
    reviewedBy:deleteField(), reviewedAt:deleteField(),
  }));
  await assertSucceeds(updateDoc(doc(admin,'translations/review'), {...decision, reviewStatus:'approved', reviewNote:''}));
  console.log('PASS: 9 review authorization and revision checks.');
} finally { await env.cleanup(); }
