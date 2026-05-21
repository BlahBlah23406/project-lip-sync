'use strict';

// ── State ────────────────────────────────────────────────────────────────────
let player = null;
let segments = [];
let syncInterval = null;
let lastSegmentIndex = -1;
let pendingVideoId = null;
let syncOffsetSeconds = 0;   // positive = show subtitles earlier (ahead of timestamps)
let currentVideoUrl = '';
let dubPollInterval = null;
let isDubActive = false;

// ── DOM refs ──────────────────────────────────────────────────────────────────
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


// ── Sync offset controls ──────────────────────────────────────────────────────
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

// ── Dub Audio Track Controls ──────────────────────────────────────────────────
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

// ── Form submit ───────────────────────────────────────────────────────────────
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


// ── New video button ──────────────────────────────────────────────────────────
newVideoBtn.addEventListener('click', () => {
  stopSync();
  segments = [];
  lastSegmentIndex = -1;
  currentVideoUrl = '';
  resetDubUI();
  setSubtitle('', true);
  showInputOnly();
  urlInput.value = '';
  urlInput.focus();
});

// ── YouTube IFrame API ────────────────────────────────────────────────────────
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
  setSubtitle('▶ Play the video to see Bangla subtitles', true);
}

function onPlayerStateChange(event) {
  const YT_PLAYING = 1;
  const YT_PAUSED  = 2;
  const YT_ENDED   = 0;

  if (event.data === YT_PLAYING) {
    startSync();
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

// ── Subtitle sync ─────────────────────────────────────────────────────────────
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
    setSubtitle('', true);
  } else {
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

// ── UI helpers ────────────────────────────────────────────────────────────────
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

// ── Dubbing flow ──────────────────────────────────────────────────────────────
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

