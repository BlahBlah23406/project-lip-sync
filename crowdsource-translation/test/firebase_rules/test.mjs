import fs from 'node:fs';
import {initializeTestEnvironment, assertSucceeds, assertFails} from '@firebase/rules-unit-testing';
import {ref, uploadBytes, getBytes} from 'firebase/storage';
import {doc, getDoc, setDoc, runTransaction, Timestamp, increment} from 'firebase/firestore';
const env = await initializeTestEnvironment({projectId:'demo-providence-repairs'});
try {
  const owner=env.authenticatedContext('owner');
  const other=env.authenticatedContext('other');
  const anon=env.unauthenticatedContext();
  const admin=env.authenticatedContext('admin',{admin:true});
  const path='translations/owner/source/recording.wav';
  const bytes=new Uint8Array(100);
  await assertSucceeds(uploadBytes(ref(owner.storage(),path), bytes,{contentType:'audio/wav'}));
  await assertSucceeds(getBytes(ref(owner.storage(),path)));
  await assertSucceeds(getBytes(ref(admin.storage(),path)));
  await assertFails(getBytes(ref(other.storage(),path)));
  await assertFails(getBytes(ref(anon.storage(),path)));
  await assertFails(uploadBytes(ref(other.storage(),path),bytes,{contentType:'audio/wav'}));
  await assertFails(uploadBytes(ref(anon.storage(),path),bytes,{contentType:'audio/wav'}));
  await assertFails(uploadBytes(ref(owner.storage(),path),bytes,{contentType:'text/plain'}));
  await assertFails(uploadBytes(ref(owner.storage(),path),new Uint8Array(44),{contentType:'audio/wav'}));
  await assertFails(uploadBytes(ref(owner.storage(),path),new Uint8Array(20*1024*1024+1),{contentType:'audio/wav'}));
  await assertFails(uploadBytes(ref(owner.storage(),'outside/recording.wav'),bytes,{contentType:'audio/wav'}));
  await env.withSecurityRulesDisabled(async ctx=>{
    await setDoc(doc(ctx.firestore(),'sentences/source'),{text:'Original.',translationCount:0,translations:[]});
    await setDoc(doc(ctx.firestore(),'translations/someone-else'),{userId:'other'});
  });
  const db=owner.firestore();
  const sentence=doc(db,'sentences/source');
  const translation=doc(db,'translations/draft');
  const quota=doc(db,'users/owner/dailyQuota/2026-10-18');
  await assertSucceeds(getDoc(translation));
  await assertFails(getDoc(doc(db,'translations/someone-else')));
  async function submit(){
    return runTransaction(db,async tx=>{
      const source=await tx.get(sentence);
      const saved=await tx.get(translation);
      if(saved.exists()) return;
      await tx.get(quota);
      tx.set(quota,{count:1,dayKey:'2026-10-18',updatedAt:Timestamp.now()});
      tx.set(translation,{userId:'owner',sentenceId:'source',transcription:'বাংলা'});
      tx.update(sentence,{translationCount:increment(1),translations:[{id:'draft',transcription:'বাংলা'}],transcription:'বাংলা',audioUrl:''});
    });
  }
  await assertSucceeds(submit());
  await assertSucceeds(submit());
  const q=await getDoc(quota);
  if(q.data().count!==1) throw Error('retry charged quota');
  console.log('PASS: 11 Storage allow/deny checks; missing draft/foreign history reads; atomic submission and idempotent retry.');
} finally {await env.cleanup();}
