'use strict';

// --- State ---
let player = null;
let segments = [];
let syncInterval = null;
let lastSegmentIndex = -1;
let subtitleHasBeenShown = false;   // true once any segment has been displayed
let pendingVideoId = null;
let syncOffsetSeconds = 0;   // positive = show subtitles earlier (ahead of timestamps)
let currentVideoId = '';
let currentVideoUrl = '';
let dubPollInterval = null;
let isDubActive = false;
let feedbackRatings = { overall_rating: 0, lip_sync_rating: 0, audio_clarity_rating: 0 };
let segmentIssues = [];

// --- DOM References ---
const urlForm        = document.getElementById('urlForm');
const urlInput       = document.getElementById('urlInput');
const submitBtn      = document.getElementById('submitBtn');
const errorMsg       = document.getElementById('errorMsg');
const loadingSection = document.getElementById('loadingSection');
const loadingText    = document.getElementById('loadingText');
const playerSection  = document.getElementById('playerSection');
const inputSection   = document.getElementById('inputSection');
const subtitleText   = document.getElementById('subtitleText');
const segmentCount   = document.getElementById('segmentCount');
const newVideoBtn    = document.getElementById('newVideoBtn');
const syncMinusBtn   = document.getElementById('syncMinus');
const syncPlusBtn    = document.getElementById('syncPlus');
const syncResetBtn   = document.getElementById('syncReset');
const syncOffsetEl   = document.getElementById('syncOffset');
const dubDownload    = document.getElementById('dubDownload');
const audioDownload  = document.getElementById('audioDownload');
const dubPlayerPanel   = document.getElementById('dubPlayerPanel');
const originalAudioBtn = document.getElementById('originalAudioBtn');
const banglaDubBtn     = document.getElementById('banglaDubBtn');
const dubbedAudio      = document.getElementById('dubbedAudio');
const statTime       = document.getElementById('statTime');
const statTokens     = document.getElementById('statTokens');
const statTokensLabel = document.getElementById('statTokensLabel');
const feedbackPanel  = document.getElementById('feedbackPanel');
const segmentIssueList = document.getElementById('segmentIssueList');
const feedbackSubmit = document.getElementById('feedbackSubmit');
const feedbackStatus = document.getElementById('feedbackStatus');
const feedbackComment = document.getElementById('feedbackComment');
const feedbackSuggestion = document.getElementById('feedbackSuggestion');
const backToPlazaBtn = document.getElementById('backToPlazaBtn');


// --- Sync Offset Controls ---
syncMinusBtn.addEventListener('click', () => adjustOffset(-1));
syncPlusBtn.addEventListener('click',  () => adjustOffset(+1));
syncResetBtn.addEventListener('click', () => {
  syncOffsetSeconds = 0;
  lastSegmentIndex = -1;   // force subtitle refresh
  updateOffsetDisplay();
});

function adjustOffset(delta) {
  syncOffsetSeconds = Math.max(-10, Math.min(10, syncOffsetSeconds + delta));
  lastSegmentIndex = -1;   // force subtitle refresh immediately
  updateOffsetDisplay();
}

function updateOffsetDisplay() {
  const sign = syncOffsetSeconds > 0 ? '+' : '';
  syncOffsetEl.textContent = `${sign}${syncOffsetSeconds.toFixed(1)}s`;
  syncOffsetEl.classList.toggle('nonzero', syncOffsetSeconds !== 0);
}

// --- Dub Audio Track Controls ---
originalAudioBtn.addEventListener('click', () => setAudioTrack(false));
banglaDubBtn.addEventListener('click', () => setAudioTrack(true));

function setAudioTrack(useDub) {
  isDubActive = useDub;
  
  if (useDub) {
    originalAudioBtn.classList.remove('active');
    banglaDubBtn.classList.add('active');
    
    // Mute YouTube player and unmute dubbed audio
    if (player && typeof player.mute === 'function') {
      player.mute();
    }
    dubbedAudio.muted = false;
    
    // Sync current time and playback state
    if (player && typeof player.getCurrentTime === 'function') {
      dubbedAudio.currentTime = player.getCurrentTime();
    }
    
    // If YouTube is playing, start audio
    if (player && typeof player.getPlayerState === 'function' && player.getPlayerState() === 1) {
      dubbedAudio.play().catch(err => console.warn('Audio play failed:', err));
    }
  } else {
    banglaDubBtn.classList.remove('active');
    originalAudioBtn.classList.add('active');
    
    // Unmute YouTube player and pause dubbed audio
    if (player && typeof player.unMute === 'function') {
      player.unMute();
    }
    dubbedAudio.muted = true;
    dubbedAudio.pause();
  }
}

// --- Form Submission ---
urlForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;

  setError('');
  showLoading('Initiating dubbing pipeline…');
  submitBtn.disabled = true;

  // Reset sync offset and dub state for each new video
  syncOffsetSeconds = 0;
  updateOffsetDisplay();
  currentVideoUrl = url;
  resetDubUI();

  try {
    const res = await fetch('/api/dub', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Failed to start dubbing.');
    }

    pollDubStatus(data.job_id);

  } catch (err) {
    setError(err.message);
    showInputOnly();
    submitBtn.disabled = false;
  }
});

// --- Plaza watch view support ---
function loadPlazaWatchView() {
  const params = new URLSearchParams(window.location.search);
  const videoId = params.get('video_id');
  if (!videoId) return false;

  setError('');
  showLoading('Loading dubbed video from plaza…');
  resetDubUI();

  fetch(`/api/plaza/${encodeURIComponent(videoId)}`)
    .then(res => {
      if (!res.ok) throw new Error(`Plaza video not found (${res.status}).`);
      return res.json();
    })
    .then(async (video) => {
      const manifest = video.manifest || {};
      const audioUrl = video.audio_url || `/output/${videoId}/${videoId}_dubbed.mp3`;
      const videoUrl = video.video_url || `/output/${videoId}/${videoId}_dubbed.mp4`;
      const title = video.title || videoId;
      const timeTaken = manifest.elapsed_seconds ?? 0;
      const tokens = manifest.tokens;

      // Plaza manifests are metadata-only; load segment-level transcript.
      const transcriptRes = await fetch(`/api/transcript/${encodeURIComponent(videoId)}`);
      let banglaSegments = [];
      if (transcriptRes.ok) {
        const transcript = await transcriptRes.json();
        banglaSegments = transcript.segments || [];
      } else {
        console.warn('Plaza transcript not found:', transcriptRes.status);
      }

      segments = banglaSegments;
      segmentCount.textContent = `${segments.length} segments translated`;
      pendingVideoId = videoId;

      initOrLoadPlayer(videoId);

      dubDownload.href = videoUrl;
      dubDownload.hidden = false;
      audioDownload.href = audioUrl;
      audioDownload.hidden = false;

      dubPlayerPanel.hidden = false;
      dubbedAudio.src = audioUrl;
      dubbedAudio.load();
      setAudioTrack(false);

      statTime.textContent = `${timeTaken.toFixed(1)}s`;
      if (tokens && tokens.cached) {
        statTokensLabel.textContent = 'Claude Cache';
        statTokens.textContent = 'Cached (0 tokens)';
        statTokens.classList.add('cached');
      } else if (tokens) {
        statTokensLabel.textContent = 'Claude Tokens';
        const total = (tokens.total_tokens || 0).toLocaleString();
        const inp = (tokens.input_tokens || 0).toLocaleString();
        const out = (tokens.output_tokens || 0).toLocaleString();
        statTokens.textContent = `${total} (${inp} in / ${out} out)`;
        statTokens.classList.remove('cached');
      } else {
        statTokensLabel.textContent = 'Claude Tokens';
        statTokens.textContent = 'N/A';
        statTokens.classList.remove('cached');
      }

      currentVideoId = videoId;
      setupFeedbackUI(videoId, banglaSegments);
      showPlayerFromPlaza();
    })
    .catch(err => {
      setError(err.message);
      showInputOnly();
    });
  return true;
}

function showPlayerFromPlaza() {
  if (backToPlazaBtn) backToPlazaBtn.hidden = false;
  if (newVideoBtn) newVideoBtn.hidden = true;
}

function showPlayerFromHomepage() {
  if (backToPlazaBtn) backToPlazaBtn.hidden = true;
  if (newVideoBtn) newVideoBtn.hidden = false;
}

// Run watch view detection on load
document.addEventListener('DOMContentLoaded', () => {
  loadPlazaWatchView();
});


// --- New Video Button ---
newVideoBtn.addEventListener('click', () => {
  stopSync();
  segments = [];
  lastSegmentIndex = -1;
  subtitleHasBeenShown = false;
  currentVideoUrl = '';
  currentVideoId = '';
  resetDubUI();
  resetFeedbackUI();
  setSubtitle('', true);
  showInputOnly();
  urlInput.value = '';
  urlInput.focus();
});

// --- YouTube IFrame API ---
window.onYouTubeIframeAPIReady = function () {
  if (pendingVideoId) {
    createPlayer(pendingVideoId);
    pendingVideoId = null;
  }
};

function initOrLoadPlayer(videoId) {
  if (!window.YT || !window.YT.Player) {
    pendingVideoId = videoId;
    return;
  }

  if (player) {
    stopSync();
    player.loadVideoById(videoId);
  } else {
    createPlayer(videoId);
  }
}

function createPlayer(videoId) {
  player = new YT.Player('ytPlayer', {
    videoId,
    playerVars: { rel: 0, modestbranding: 1 },
    events: {
      onReady: onPlayerReady,
      onStateChange: onPlayerStateChange,
    },
  });
}

function onPlayerReady() {
  inputSection.hidden = true;
  loadingSection.hidden = true;
  playerSection.hidden = false;
  // Only show the placeholder if the video isn't already playing. This avoids
  // clobbering subtitles that are already rendering after a quick state change.
  if (player && typeof player.getPlayerState === 'function' && player.getPlayerState() === 1) {
    syncSubtitle();
  } else {
    setSubtitle('▶ Play the video to see Bangla subtitles', true);
  }
}

function onPlayerStateChange(event) {
  const YT_PLAYING = 1;
  const YT_PAUSED  = 2;
  const YT_ENDED   = 0;

  if (event.data === YT_PLAYING) {
    startSync();
    // Update subtitles immediately so the placeholder clears the moment playback starts.
    syncSubtitle();
    if (isDubActive && dubbedAudio) {
      dubbedAudio.currentTime = player.getCurrentTime();
      dubbedAudio.play().catch(err => console.warn('Audio play failed:', err));
    }
  } else if (event.data === YT_PAUSED) {
    stopSync();
    if (isDubActive && dubbedAudio) {
      dubbedAudio.pause();
    }
  } else if (event.data === YT_ENDED) {
    stopSync();
    setSubtitle('', true);
    if (isDubActive && dubbedAudio) {
      dubbedAudio.pause();
      dubbedAudio.currentTime = 0;
    }
  }
}

// --- Subtitle Synchronization ---
function startSync() {
  if (syncInterval) return;
  syncInterval = setInterval(syncSubtitle, 100);  // 100ms for snappier response
}

function stopSync() {
  if (syncInterval) {
    clearInterval(syncInterval);
    syncInterval = null;
  }
}

function syncSubtitle() {
  if (!player || typeof player.getCurrentTime !== 'function') return;

  const playerTime = player.getCurrentTime();

  // If dub is active, ensure audio playback stays synchronized
  if (isDubActive && dubbedAudio) {
    const isPlaying = player.getPlayerState() === 1;
    
    // Sync play/pause states
    if (isPlaying && dubbedAudio.paused) {
      dubbedAudio.play().catch(err => console.warn(err));
    } else if (!isPlaying && !dubbedAudio.paused) {
      dubbedAudio.pause();
    }
    
    // Sync time if drift exceeds 0.3s
    if (Math.abs(dubbedAudio.currentTime - playerTime) > 0.3) {
      dubbedAudio.currentTime = playerTime;
    }
  }

  // Apply offset: positive offset looks further ahead in the transcript,
  // making subtitles appear earlier relative to the audio.
  const currentTime = playerTime + syncOffsetSeconds;
  const idx = findSegmentIndex(currentTime);

  if (idx === lastSegmentIndex) return;
  lastSegmentIndex = idx;

  if (idx === -1) {
    // Keep the initial placeholder until the first segment's start time is reached.
    // After a segment has been shown, gaps between segments should be empty.
    if (subtitleHasBeenShown) {
      setSubtitle('', true);
    }
  } else {
    subtitleHasBeenShown = true;
    setSubtitle(segments[idx].text, false);
  }
}

// Binary search: find segment whose [start, start+duration) contains t.
function findSegmentIndex(t) {
  let lo = 0;
  let hi = segments.length - 1;

  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const seg = segments[mid];
    const end = seg.start + seg.duration;

    if (t < seg.start) {
      hi = mid - 1;
    } else if (t >= end) {
      lo = mid + 1;
    } else {
      return mid;
    }
  }
  return -1;
}

// --- UI Helpers ---
function setSubtitle(text, isEmpty) {
  // Update text immediately — no setTimeout delay so timing never drifts.
  // Restart the CSS animation by removing the class, forcing a reflow, then re-adding.
  subtitleText.classList.remove('changed');
  void subtitleText.offsetWidth; // trigger reflow so animation restarts
  subtitleText.textContent = text;
  subtitleText.classList.toggle('empty', isEmpty);
  subtitleText.classList.add('changed');
}

function setError(msg) {
  errorMsg.textContent = msg;
  errorMsg.hidden = !msg;
}

function showLoading(msg) {
  loadingText.textContent = msg;
  inputSection.hidden = false;
  loadingSection.hidden = false;
  playerSection.hidden = true;
}

function showInputOnly() {
  inputSection.hidden = false;
  loadingSection.hidden = true;
  playerSection.hidden = true;
}

// --- Dubbing Pipeline Polling ---
function pollDubStatus(jobId) {
  if (dubPollInterval) clearInterval(dubPollInterval);
  
  dubPollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/dub/status/${jobId}`);
      const data = await res.json();

      if (data.progress) {
        loadingText.textContent = data.progress;
      }

      if (data.status === 'done') {
        clearInterval(dubPollInterval);
        dubPollInterval = null;

        // Hide loading and show player section
        inputSection.hidden = true;
        loadingSection.hidden = true;
        playerSection.hidden = false;

        // Load segments & video details
        segments = data.segments;
        lastSegmentIndex = -1;
        subtitleHasBeenShown = false;
        segmentCount.textContent = `${segments.length} segments translated`;
        pendingVideoId = data.video_id;

        // Show player
        initOrLoadPlayer(data.video_id);

        // Configure player & download paths
        dubDownload.href = data.url;
        dubDownload.hidden = false;
        audioDownload.href = data.audio_url;
        audioDownload.hidden = false;

        // Show live playback panel and set up the audio source
        dubPlayerPanel.hidden = false;
        dubbedAudio.src = data.audio_url;
        dubbedAudio.load();
        setAudioTrack(false); // Default to Original English initially

        // Format stats
        statTime.textContent = `${data.time_taken.toFixed(1)}s`;

        if (data.tokens && data.tokens.cached) {
          statTokensLabel.textContent = 'Claude Cache';
          statTokens.textContent = 'Cached (0 tokens)';
          statTokens.classList.add('cached');
        } else if (data.tokens) {
          statTokensLabel.textContent = 'Claude Tokens';
          const total = data.tokens.total_tokens.toLocaleString();
          const inp = data.tokens.input_tokens.toLocaleString();
          const out = data.tokens.output_tokens.toLocaleString();
          statTokens.textContent = `${total} (${inp} in / ${out} out)`;
          statTokens.classList.remove('cached');
        } else {
          statTokensLabel.textContent = 'Claude Tokens';
          statTokens.textContent = 'N/A';
          statTokens.classList.remove('cached');
        }

        // Setup human evaluation panel
        currentVideoId = data.video_id;
        setupFeedbackUI(data.video_id, data.segments);
        showPlayerFromHomepage();

        submitBtn.disabled = false;
      } else if (data.status === 'error') {
        clearInterval(dubPollInterval);
        dubPollInterval = null;
        setError(data.error || 'Dubbing failed.');
        showInputOnly();
        submitBtn.disabled = false;
      }
    } catch (err) {
      console.warn('Polling error:', err);
      // transient fetch error — keep polling
    }
  }, 3000);
}

function resetDubUI() {
  if (dubPollInterval) {
    clearInterval(dubPollInterval);
    dubPollInterval = null;
  }
  submitBtn.disabled = false;
  lastSegmentIndex = -1;
  subtitleHasBeenShown = false;
  dubDownload.hidden = true;
  dubDownload.href = '';
  audioDownload.hidden = true;
  audioDownload.href = '';

  // Reset dub player panel and dubbed audio
  if (dubPlayerPanel) dubPlayerPanel.hidden = true;
  isDubActive = false;
  if (dubbedAudio) {
    dubbedAudio.pause();
    dubbedAudio.src = '';
  }
  if (originalAudioBtn) originalAudioBtn.classList.add('active');
  if (banglaDubBtn) banglaDubBtn.classList.remove('active');
  if (player && typeof player.unMute === 'function') {
    player.unMute();
  }
}

// --- Human Evaluation UI ---

const RATING_LABELS = ['', 'Poor', 'Fair', 'Good', 'Very Good', 'Excellent'];

function createStarRow(containerId, labelId, field) {
  const container = document.getElementById(containerId);
  const label = document.getElementById(labelId);
  container.innerHTML = '';
  for (let i = 1; i <= 5; i++) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'star-btn';
    btn.textContent = '★';
    btn.dataset.value = i;
    btn.addEventListener('click', () => {
      feedbackRatings[field] = i;
      updateStarDisplay(container, i, label);
    });
    container.appendChild(btn);
  }
}

function updateStarDisplay(container, value, label) {
  Array.from(container.children).forEach((btn, idx) => {
    btn.classList.toggle('active', idx < value);
  });
  if (label) label.textContent = RATING_LABELS[value] || '';
}

function setupFeedbackUI(videoId, segs) {
  currentVideoId = videoId;
  segmentIssues = [];
  feedbackRatings = { overall_rating: 0, lip_sync_rating: 0, audio_clarity_rating: 0 };
  document.querySelector('input[name="syncIssue"][value="none"]').checked = true;
  feedbackComment.value = '';
  feedbackSuggestion.value = '';
  feedbackStatus.textContent = '';
  feedbackStatus.className = 'feedback-status';
  feedbackSubmit.disabled = false;
  feedbackSubmit.textContent = 'Submit feedback';
  feedbackPanel.hidden = false;

  createStarRow('overallStars', 'overallLabel', 'overall_rating');
  createStarRow('lipSyncStars', 'lipSyncLabel', 'lip_sync_rating');
  createStarRow('audioStars', 'audioLabel', 'audio_clarity_rating');

  renderSegmentIssueList(segs);
}

function resetFeedbackUI() {
  currentVideoId = '';
  segmentIssues = [];
  feedbackRatings = { overall_rating: 0, lip_sync_rating: 0, audio_clarity_rating: 0 };
  if (feedbackPanel) feedbackPanel.hidden = true;
}

const ISSUE_CATEGORIES = [
  'wrong_translation',
  'bad_lip_sync',
  'too_fast',
  'too_slow',
  'audio_glitch',
  'caption_issue',
  'other',
];

function renderSegmentIssueList(segs) {
  segmentIssueList.innerHTML = '';

  // Current segment row
  const currentRow = document.createElement('div');
  currentRow.className = 'segment-issue-row';
  currentRow.innerHTML = `
    <span>Current segment:</span>
    <select id="currentIssueCategory">
      <option value="">— category —</option>
      ${ISSUE_CATEGORIES.map(c => `<option value="${c}">${c.replace(/_/g, ' ')}</option>`).join('')}
    </select>
    <input type="text" id="currentIssueComment" placeholder="Optional note">
    <button type="button" class="add-issue-btn" id="reportCurrentSegmentBtn">Report</button>
  `;
  segmentIssueList.appendChild(currentRow);

  document.getElementById('reportCurrentSegmentBtn').addEventListener('click', () => {
    const category = document.getElementById('currentIssueCategory').value;
    if (!category) {
      setFeedbackStatus('Pick a category for the current segment issue.', 'error');
      return;
    }
    const idx = lastSegmentIndex >= 0 ? lastSegmentIndex : 0;
    const seg = segments[idx] || { start: 0, duration: 0, text: '' };
    segmentIssues.push({
      segment_index: idx,
      segment_start: seg.start,
      category,
      comment: document.getElementById('currentIssueComment').value.trim(),
    });
    document.getElementById('currentIssueCategory').value = '';
    document.getElementById('currentIssueComment').value = '';
    setFeedbackStatus('Current-segment issue added. You can add more or submit.', 'success');
  });

  // Compact list of first several segments
  const preview = (segs || []).slice(0, 20);
  preview.forEach((seg, idx) => {
    const row = document.createElement('div');
    row.className = 'segment-issue-row';
    row.innerHTML = `
      <span>#${idx} ${formatTime(seg.start)}:</span>
      <select class="issue-category" data-index="${idx}" data-start="${seg.start}">
        <option value="">— category —</option>
        ${ISSUE_CATEGORIES.map(c => `<option value="${c}">${c.replace(/_/g, ' ')}</option>`).join('')}
      </select>
      <input type="text" class="issue-comment" data-index="${idx}" placeholder="Optional note">
      <button type="button" class="add-issue-btn report-segment-btn" data-index="${idx}" data-start="${seg.start}">Report</button>
    `;
    segmentIssueList.appendChild(row);
  });

  segmentIssueList.querySelectorAll('.report-segment-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.index, 10);
      const start = parseFloat(btn.dataset.start);
      const category = segmentIssueList.querySelector(`.issue-category[data-index="${idx}"]`).value;
      if (!category) {
        setFeedbackStatus(`Pick a category for segment #${idx}.`, 'error');
        return;
      }
      const comment = segmentIssueList.querySelector(`.issue-comment[data-index="${idx}"]`).value.trim();
      segmentIssues.push({ segment_index: idx, segment_start: start, category, comment });
      setFeedbackStatus(`Issue recorded for segment #${idx}.`, 'success');
    });
  });
}

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function setFeedbackStatus(msg, kind) {
  feedbackStatus.textContent = msg;
  feedbackStatus.className = 'feedback-status' + (kind ? ` ${kind}` : '');
}

feedbackSubmit.addEventListener('click', async () => {
  if (!currentVideoId) {
    setFeedbackStatus('No video loaded yet.', 'error');
    return;
  }
  if (feedbackRatings.overall_rating < 1 || feedbackRatings.lip_sync_rating < 1 || feedbackRatings.audio_clarity_rating < 1) {
    setFeedbackStatus('Please rate all three dimensions (1–5 stars).', 'error');
    return;
  }

  const syncIssue = document.querySelector('input[name="syncIssue"]:checked')?.value || 'none';
  const payload = {
    video_id: currentVideoId,
    overall_rating: feedbackRatings.overall_rating,
    lip_sync_rating: feedbackRatings.lip_sync_rating,
    audio_clarity_rating: feedbackRatings.audio_clarity_rating,
    sync_issue: syncIssue,
    segment_issues: segmentIssues,
    general_comment: feedbackComment.value.trim(),
    improvement_suggestion: feedbackSuggestion.value.trim(),
  };

  feedbackSubmit.disabled = true;
  feedbackSubmit.textContent = 'Saving…';
  try {
    const res = await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to save feedback.');
    setFeedbackStatus('Feedback saved. Thank you! ID: ' + data.id, 'success');
    feedbackSubmit.textContent = 'Feedback submitted';
  } catch (err) {
    setFeedbackStatus(err.message, 'error');
    feedbackSubmit.disabled = false;
    feedbackSubmit.textContent = 'Submit feedback';
  }
});


