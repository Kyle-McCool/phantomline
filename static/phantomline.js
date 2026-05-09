const $ = (id) => document.getElementById(id);
function forceReadableButtons(root = document) {
  const buttons = root.querySelectorAll
    ? root.querySelectorAll('button, a.btn, a.cta, .btn, .tab-btn, .filter-btn, .dur-btn, .publish-pill, .iconbtn')
    : [];
  buttons.forEach(el => {
    // Skip buttons that explicitly opt out of the readable-text override.
    // .launch-action.primary uses dark text on a teal fill (CTA pattern)
    // and the white-text default would tank its contrast.
    if (el.classList.contains('launch-action') && el.classList.contains('primary')) return;
    const isPrimary = el.classList.contains('btn') && !el.classList.contains('secondary');
    if (isPrimary) {
      el.style.setProperty('color', '#f4f8f5', 'important');
      el.style.setProperty('background', 'linear-gradient(135deg, rgba(34, 231, 245,0.24), rgba(34, 231, 245,0.18)), rgba(10,18,18,0.94)', 'important');
      el.style.setProperty('border-color', 'rgba(34, 231, 245,0.42)', 'important');
      el.style.setProperty('text-shadow', '0 1px 1px rgba(0,0,0,0.65)', 'important');
    } else if (el.classList.contains('secondary') || el.tagName === 'BUTTON') {
      el.style.setProperty('color', '#f4f8f5', 'important');
    }
  });
}
window.addEventListener('DOMContentLoaded', () => forceReadableButtons());

// First-run vs returning-user gate. The Make tab's hero block (eyebrow
// + headline + 5-step rail + brand actions) is helpful on the first
// visit and pure visual noise after that.
//
// Default behavior: HIDE the hero. Only show it if we have strong
// evidence this is a genuine first visit. i.e. no Phantomline state
// in localStorage at all. Anyone who has saved a tab, dismissed the
// install banner, saved form state, or had auth in this browser is
// treated as returning.
//
// The previous logic ("show by default; flip after 30s on page or
// engagement") meant existing long-time users got the hero one more
// time on every reload until they engaged. exactly the noise the
// user complained about. This version is one-shot: first paint after
// first install = hero; every subsequent load = no hero.
(function makeHeroFirstRunGate() {
  try {
    const KEY = 'phantomline-make-hero-seen';
    const isFirstVisit = (() => {
      // Explicit flag wins.
      if (localStorage.getItem(KEY) === '1') return false;
      // Any other phantomline-* key is evidence of prior use.
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k) continue;
        // 'phantomline-' for our own keys, 'sb-' / 'supabase' for
        // Supabase auth tokens (means they've signed in before, so
        // they've definitely seen the studio at least once).
        if (k.startsWith('phantomline-') || k.startsWith('sb-') || k.includes('supabase')) {
          return false;
        }
      }
      return true;
    })();
    if (!isFirstVisit) {
      document.body.classList.add('is-returning');
      // Backfill the flag so we don't have to re-scan keys next load.
      try { localStorage.setItem(KEY, '1'); } catch (_) {}
      return;
    }
    // True first visit. show the hero, then mark seen as soon as
    // they take any action (or after 30s) so the next reload hides it.
    const markSeen = () => {
      try { localStorage.setItem(KEY, '1'); } catch (_) {}
    };
    setTimeout(markSeen, 30000);
    document.addEventListener('click', (e) => {
      const t = e.target.closest('.tab-btn[data-tab], #makeLoadDemoBtn, #makeShuffleIdeaBtn, #makeVideoBtn');
      if (t) markSeen();
    }, { once: false, passive: true });
  } catch (_) {
    // localStorage blocked. be safe and hide the hero.
    document.body.classList.add('is-returning');
  }
})();
let currentJob = null;
let pollTimer = null;

function fmt(t) {
  const d = new Date(t * 1000);
  return d.toTimeString().slice(0, 8);
}

function toast(msg, bad=false) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.toggle('bad', bad);
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 2400);
}

const NOTIFICATION_KEY = 'ghostline.notifications.v1';
let notifications = [];
let knownPublishStatuses = {};
let publishStatusWatchReady = false;

function loadNotifications() {
  try { notifications = JSON.parse(localStorage.getItem(NOTIFICATION_KEY) || '[]'); }
  catch { notifications = []; }
  if (!Array.isArray(notifications)) notifications = [];
}

function saveNotifications() {
  localStorage.setItem(NOTIFICATION_KEY, JSON.stringify(notifications.slice(0, 40)));
}

function relativeNotificationTime(ts) {
  const seconds = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
  if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
  return Math.floor(seconds / 86400) + 'd ago';
}

function renderNotifications() {
  const list = $('notificationList');
  const count = $('notificationCount');
  if (!list || !count) return;
  const unread = notifications.filter(n => !n.read).length;
  count.textContent = unread > 9 ? '9+' : String(unread);
  count.classList.toggle('shown', unread > 0);
  if (!notifications.length) {
    list.innerHTML = '<div class="notification-empty">No notifications yet. Finished videos and live posts will show up here.</div>';
    return;
  }
  list.innerHTML = notifications.slice(0, 30).map(n => `
    <div class="notification-item ${n.read ? '' : 'unread'}">
      <strong>${escapeHtml(n.title || 'Phantomline update')}</strong>
      <p>${escapeHtml(n.body || '')}</p>
      ${n.url ? `<small><a href="${escapeHtml(n.url)}" target="_blank">Open link</a></small>` : `<small>${relativeNotificationTime(n.ts || Date.now())}</small>`}
    </div>
  `).join('');
}

function showDesktopNotification(title, body, url) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    const note = new Notification(title, {
      body,
      icon: '/static/phantomline-logo.svg',
      tag: title + body,
    });
    if (url) note.onclick = () => window.open(url, '_blank');
  } catch { /* browser notification can fail silently */ }
}

function addNotification({id, type='info', title, body, url, desktop=true}) {
  const key = id || `${type}:${title}:${body}`;
  if (notifications.some(n => n.id === key)) return;
  const item = { id: key, type, title, body, url, read: false, ts: Date.now() };
  notifications.unshift(item);
  notifications = notifications.slice(0, 40);
  saveNotifications();
  renderNotifications();
  toast(title || 'Phantomline update', type === 'error');
  if (desktop) showDesktopNotification(title || 'Phantomline update', body || '', url);
}

async function requestDesktopNotifications() {
  if (!('Notification' in window)) {
    toast('Desktop notifications are not supported in this browser', true);
    return;
  }
  const result = await Notification.requestPermission();
  toast(result === 'granted' ? 'Desktop notifications enabled' : 'Desktop notifications not enabled', result !== 'granted');
}

function setupNotifications() {
  loadNotifications();
  renderNotifications();
  $('notificationBell')?.addEventListener('click', () => {
    const panel = $('notificationPanel');
    const bell = $('notificationBell');
    const opened = panel?.classList.toggle('open');
    bell?.setAttribute('aria-expanded', opened ? 'true' : 'false');
    notifications = notifications.map(n => ({ ...n, read: true }));
    saveNotifications();
    renderNotifications();
  });
  $('markNotificationsReadBtn')?.addEventListener('click', () => {
    notifications = notifications.map(n => ({ ...n, read: true }));
    saveNotifications();
    renderNotifications();
  });
  $('enableDesktopNotificationsBtn')?.addEventListener('click', requestDesktopNotifications);
  document.addEventListener('click', (event) => {
    const wrap = document.querySelector('.notification-wrap');
    if (wrap && !wrap.contains(event.target)) {
      $('notificationPanel')?.classList.remove('open');
      $('notificationBell')?.setAttribute('aria-expanded', 'false');
    }
  });
}

const VISUAL_PRESETS = {
  'cinematic-photoreal': {
    style: 'cinematic photoreal, high detail, natural materials, film still, realistic lighting, shallow depth of field',
    ambience: 'moody atmospheric light, soft contrast, believable color grading',
  },
  'animated-film': {
    style: 'high-quality animated film still, expressive shapes, polished 3D animation look, appealing character design',
    ambience: 'warm cinematic lighting, rich color, gentle depth, family-friendly adventure mood',
  },
  'storybook': {
    style: 'storybook illustration, hand-painted texture, soft brushwork, charming composition, detailed environment art',
    ambience: 'cozy magical atmosphere, gentle glow, soft shadows, bedtime-story calm',
  },
  'cartoon': {
    style: 'clean cartoon illustration, bold readable shapes, simple appealing faces, crisp outlines, bright but tasteful colors',
    ambience: 'friendly upbeat mood, clear lighting, uncluttered background',
  },
  'survival-channel': {
    style: 'clean educational cartoon adventure style, YouTube survival tips visual, clear step-by-step action, readable silhouettes',
    ambience: 'outdoor wilderness ambience, practical daylight, grounded natural colors, calm instructional mood',
    character: 'a small friendly backpacker host demonstrating each survival tip, orange jacket, beanie, backpack, same character in every scene',
  },
};

const CHANNEL_RECIPES = {
  'viral-story': {
    niche: 'viral faceless story shorts',
    audience: 'short-form viewers who stop for secrets, betrayal, impossible situations, and fast twists',
    format: 'viral-short',
    tone: 'fast, tense, conversational, addictive, unresolved',
    direction: 'VIRAL SHORTS PLAYBOOK: Write addictive unresolved tension delivered in under 40 seconds. Use only Confession / Secret, Betrayal / Drama, Rule-Based Horror, or Impossible Situation. Hook must introduce a person, a problem, and a broken expectation. Structure: 0-1.5s shock hook, 1.5-10s setup, 10-20s escalation, 20-35s twist, 35-45s loop ending. Ending must connect back to the beginning, leave missing information, or cut slightly early. Captions should be 2-5 words per beat with one highlighted word. Title should be short, emotional, incomplete, and never "Part 1".',
    visualPreset: 'cartoon',
    ambience: 'high-retention short-form tension, clean readable frames, bold contrast, fast emotional clarity',
    music: 'low suspense pulse, soft ticking texture, light tension bed, no vocals, stays under narration',
    duration: '95',
    aspect: '9:16',
    captionStyle: 'tiktok',
    videoMode: 'source',
    hookStyle: 'conflict',
  },
  custom: {
    niche: 'faceless YouTube channel',
    audience: 'curious general viewers',
    format: 'explainer',
    tone: 'clear, engaging, conversational, YouTube-ready',
    direction: 'Open with a strong hook, keep each section visual, avoid filler, and write for a single narrator.',
    visualPreset: 'cinematic-photoreal',
    ambience: 'clean cinematic ambience, strong subject focus, consistent color mood',
    music: 'subtle cinematic background bed, steady pulse, soft reverb, no vocals',
  },
  survival: {
    niche: 'survival tips and outdoor safety',
    audience: 'beginner campers, hikers, preppers, and outdoor-curious viewers',
    format: 'tutorial',
    tone: 'practical, calm under pressure, clear, slightly urgent, beginner-friendly',
    direction: 'Write actionable survival tips as visual steps. Each scene should show a specific action the viewer can imagine: stop, signal, shelter, water, fire, navigation, warmth, or rescue. Avoid vague motivation.',
    visualPreset: 'survival-channel',
    ambience: 'outdoor wilderness ambience, practical daylight, grounded natural colors, calm instructional mood',
    character: 'a small friendly backpacker host demonstrating each survival tip, orange jacket, beanie, backpack, same character in every scene',
    music: 'light tense outdoor ambience, soft hand percussion, low warm drone, no vocals, instructional but not dramatic',
  },
  mystery: {
    niche: 'mystery documentary',
    audience: 'viewers who like unexplained events, lost places, strange evidence, and slow reveals',
    format: 'documentary',
    tone: 'serious, investigative, atmospheric, suspenseful, grounded',
    direction: 'Structure the script like a mini documentary: cold open, known facts, strange details, competing theories, and a final unresolved image. Make every scene visually concrete.',
    visualPreset: 'cinematic-photoreal',
    ambience: 'cold investigative mood, dim practical light, fog, archival tension, restrained color',
    music: 'dark documentary pulse, low strings, sparse piano, slow tension, no vocals',
  },
  history: {
    niche: 'history explainer',
    audience: 'curious viewers who want dramatic but accurate history',
    format: 'documentary',
    tone: 'cinematic, clear, factual, dramatic without exaggeration',
    direction: 'Explain the topic through vivid historical scenes, cause and effect, stakes, and human consequences. Keep the narration easy to follow.',
    visualPreset: 'storybook',
    ambience: 'aged parchment warmth, museum light, historical atmosphere, grounded realism',
    music: 'cinematic historical bed, low strings, soft percussion, warm room tone, no vocals',
  },
  'kids-edu': {
    niche: 'kids educational channel',
    audience: 'children and parents watching together',
    format: 'explainer',
    tone: 'friendly, simple, curious, safe, upbeat',
    direction: 'Use simple language, short sentences, vivid examples, and a friendly guide. Make each scene easy to picture and age-appropriate.',
    visualPreset: 'cartoon',
    ambience: 'bright classroom adventure mood, warm daylight, playful but uncluttered',
    character: 'a cheerful kid-friendly guide character pointing, discovering, and demonstrating ideas, same design every scene',
    music: 'gentle playful educational music, light marimba, soft piano, no vocals, not hyper',
  },
  tech: {
    niche: 'technology explainer',
    audience: 'busy viewers who want complex tech explained simply',
    format: 'explainer',
    tone: 'smart, clear, modern, concise, practical',
    direction: 'Turn abstract tech into concrete visual metaphors. Use examples, before-and-after comparisons, and simple analogies.',
    visualPreset: 'cinematic-photoreal',
    ambience: 'clean modern workspace, soft screens, crisp contrast, subtle futuristic mood',
    music: 'minimal tech ambience, soft synth pulse, clean low bass, no vocals',
  },
  finance: {
    niche: 'personal finance explainer',
    audience: 'everyday viewers trying to make smarter money decisions',
    format: 'explainer',
    tone: 'trustworthy, calm, direct, practical, non-hype',
    direction: 'Use concrete examples and clear consequences. Avoid financial advice guarantees. Keep visuals symbolic and easy to understand.',
    visualPreset: 'cinematic-photoreal',
    ambience: 'clean desk, warm morning light, organized financial documents, confident calm',
    music: 'quiet confident corporate bed, warm piano, soft pulse, no vocals',
  },
  'scary-story': {
    niche: 'scary story narration',
    audience: 'viewers who like eerie stories, mystery, and late-night narration',
    format: 'story',
    tone: 'slow-burn, eerie, cinematic, calm narration, suspenseful',
    direction: 'Write as a single-narrator scary story with a strong hook, escalating unease, concrete locations, and a memorable final image. Avoid gore.',
    visualPreset: 'cinematic-photoreal',
    ambience: 'night fog, cold moonlight, empty roads, soft film grain, quiet dread',
    music: 'dark ambient bed, low strings, distant piano, no drums, no vocals, sleep-friendly',
  },
  motivational: {
    niche: 'motivational shorts',
    audience: 'viewers who want confidence, discipline, and momentum',
    format: 'short-hook',
    tone: 'direct, uplifting, cinematic, punchy, sincere',
    direction: 'Open with a sharp emotional hook, build momentum, use vivid action imagery, and end with a line that feels quotable without sounding fake.',
    visualPreset: 'animated-film',
    ambience: 'sunrise energy, forward motion, warm highlights, clean contrast',
    music: 'uplifting cinematic bed, soft drums, warm piano, gradual rise, no vocals',
  },
  'reddit-story': {
    niche: 'Reddit-style story narration',
    audience: 'short-form viewers who like personal conflict and satisfying twists',
    format: 'story',
    tone: 'conversational, clear, dramatic, fast hook, easy to follow',
    direction: 'Write in first-person story style with a quick hook, clear stakes, escalating conflict, and a satisfying twist or lesson. Keep it safe for broad platforms.',
    visualPreset: 'cartoon',
    ambience: 'modern everyday settings, clean readable scenes, expressive but not chaotic',
    music: 'light suspense bed, soft plucks, gentle pulse, no vocals',
  },
};

function applyChannelRecipe(force=false) {
  const recipe = CHANNEL_RECIPES[$('makeRecipe')?.value || 'custom'];
  if (!recipe) return;
  if (force || !$('makeNiche').value.trim()) $('makeNiche').value = recipe.niche || '';
  if (force || !$('makeAudience').value.trim()) $('makeAudience').value = recipe.audience || '';
  if (recipe.format) $('makeFormat').value = recipe.format;
  if (force || !$('makeTone').value.trim()) $('makeTone').value = recipe.tone || '';
  if (force || !$('makeDesc').value.trim()) $('makeDesc').value = recipe.direction || '';
  if (recipe.visualPreset) {
    $('makeVisualPreset').value = recipe.visualPreset;
    applyVisualPreset('make');
  }
  if (force || !$('makeAmbience').value.trim()) $('makeAmbience').value = recipe.ambience || '';
  if (recipe.character && (force || !$('makeVisualCharacter').value.trim())) {
    $('makeVisualCharacter').value = recipe.character;
  }
  if (force || !$('makeMusicPrompt').value.trim()) $('makeMusicPrompt').value = recipe.music || '';
  if (recipe.duration && (force || $('makeRecipe').value === 'viral-story')) $('makeDuration').value = recipe.duration;
  if (recipe.aspect && (force || $('makeRecipe').value === 'viral-story')) $('makeAspect').value = recipe.aspect;
  if (recipe.captionStyle && $('makeCaptionStyle')) $('makeCaptionStyle').value = recipe.captionStyle;
  if (recipe.hookStyle && $('makeHookStyle')) $('makeHookStyle').value = recipe.hookStyle;
  if (recipe.videoMode && force) {
    $('makeVideoMode').value = recipe.videoMode;
    updateMakeVideoMode();
  }
  updateViralStage();
  updateMakeDurationHint();
}

function isViralStoryMode() {
  return $('makeRecipe')?.value === 'viral-story' || $('makeFormat')?.value === 'viral-short';
}

function updateViralStage() {
  const stage = $('makeViralStage');
  if (stage) stage.style.display = isViralStoryMode() ? '' : 'none';
  document.querySelectorAll('#makeTensionCards .choice-card').forEach(card => {
    card.classList.toggle('active', card.dataset.tension === $('makeTensionFormat').value);
  });
}

function applyVisualPreset(prefix) {
  const presetEl = $(`${prefix}VisualPreset`);
  const styleEl = prefix === 'make' ? $('makeVisualStyle') : $('videoStyle');
  const ambienceEl = prefix === 'make' ? $('makeAmbience') : $('videoAmbience');
  const characterEl = prefix === 'make' ? $('makeVisualCharacter') : $('videoVisualCharacter');
  if (!presetEl || presetEl.value === 'custom') return;
  const preset = VISUAL_PRESETS[presetEl.value];
  if (!preset) return;
  if (styleEl) styleEl.value = preset.style;
  if (ambienceEl) ambienceEl.value = preset.ambience || '';
  if (characterEl && preset.character && !characterEl.value.trim()) {
    characterEl.value = preset.character;
  }
}

function makeCreativeBrief() {
  const hookHints = {
    mistake: 'Open with a mistake-warning hook that makes viewers feel they need to keep watching.',
    question: 'Open with a sharp question that creates immediate curiosity.',
    secret: 'Open with a "most people do not know this" reveal hook.',
    'cold-open': 'Open on a vivid visual cold open before explaining what is happening.',
    countdown: 'Open with a list/countdown promise and clear payoff.',
    conflict: 'Open with a human conflict, danger, or high-stakes dilemma.',
  };
  const tensionLabels = {
    betrayal_drama: 'Betrayal / Drama',
    confession_secret: 'Confession / Secret',
    rule_horror: 'Rule-Based Horror',
    impossible_situation: 'Impossible Situation',
  };
  const loopLabels = {
    circular: 'Circular callback ending that echoes the opening hook.',
    'missing-info': 'Missing-information ending that makes viewers replay for clues.',
    'hard-cut': 'Fast hard cut ending that stops slightly before the next action.',
    question: 'Question ending that pushes viewers to comment.',
  };
  const viralBlock = isViralStoryMode()
    ? [
        'VIRAL SHORTS PLAYBOOK:',
        'Goal: addictive unresolved tension delivered in under 40 seconds.',
        `Tension format: ${tensionLabels[$('makeTensionFormat').value] || 'Betrayal / Drama'}.`,
        `Loop ending: ${loopLabels[$('makeLoopType').value] || loopLabels.circular}`,
        'Hook requirements: introduce a person, introduce a problem, and break expectation in the first sentence.',
        'Exact structure: 0-1.5s shock hook, 1.5-10s setup, 10-20s escalation, 20-35s twist, 35-45s loop ending.',
        'Captions must work as attention anchors: short spoken lines, 2-5 words per caption beat, one key word worth highlighting.',
        'Do not write a normal story. Avoid slow worldbuilding, moral explanations, "Part 1", and soft endings.',
        $('makePinnedComment')?.value.trim() ? `Pinned comment to support: ${$('makePinnedComment').value.trim()}.` : 'Include a debate-style pinned comment idea after generation if packaging is shown.',
      ].join('\n')
    : '';
  return [
    viralBlock,
    $('makePreferredTitle')?.value.trim()
      ? `Preferred YouTube title/package: ${$('makePreferredTitle').value.trim()}. Use this title direction unless a clearly stronger title is needed.`
      : '',
    `Channel niche: ${$('makeNiche').value.trim() || 'faceless YouTube channel'}.`,
    `Target viewer: ${$('makeAudience').value.trim() || 'general viewers'}.`,
    `Video format: ${$('makeFormat').value}.`,
    `Hook strategy: ${hookHints[$('makeHookStyle').value] || hookHints.mistake}`,
    `Script direction: ${$('makeDesc').value.trim()}.`,
    `Visual style for the generated scenes: ${$('makeVisualStyle').value.trim()}.`,
    `Ambience: ${$('makeAmbience').value.trim()}.`,
    $('makeVisualCharacter').value.trim()
      ? `Recurring visual host/character: ${$('makeVisualCharacter').value.trim()}. Write scenes that give this character concrete things to do.`
      : 'No recurring host is required; write visually concrete scenes that can stand alone.',
    'Write narration that naturally creates clear visual moments for each scene. Avoid abstract filler.',
  ].filter(Boolean).join('\n');
}

function visualSettings(prefix) {
  return {
    visual_style: (prefix === 'make' ? $('makeVisualStyle') : $('videoStyle'))?.value.trim() || '',
    visual_ambience: (prefix === 'make' ? $('makeAmbience') : $('videoAmbience'))?.value.trim() || '',
    visual_character: (prefix === 'make' ? $('makeVisualCharacter') : $('videoVisualCharacter'))?.value.trim() || '',
  };
}

const BRAND_KIT_KEY = 'ghostline.makeVideo.brandKit.v1';
// One-time migration from the legacy "bindery." key. Safe to remove after a release.
(() => {
  try {
    const legacy = localStorage.getItem('bindery.makeVideo.brandKit.v1');
    if (legacy && !localStorage.getItem(BRAND_KIT_KEY)) {
      localStorage.setItem(BRAND_KIT_KEY, legacy);
    }
    if (legacy) localStorage.removeItem('bindery.makeVideo.brandKit.v1');
  } catch {}
})();
const BRAND_FIELDS = [
  'makeRecipe', 'makeNiche', 'makeAudience', 'makeFormat', 'makeHookStyle',
  'makeTensionFormat', 'makeLoopType',
  'makeTone', 'makeVoice', 'makeAspect', 'makeVisualPreset', 'makeAmbience',
  'makeVisualStyle', 'makeVisualCharacter', 'makeMusicPrompt', 'makeCaptionStyle',
  'makePatternInterrupts', 'makeSourceEnhance', 'makeTitleStyle', 'makeKeywordMode',
  'makePreferredTitle', 'makePinnedComment', 'makeHashtags',
];

function saveBrandKit() {
  const kit = {};
  for (const id of BRAND_FIELDS) {
    const el = $(id);
    if (el) kit[id] = el.value;
  }
  localStorage.setItem(BRAND_KIT_KEY, JSON.stringify(kit));
  toast('Brand kit saved');
}

function loadBrandKit() {
  let kit = null;
  try { kit = JSON.parse(localStorage.getItem(BRAND_KIT_KEY) || 'null'); }
  catch { kit = null; }
  if (!kit) {
    toast('No saved brand kit yet', true);
    return;
  }
  for (const [id, value] of Object.entries(kit)) {
    const el = $(id);
    if (el) el.value = value;
  }
  if ($('makePreferredTitle').value.trim()) $('makeTitlePackage').style.display = 'block';
  updateMakeVideoMode();
  updateMakeChoiceCards();
  updateMakeReadiness();
  toast('Brand kit loaded');
}

function fillModelSelect(sel, models) {
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '';
  let selected = false;
  for (const m of models) {
    const opt = document.createElement('option');
    opt.value = m; opt.textContent = m;
    if (m === current || (!current.includes(':') && m === current + ':latest')) {
      opt.selected = true;
      selected = true;
    }
    sel.appendChild(opt);
  }
  if (!selected && models.length) {
    sel.value = models[0];
  }
  if (current && !selected && !models.length) {
    const opt = document.createElement('option');
    opt.value = current; opt.textContent = current + ' (not installed)';
    opt.selected = true;
    sel.insertBefore(opt, sel.firstChild);
  }
}

// Read the Supabase access token out of localStorage (same key the
// auth-gate inspects). Returns null when the user isn't signed in or
// when localStorage is unavailable. Used to attach an Authorization
// header to every /api/* call so the hosted server can scope projects
// to the signed-in user. Local desktop installs don't need this. the
// server ignores the header when the Supabase flag is off.
function _phantomlineAuthToken() {
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k || k.indexOf('sb-') !== 0 || k.indexOf('-auth-token') < 0) continue;
      const raw = localStorage.getItem(k);
      if (!raw) continue;
      const parsed = JSON.parse(raw);
      const sess = (parsed && parsed.currentSession) || parsed;
      if (sess && sess.access_token) return sess.access_token;
    }
  } catch (e) { /* localStorage disabled. anonymous fetch */ }
  return null;
}

// Attach Authorization header on cross-origin-safe URLs (same-origin
// /api/* calls). Mutates the supplied options.headers in place but
// preserves any caller-supplied headers.
function _withAuthHeader(options) {
  options = options || {};
  const token = _phantomlineAuthToken();
  if (!token) return options;
  const headers = new Headers(options.headers || {});
  if (!headers.has('Authorization')) {
    headers.set('Authorization', 'Bearer ' + token);
  }
  options.headers = headers;
  return options;
}

async function apiJson(url, options) {
  const r = await fetch(url, _withAuthHeader(options));
  const text = await r.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (e) {
    const clean = text.replace(/\s+/g, ' ').slice(0, 120);
    throw new Error(`${url} returned non-JSON (${r.status}): ${clean || 'empty response'}`);
  }
  if (!r.ok || data.ok === false) {
    throw new Error(data.error || `${url} failed (${r.status})`);
  }
  return data;
}

// Wrap window.fetch so direct fetch() calls (not via apiJson) on /api/*
// also get the bearer header. Avoids hunting through 200+ raw fetch
// call sites to add auth manually.
(function () {
  const origFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    let url;
    try {
      url = (typeof input === 'string') ? input : (input && input.url) || '';
    } catch (e) { url = ''; }
    // Only auth-attach on same-origin /api/* paths so we never leak the
    // token to third-party APIs.
    if (typeof url === 'string' && url.indexOf('/api/') === 0) {
      init = _withAuthHeader(init);
    } else if (typeof url === 'string' && url.indexOf(location.origin + '/api/') === 0) {
      init = _withAuthHeader(init);
    }
    return origFetch(input, init);
  };
})();

async function refreshOllama() {
  const badge = $('ollamaBadge');
  const text = $('ollamaText');
  // On the hosted PWA there is no local Ollama. inference runs in-browser via
  // WebLLM. Showing "ollama offline" with a red dot makes the app look broken
  // to first-time visitors. Detect hosted mode via hostname and surface a
  // neutral "browser mode" badge instead. set this BEFORE the fetch so
  // the word "ollama" never flashes on screen during the first paint.
  const host = (window.location && window.location.hostname) || '';
  const isHosted = /\.onrender\.com$/i.test(host)
    || /(^|\.)phantomline\.(xyz|com|app)$/i.test(host)
    || (host && host !== 'localhost' && host !== '127.0.0.1');
  if (isHosted) {
    badge.classList.remove('bad'); badge.classList.remove('ok');
    text.textContent = 'browser mode';
    return; // skip /api/models fetch entirely on hosted
  }
  try {
    const r = await fetch('/api/models');
    const d = await r.json();
    if (d.ok && d.models && d.models.length) {
      badge.classList.add('ok'); badge.classList.remove('bad');
      text.textContent = `ollama · ${d.models.length} model${d.models.length === 1 ? '' : 's'}`;
      fillModelSelect($('model'), d.models);
      fillModelSelect($('shortModel'), d.models);
      fillModelSelect($('makeModel'), d.models);
      fillModelSelect($('settingsModel'), d.models);
    } else if (d.ok) {
      badge.classList.add('bad'); badge.classList.remove('ok');
      text.textContent = 'ollama · no models installed';
    } else {
      badge.classList.add('bad'); badge.classList.remove('ok');
      text.textContent = 'ollama offline';
    }
  } catch (e) {
    badge.classList.add('bad'); badge.classList.remove('ok');
    text.textContent = 'ollama offline';
  }
}

/* Mode toggle dropdown. clickable badge that lets the user switch
 * between hosted browser mode and their local Phantomline install.
 *
 * Why we DON'T probe localhost from hosted: Chrome / Brave block
 * HTTPS->HTTP localhost fetches even with PNA headers in many configs,
 * so the probe falsely reports "NOT RUNNING" for users who actually
 * have it installed. The probe is the wrong UX pattern.
 *
 * New approach: trust the user. Always show "Switch to my local
 * Phantomline" as a clickable action that opens localhost:5000/app in
 * a new tab. If their local server is running it loads; if not, the
 * browser's clear "site can't be reached" message tells them what to
 * fix. We also remember whether the user has ever successfully been on
 * localhost so the copy can adapt over time. */
(function () {
  const badge = $('ollamaBadge');
  const menu = $('modeToggleMenu');
  const localRow = $('modeToggleLocal');
  const localStatus = $('modeToggleLocalStatus');
  if (!badge || !menu) return;

  const host = (location && location.hostname) || '';
  const isLocalContext = host === 'localhost' || host === '127.0.0.1' || host.endsWith('.local');
  const browserRow = menu.querySelector('[data-mode-row="browser"]');

  // Persist "user has been on local before" once we ever load via
  // localhost. Hosted page reads this to render confident copy
  // ("Switch to your local install") instead of generic prompting.
  const HAS_LOCAL_KEY = 'phantomline.has_local_install';
  if (isLocalContext) {
    try { localStorage.setItem(HAS_LOCAL_KEY, '1'); } catch (e) { /* private mode */ }
  }
  function hasLocalInstall() {
    try { return localStorage.getItem(HAS_LOCAL_KEY) === '1'; }
    catch (e) { return false; }
  }

  function applyContext() {
    if (isLocalContext) {
      // We ARE in local mode. Local row is the current state. Browser
      // row becomes a link to phantomline.xyz/app for cross-device
      // library viewing.
      if (localRow) {
        localRow.removeAttribute('href');
        localRow.style.cursor = 'default';
      }
      if (localStatus) {
        localStatus.textContent = 'CURRENT';
        localStatus.classList.add('detected');
        localStatus.classList.remove('missing');
      }
      if (browserRow) {
        const a = document.createElement('a');
        a.href = 'https://phantomline.xyz/app';
        a.target = '_blank';
        a.rel = 'noopener';
        a.className = browserRow.className;
        a.innerHTML = browserRow.innerHTML.replace(
          /<strong>Browser mode<\/strong>/,
          '<strong>Browser mode <span class="mode-toggle-status">SWITCH</span></strong>'
        );
        browserRow.replaceWith(a);
      }
    } else {
      // Hosted. Browser row is current. Local row is "switch to local"
      // action (no probe. we trust the user). Status pill adapts based
      // on whether the user has previously used local Phantomline (we
      // remember via localStorage).
      if (browserRow) {
        browserRow.querySelector('strong').innerHTML =
          'Browser mode <span class="mode-toggle-status detected">CURRENT</span>';
      }
      if (localStatus) {
        if (hasLocalInstall()) {
          localStatus.textContent = 'OPEN →';
          localStatus.classList.add('detected');
          localStatus.classList.remove('missing');
        } else {
          localStatus.textContent = 'OPEN ↗';
          localStatus.classList.remove('missing');
          localStatus.classList.remove('detected');
        }
      }
      // Show the "make sure Phantomline desktop is running" hint to
      // first-time clickers so they understand the failure mode if their
      // server isn't up. Returning users (we know they have it set up)
      // get the cleaner row.
      const hintEl = $('modeToggleHint');
      if (hintEl) hintEl.hidden = hasLocalInstall();
      if (localRow) {
        // Open in a new tab. keeps the hosted library visible while
        // the user works in their local install.
        localRow.target = '_blank';
        localRow.rel = 'noopener';
      }
    }
  }
  applyContext();

  badge.addEventListener('click', (ev) => {
    ev.stopPropagation();
    menu.hidden = !menu.hidden;
    badge.setAttribute('aria-expanded', String(!menu.hidden));
  });

  document.addEventListener('click', (ev) => {
    if (menu.hidden) return;
    if (!badge.parentNode.contains(ev.target)) {
      menu.hidden = true;
      badge.setAttribute('aria-expanded', 'false');
    }
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !menu.hidden) {
      menu.hidden = true;
      badge.setAttribute('aria-expanded', 'false');
    }
  });

  // On hosted, clicking the "Local Phantomline" row tries to open
  // localhost:5000/app. If we know the user has it installed (visited
  // before), the link just works. If we don't know, we still let them
  // click. failure mode is "browser shows site-not-reachable", which
  // is itself a clear signal to start their local server.
  // The "Don't have it yet?" row goes to /download as a fallback path.
})();

document.querySelectorAll('.tag[data-g]').forEach(el => {
  el.addEventListener('click', () => {
    const targetId = el.dataset.target || 'genre';
    const target = $(targetId);
    if (target) target.value = el.dataset.g;
  });
});

function setGenMode(mode) {
  // 'idle' = dashboard visible; 'working' = status panel visible.
  if (!$('genIdle') || !$('genWorking')) return;
  $('genIdle').style.display = mode === 'idle' ? '' : 'none';
  $('genWorking').style.display = mode === 'working' ? '' : 'none';
}

// Hero-choice cards on the idle dashboard route to other tabs.
document.querySelectorAll('.hero-choice').forEach(btn => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.go;
    if (target === 'generate') return; // already here
    const tabBtn = document.querySelector(`.tab-btn[data-tab="${target}"]`);
    if (tabBtn) tabBtn.click();
  });
});

async function loadRecentWork() {
  const row = $('recentRow');
  if (!row) return;
  try {
    const r = await fetch('/api/projects');
    const d = await r.json();
    const items = (d.projects || []).slice(0, 4);
    if (!items.length) return; // keep the empty-state default
    row.innerHTML = items.map(p => {
      const kindLabel = (KIND_LABELS && KIND_LABELS[p.kind]) || p.kind;
      const sub = p.word_count ? `${p.word_count.toLocaleString()} words` : kindLabel;
      return `
        <div class="recent-card" data-id="${p.id}">
          <div class="rc-meta">
            <div class="rc-title">${escapeHtml(p.title || 'Untitled')}</div>
            <div class="rc-sub">${sub} · ${relTime(p.created_at)}</div>
          </div>
          <span class="rc-kind">${kindLabel}</span>
        </div>`;
    }).join('');
    row.querySelectorAll('.recent-card').forEach(c => {
      c.addEventListener('click', () => {
        document.querySelector('.tab-btn[data-tab="library"]').click();
      });
    });
  } catch (e) { /* keep default empty state */ }
}

$('startBtn').addEventListener('click', async () => {
  const body = {
    topic: $('topic').value.trim(),
    genre: $('genre').value.trim(),
    tone: $('tone').value.trim(),
    word_count: parseInt($('words').value, 10) || 10000,
    model: $('model').value.trim(),
  };
  $('startBtn').disabled = true;
  $('scriptOutput').classList.remove('shown');
  $('log').innerHTML = '';
  setGenMode('working');
  $('titleDisplay').textContent = 'Starting…';
  $('titleDisplay').classList.remove('empty');
  $('statusStat').textContent = 'starting';
  $('sectionStat').textContent = '-';
  $('wordsStat').textContent = '0';
  $('progressBar').style.width = '0%';

  try {
    const r = await fetch('/api/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'failed to start');
    currentJob = d.job_id;
    startPolling();
  } catch (e) {
    toast(e.message || 'Failed to start', true);
    $('startBtn').disabled = false;
    $('titleDisplay').textContent = 'No job running. Configure on the left and press Generate.';
    $('titleDisplay').classList.add('empty');
    $('statusStat').textContent = 'idle';
  }
});

function renderLog(lines) {
  const log = $('log');
  // Render only the latest ~120 lines to keep DOM light.
  const tail = lines.slice(-120);
  log.innerHTML = tail.map(l => {
    const cls = /ERROR/.test(l.msg) ? 'err' : (/Finished/.test(l.msg) ? 'ok' : '');
    return `<span class="line ${cls}"><span class="t">${fmt(l.t)}</span>${escapeHtml(l.msg)}</span>`;
  }).join('');
  log.scrollTop = log.scrollHeight;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollOnce, 1500);
  pollOnce();
}

async function pollOnce() {
  if (!currentJob) return;
  try {
    const r = await fetch('/api/status/' + currentJob);
    const d = await r.json();
    if (!d.ok) return;
    const job = d.job;

    if (job.title) {
      $('titleDisplay').textContent = job.title;
      $('titleDisplay').classList.remove('empty');
    }
    $('sectionStat').textContent = job.section || '-';
    $('wordsStat').textContent = (job.words || 0).toLocaleString();
    $('statusStat').textContent = job.status || '-';

    const target = job.target || parseInt($('words').value, 10) || 10000;
    const pct = Math.min(100, Math.round(((job.words || 0) / target) * 100));
    $('progressBar').style.width = pct + '%';

    renderLog(job.log || []);

    if (job.error) {
      toast('Error: ' + job.error, true);
    }

    if (job.done) {
      clearInterval(pollTimer); pollTimer = null;
      $('startBtn').disabled = false;
      if (job.error) {
        $('statusStat').textContent = 'error';
      } else {
        $('statusStat').textContent = 'done';
        await loadScript();
      }
    }
  } catch (e) {
    /* network blip - keep polling */
  }
}

async function loadScript() {
  if (!currentJob) return;
  const r = await fetch('/api/script/' + currentJob);
  const d = await r.json();
  if (!d.ok) return;
  $('scriptText').value = d.text;
  $('savedPathHint').textContent = 'saved to ' + d.path;
  $('scriptOutput').classList.add('shown');
  $('scriptOutput').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

$('copyBtn').addEventListener('click', async () => {
  const text = $('scriptText').value;
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied script to clipboard');
  } catch {
    $('scriptText').select(); document.execCommand('copy');
    toast('Copied script to clipboard');
  }
});

$('downloadBtn').addEventListener('click', () => {
  if (!currentJob) return;
  window.location = '/api/download/' + currentJob;
});

$('scriptVideoBtn').addEventListener('click', () => {
  sendToVideoStudio($('scriptText').value, $('titleDisplay').textContent || '');
});

// ---------- Kokoro TTS ----------
let ttsJob = null;
let ttsTimer = null;

async function loadVoices() {
  try {
    const r = await fetch('/api/voices');
    const d = await r.json();
    for (const id of ['voice', 'pasteVoice', 'makeVoice', 'settingsVoice']) {
      const sel = $(id);
      if (!sel) continue;
      sel.innerHTML = '';
      for (const v of d.voices || []) {
        const opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = v.label;
        sel.appendChild(opt);
      }
      // Default to a calm female voice that's good for bedtime.
      sel.value = 'af_nicole';
    }
  } catch (e) { /* leave dropdowns empty */ }
}

// Tab switching
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.tab;
    if (!target) return;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.tab-content').forEach(c => {
      c.classList.toggle('active', c.id === 'tab-' + target);
    });
    if (target === 'publish' && btn.dataset.pubJump && typeof switchPublishView === 'function') {
      switchPublishView(btn.dataset.pubJump);
    }
    // Remember the user's last tab so refresh doesn't always slam them
    // back to Launch Setup. Only persist meaningful tabs (skip ephemeral
    // sub-tools like Generate/Short/Paste since those are entered from
    // Make Video, not picked as a home).
    try {
      if (['launch','make','publish','library','settings'].includes(target)) {
        localStorage.setItem('phantomline-last-tab', target);
      }
    } catch (_) {}
  });
});

// Smart default tab. The HTML default is Create Video. that's where
// users want to be after first-time login. Two overrides:
//   1. If readiness has required blockers, route to Launch Setup so
//      the user fixes the install issue before trying to render.
//      (The install banner also appears at the top of every tab; this
//      additionally puts the checklist front-and-center on first visit
//      when nothing is set up yet.)
//   2. If the user explicitly chose a different tab last session,
//      honor it (publish, library, settings) provided readiness is OK.
// Returning users get continuity; new users get the creator surface.
(function smartDefaultTab() {
  try {
    const saved = localStorage.getItem('phantomline-last-tab');
    fetch('/api/launch/readiness', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        const blockers = (d?.blockers || []).filter(b => b.required);
        if (blockers.length > 0) {
          // System not ready. force Launch so blockers are visible.
          const btn = document.querySelector('.tab-btn[data-tab="launch"]');
          if (btn) btn.click();
          return;
        }
        // Ready: honor saved tab if it exists and is non-default.
        if (saved && saved !== 'make') {
          const btn = document.querySelector(`.tab-btn[data-tab="${saved}"]`);
          if (btn) btn.click();
        }
        // Otherwise stay on Create Video (the HTML default).
      })
      .catch(() => { /* no readiness. stay on Create Video default */ });
  } catch (_) {}
})();
$('advancedNavToggle')?.addEventListener('click', () => {
  document.querySelector('.tabs')?.classList.toggle('show-advanced');
});

$('speed').addEventListener('input', (e) => {
  $('speedVal').textContent = parseFloat(e.target.value).toFixed(2) + 'x';
});

$('ttsBtn').addEventListener('click', async () => {
  const text = $('scriptText').value.trim();
  if (!text) { toast('No script to narrate', true); return; }
  const body = {
    text,
    voice: $('voice').value,
    speed: parseFloat($('speed').value),
    format: $('audioFmt').value,
    title: $('titleDisplay').textContent || 'narration',
  };
  $('ttsBtn').disabled = true;
  $('ttsDownloadBtn').disabled = true;
  $('ttsAudio').style.display = 'none';
  $('ttsAudio').removeAttribute('src');
  $('ttsPanel').style.display = 'block';
  $('ttsStatus').textContent = 'starting…';
  $('ttsSegment').textContent = '-';
  $('ttsChars').textContent = '0 / ' + text.length.toLocaleString();
  $('ttsProgress').style.width = '0%';
  $('ttsPathHint').textContent = '';

  try {
    const r = await fetch('/api/tts/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'failed to start');
    ttsJob = d.job_id;
    if (ttsTimer) clearInterval(ttsTimer);
    ttsTimer = setInterval(pollTts, 1500);
    pollTts();
  } catch (e) {
    toast(e.message || 'TTS failed to start', true);
    $('ttsStatus').textContent = 'error';
    $('ttsBtn').disabled = false;
  }
});

async function pollTts() {
  if (!ttsJob) return;
  try {
    const r = await fetch('/api/tts/status/' + ttsJob);
    const d = await r.json();
    if (!d.ok) return;
    const j = d.job;
    $('ttsStatus').textContent = j.status || '-';
    $('ttsSegment').textContent = j.segment || '-';
    const total = j.chars_total || 1;
    $('ttsChars').textContent = (j.chars_done || 0).toLocaleString() + ' / ' + total.toLocaleString();
    const pct = Math.min(100, Math.round(((j.chars_done || 0) / total) * 100));
    $('ttsProgress').style.width = pct + '%';

    if (j.error) {
      toast('TTS error: ' + j.error, true);
    }
    if (j.done) {
      clearInterval(ttsTimer); ttsTimer = null;
      $('ttsBtn').disabled = false;
      if (!j.error) {
        $('ttsStatus').textContent = 'done';
        $('ttsProgress').style.width = '100%';
        const url = '/api/tts/download/' + ttsJob;
        const audio = $('ttsAudio');
        audio.src = url + '?inline=1';
        audio.style.display = 'block';
        $('ttsDownloadBtn').disabled = false;
        $('ttsPathHint').textContent = 'saved as .' + (j.audio_ext || 'audio');
      }
    }
  } catch (e) { /* keep polling */ }
}

$('ttsDownloadBtn').addEventListener('click', () => {
  if (!ttsJob) return;
  window.location = '/api/tts/download/' + ttsJob;
});

// ---------- Short script tab ----------
let shortJob = null;
let shortTimer = null;
let shortWords = 280;

// Niche presets sourced from the Viral Shorts Niche Playbook (April 2026).
// Each chip pre-fills the topic + description so the model writes a script
// that fits the niche's hook and pacing.
const NICHES = [
  { key: "deepsea", label: "Deep-Sea Creatures",
    topic: "A research submersible four kilometers down captures something on its forward camera that no one in the lab can identify, and the signal cuts out before the recording can be recovered.",
    desc: "Single-narrator voiceover. Calm, eerie, slow-build dread. Treat the deep ocean like outer space. End on an unanswered question, not a jump scare." },
  { key: "factory", label: "Asian Factories",
    topic: "An overnight shift at a precision-parts factory in Shenzhen captures a machine producing a part that wasn't in the day's order list - and the part isn't on any blueprint anyone can find.",
    desc: "Process-curiosity voiceover. Oddly satisfying, contemplative. Marvel at the machinery, then twist into a quiet mystery. Avoid action language." },
  { key: "rube", label: "Rube Goldberg / Chain Reaction",
    topic: "A backyard contraption with 412 steps reaches its final domino, and the final domino does not fall. The builder rewinds the footage and finds something behind the dominoes that wasn't there when they set it up.",
    desc: "Suspense-driven voiceover. Talk like the camera is watching with us. Build to a held-breath payoff - viewers must NEED to see the ending." },
  { key: "weather", label: "Extreme Weather",
    topic: "Storm chasers tracking a supercell across Oklahoma capture a structure inside the rotation that meteorologists later say should not be physically possible.",
    desc: "Awe-and-dread voiceover. Cinematic, calm, factual cadence. One-in-a-lifetime tone. Each sentence raises the stakes." },
  { key: "shark", label: "Dangerous Sea Creatures",
    topic: "A spearfisher off the coast of Western Australia hears a sound underwater that doesn't match anything in the marine biologist's database, and his GoPro picks up a shape moving toward him in the murk.",
    desc: "Fear-and-fascination voiceover. Tight, present-tense, every sentence makes the next one more dangerous. Restrained - no gore." },
  { key: "drones", label: "Drone Light Shows",
    topic: "A weekly drone show in Seoul forms a shape no one in the program planned - and the company that operates the drones cannot find the file that told them to draw it.",
    desc: "Spectacle-then-mystery voiceover. Calm, observational. Begin with the wonder of the show, end with a quiet impossible detail." },
  { key: "streetfood", label: "Regional Street Food",
    topic: "A 70-year-old vendor in a Seoul night market makes a dish that takes nine minutes to prepare and twenty seconds to disappear, and the recipe is one she promised her grandmother she would never write down.",
    desc: "Sensory voiceover. Warm, patient, almost ASMR. Lean on sound and texture. End on a small emotional revelation, not a twist." },
  { key: "artisan", label: "Artisan Craft Transformations",
    topic: "A swordsmith in rural Japan finishes a blade that, when polished, reveals a name etched into the steel - a name that wasn't in any of the materials he used.",
    desc: "Process-to-revelation voiceover. Slow, reverent, observational. Let the transformation speak. End on the unexplained inscription." },
  { key: "spectacle", label: "Cultural Spectacles",
    topic: "A holographic festival in Shanghai displays an emperor who never existed in any historical record, and tourists leave the show describing memories of a country that isn't on the map.",
    desc: "Wonder-tipping-into-uncanny voiceover. Calm, curious, then increasingly unsettled. Restrained pacing." },
  { key: "vinyl", label: "Vintage Music & Vinyl",
    topic: "A collector buys a sealed jazz LP from 1962 at an estate sale, plays it once, and discovers a track on side B that the record's official catalog has never listed.",
    desc: "Slow, audiophile, late-night-radio voiceover. Almost hushed. Treat the record like a haunted artifact." },
  { key: "dashcam", label: "Dashcam (country-specific)",
    topic: "A trucker's dashcam in northern Russia records a vehicle ahead of him on an empty road that does not appear in his rear-view footage three minutes later, and the road has no exits.",
    desc: "Plain, matter-of-fact narrator. Let the footage speak. Build dread through what is missing, not what is shown." },
  { key: "press", label: "Hydraulic Press / Industrial",
    topic: "An industrial press at a recycling plant crushes an object an operator can't identify, and the metal it produces afterward weighs more than the object that went in.",
    desc: "Curious, cool, slightly mischievous voiceover. Lean on physics and weight. End on the impossible measurement." },
];

(function buildNicheChips() {
  const wrap = document.getElementById('nicheChips');
  if (!wrap) return;
  for (const n of NICHES) {
    const el = document.createElement('span');
    el.className = 'tag';
    el.textContent = n.label;
    el.title = n.desc;
    el.addEventListener('click', () => {
      $('shortTopic').value = n.topic;
      $('shortDesc').value = n.desc;
      // Visually mark the active chip.
      wrap.querySelectorAll('.tag').forEach(t => t.classList.remove('active'));
      el.classList.add('active');
    });
    wrap.appendChild(el);
  }
})();

document.querySelectorAll('#durButtons .dur-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#durButtons .dur-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    shortWords = parseInt(btn.dataset.words, 10);
  });
});

$('shortBtn').addEventListener('click', async () => {
  const body = {
    topic: $('shortTopic').value.trim(),
    description: $('shortDesc').value.trim(),
    genre: $('shortGenre').value.trim(),
    tone: $('shortTone').value.trim(),
    word_count: shortWords,
    model: $('shortModel').value.trim(),
  };
  $('shortBtn').disabled = true;
  $('shortStatusPanel').style.display = 'block';
  $('shortOutput').classList.remove('shown');
  $('shortTitleDisplay').textContent = 'Working…';
  $('shortTitleDisplay').classList.remove('empty');
  $('shortStatus').textContent = 'starting';
  $('shortWords').textContent = '0';
  $('shortTarget').textContent = shortWords;
  $('shortProgress').style.width = '0%';

  try {
    const r = await fetch('/api/start_short', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'failed to start');
    shortJob = d.job_id;
    if (shortTimer) clearInterval(shortTimer);
    shortTimer = setInterval(pollShort, 1500);
    pollShort();
  } catch (e) {
    toast(e.message || 'Failed to start', true);
    $('shortBtn').disabled = false;
    $('shortStatus').textContent = 'error';
  }
});

async function pollShort() {
  if (!shortJob) return;
  try {
    const r = await fetch('/api/status/' + shortJob);
    const d = await r.json();
    if (!d.ok) return;
    const j = d.job;
    if (j.title) {
      $('shortTitleDisplay').textContent = j.title;
      $('shortTitleDisplay').classList.remove('empty');
    }
    $('shortStatus').textContent = j.status || '-';
    $('shortWords').textContent = (j.words || 0).toLocaleString();
    $('shortTarget').textContent = j.target || shortWords;
    const target = j.target || shortWords || 1;
    const pct = Math.min(100, Math.round(((j.words || 0) / target) * 100));
    $('shortProgress').style.width = pct + '%';

    if (j.error) toast('Error: ' + j.error, true);
    if (j.done) {
      clearInterval(shortTimer); shortTimer = null;
      $('shortBtn').disabled = false;
      if (!j.error) {
        $('shortStatus').textContent = 'done';
        $('shortProgress').style.width = '100%';
        const sr = await fetch('/api/script/' + shortJob);
        const sd = await sr.json();
        if (sd.ok) {
          $('shortScriptText').value = sd.text;
          $('shortPathHint').textContent = 'saved to ' + sd.path;
          $('shortOutput').classList.add('shown');
          $('shortNextActions').style.display = '';
          $('shortProductionHint').style.display = '';
          $('shortProductionHint').textContent = 'Script ready. Build a production kit to create narration, scene prompts, an aligned timeline, and a draft MP4.';
          $('shortOutput').scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }
    }
  } catch (e) { /* keep polling */ }
}

$('shortCopyBtn').addEventListener('click', async () => {
  const text = $('shortScriptText').value;
  try { await navigator.clipboard.writeText(text); toast('Copied'); }
  catch { $('shortScriptText').select(); document.execCommand('copy'); toast('Copied'); }
});

$('shortDownloadBtn').addEventListener('click', () => {
  if (!shortJob) return;
  window.location = '/api/download/' + shortJob;
});

$('shortNarrateBtn').addEventListener('click', () => {
  sendShortToNarrate();
});

$('shortVideoBtn').addEventListener('click', () => {
  sendToVideoStudio($('shortScriptText').value, $('shortTitleDisplay').textContent || '');
});

$('shortShowScriptBtn').addEventListener('click', () => {
  $('shortOutput').classList.add('shown');
  $('shortOutput').scrollIntoView({ behavior: 'smooth', block: 'start' });
});

$('shortNarrateTopBtn').addEventListener('click', sendShortToNarrate);

$('shortVideoTopBtn').addEventListener('click', () => {
  sendToVideoStudio($('shortScriptText').value, $('shortTitleDisplay').textContent || '');
});

// ---------- Paste-to-narrate tab ----------
let pasteJob = null;
let pasteTimer = null;

function updatePasteCount() {
  const t = $('pasteText').value;
  const chars = t.length;
  const words = t.trim() ? t.trim().split(/\s+/).length : 0;
  $('pasteCount').textContent = chars.toLocaleString() + ' characters · ' + words.toLocaleString() + ' words';
}

$('pasteText').addEventListener('input', updatePasteCount);

$('pasteSpeed').addEventListener('input', (e) => {
  $('pasteSpeedVal').textContent = parseFloat(e.target.value).toFixed(2) + 'x';
});

$('pasteBtn').addEventListener('click', async () => {
  const text = $('pasteText').value.trim();
  if (!text) { toast('Paste some text first', true); return; }
  const body = {
    text,
    voice: $('pasteVoice').value,
    speed: parseFloat($('pasteSpeed').value),
    format: $('pasteFmt').value,
    title: $('pasteTitle').value.trim() || 'narration',
  };
  $('pasteBtn').disabled = true;
  $('pasteDownloadBtn').disabled = true;
  $('pasteAudio').style.display = 'none';
  $('pasteAudio').removeAttribute('src');
  $('pastePanel').style.display = 'block';
  $('pasteStatus').textContent = 'starting…';
  $('pasteSegment').textContent = '-';
  $('pasteChars').textContent = '0 / ' + text.length.toLocaleString();
  $('pasteProgress').style.width = '0%';
  $('pastePathHint').textContent = '';

  try {
    const r = await fetch('/api/tts/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'failed to start');
    pasteJob = d.job_id;
    if (pasteTimer) clearInterval(pasteTimer);
    pasteTimer = setInterval(pollPaste, 1500);
    pollPaste();
  } catch (e) {
    toast(e.message || 'TTS failed to start', true);
    $('pasteStatus').textContent = 'error';
    $('pasteBtn').disabled = false;
  }
});

async function pollPaste() {
  if (!pasteJob) return;
  try {
    const r = await fetch('/api/tts/status/' + pasteJob);
    const d = await r.json();
    if (!d.ok) return;
    const j = d.job;
    $('pasteStatus').textContent = j.status || '-';
    $('pasteSegment').textContent = j.segment || '-';
    const total = j.chars_total || 1;
    $('pasteChars').textContent = (j.chars_done || 0).toLocaleString() + ' / ' + total.toLocaleString();
    const pct = Math.min(100, Math.round(((j.chars_done || 0) / total) * 100));
    $('pasteProgress').style.width = pct + '%';

    if (j.error) {
      toast('TTS error: ' + j.error, true);
    }
    if (j.done) {
      clearInterval(pasteTimer); pasteTimer = null;
      $('pasteBtn').disabled = false;
      if (!j.error) {
        $('pasteStatus').textContent = 'done';
        $('pasteProgress').style.width = '100%';
        const audio = $('pasteAudio');
        audio.src = '/api/tts/download/' + pasteJob + '?inline=1';
        audio.style.display = 'block';
        $('pasteDownloadBtn').disabled = false;
        $('pastePathHint').textContent = 'saved as .' + (j.audio_ext || 'audio');
      }
    }
  } catch (e) { /* keep polling */ }
}

$('pasteDownloadBtn').addEventListener('click', () => {
  if (!pasteJob) return;
  window.location = '/api/tts/download/' + pasteJob;
});

// ---------- Video Studio tab ----------
let videoPlan = null;
let videoProject = null;
let timeline = null;
let timelineProject = null;
let draftVideoJob = null;
let draftVideoTimer = null;

function updateVideoCount() {
  const text = $('videoScript').value.trim();
  const words = text ? text.split(/\s+/).length : 0;
  const seconds = parseInt($('videoSceneSeconds').value, 10) || 8;
  const estimate = words ? Math.max(1, Math.ceil(words / Math.max(18, seconds * 2.35))) : 0;
  $('videoCount').textContent = words.toLocaleString() + ' words · about ' + estimate.toLocaleString() + ' scenes';
}

function sendToVideoStudio(text, title) {
  if (!text || !text.trim()) {
    toast('No script to send yet', true);
    return;
  }
  $('videoScript').value = text;
  if (title && !/^starting|working|no job/i.test(title)) $('videoTitle').value = title;
  updateVideoCount();
  document.querySelector('.tab-btn[data-tab="video"]').click();
}

function sendShortToNarrate() {
  const text = $('shortScriptText').value;
  const title = $('shortTitleDisplay').textContent;
  $('pasteText').value = text.replace(/^TITLE:\s*/i, '');
  $('pasteTitle').value = title || 'narration';
  updatePasteCount();
  document.querySelector('.tab-btn[data-tab="paste"]').click();
}

function promptsText(plan) {
  if (!plan) return '';
  return plan.scenes.map(scene => [
    `SCENE ${scene.id} | ${scene.start} | ${scene.duration_seconds}s`,
    'IMAGE PROMPT:',
    scene.image_prompt,
    '',
    'VIDEO PROMPT:',
    scene.video_prompt,
    '',
    'NEGATIVE PROMPT:',
    scene.negative_prompt,
  ].join('\n')).join('\n\n');
}

function timelineText(tl) {
  if (!tl) return '';
  return tl.scenes.map(scene =>
    `${scene.clip_file} | ${scene.start} - ${scene.end} | ${scene.duration_seconds}s\n${scene.video_prompt}`
  ).join('\n\n');
}

function renderVideoPlan(plan, project) {
  videoPlan = plan;
  videoProject = project || null;
  $('videoPlanPanel').style.display = 'block';
  $('videoSceneCount').textContent = (plan.scene_count || 0).toLocaleString();
  $('videoRuntime').textContent = plan.estimated_runtime || '-';
  $('videoPathHint').textContent = project ? 'saved to project library' : '';
  $('videoDownloadPlanBtn').disabled = !(project && project.files && project.files.scene_plan);
  $('videoDownloadPromptsBtn').disabled = !(project && project.files && project.files.prompts);

  const scenes = plan.scenes || [];
  const shown = scenes.slice(0, 80);
  $('videoScenes').innerHTML = shown.map(scene => `
    <div class="scene-card">
      <div class="scene-head">
        <span>Scene ${scene.id}</span>
        <span>${escapeHtml(scene.start)} · ${scene.duration_seconds}s</span>
      </div>
      <p>${escapeHtml(scene.narration)}</p>
      <div class="prompt-box">${escapeHtml(scene.video_prompt)}</div>
    </div>
  `).join('') + (scenes.length > shown.length ? `
    <div class="empty-state" style="grid-column:1/-1;">
      <h3>${(scenes.length - shown.length).toLocaleString()} more scenes in the exported prompt file</h3>
      <p>The page previews the first 80 cards so the browser stays quick.</p>
    </div>` : '');
}

$('videoScript').addEventListener('input', updateVideoCount);
$('videoSceneSeconds').addEventListener('input', updateVideoCount);
$('videoVisualPreset').addEventListener('change', () => applyVisualPreset('video'));
$('makeVisualPreset').addEventListener('change', () => applyVisualPreset('make'));
$('makeRecipe').addEventListener('change', () => { applyChannelRecipe(true); updateMakeChoiceCards(); updateMakeReadiness(); });
$('makeFormat').addEventListener('change', () => { updateViralStage(); updateMakeReadiness(); });
$('makeSaveBrandBtn').addEventListener('click', saveBrandKit);
$('makeLoadBrandBtn').addEventListener('click', loadBrandKit);

function updateMakeChoiceCards() {
  document.querySelectorAll('[id^="makeRecipeCards"] .choice-card').forEach(card => {
    card.classList.toggle('active', card.dataset.recipe === $('makeRecipe').value);
  });
  document.querySelectorAll('#makeVisualSourceCards .choice-card').forEach(card => {
    card.classList.toggle('active', card.dataset.mode === $('makeVideoMode').value);
  });
}

let makeIdeaSelected = false;
let makeSelectedIdea = null;

function updateTitleIdeaGate() {
  const btn = $('makeTitleIdeasBtn');
  if (!btn) return;
  btn.disabled = !makeIdeaSelected;
  btn.title = makeIdeaSelected
    ? 'Generate titles for the selected idea'
    : 'Generate and select an idea first';
}

function selectedCardText(selector, fallback) {
  const active = document.querySelector(`${selector} .choice-card.active strong`);
  return active ? active.textContent.trim() : fallback;
}

let makePreviewPlatform = 'tiktok';

const PLATFORM_PREVIEWS = {
  tiktok: {
    tabs: '<span>Explore</span><span>Following</span><strong>For You</strong>',
    topLeft: 'Q',
    topIcons: '',
    actions: [
      ['profile', '2.8M'],
      ['like', '2.8M'],
      ['chat', '2.8M'],
      ['save', '2.8M'],
      ['send', '2.8M'],
    ],
    meta: '<strong>@your_channel</strong><span>Caption safe preview. Keep subtitles clear of the action rail and description area.</span>',
  },
  shorts: {
    tabs: '',
    topLeft: '',
    topIcons: '<span>Q</span><span>...</span>',
    actions: [
      ['like', '2.8M'],
      ['dislike', 'Dislike'],
      ['chat', '2.8M'],
      ['send', '2.8M'],
      ['mix', '2.8M'],
    ],
    meta: '<strong>@your_channel</strong><span>Shorts preview. Captions should sit above the handle and away from buttons.</span>',
  },
  reels: {
    tabs: '',
    topLeft: 'Q',
    topIcons: '<span>Q</span><span>cam</span>',
    actions: [
      ['like', '2.8M'],
      ['chat', '2.8M'],
      ['send', '2.8M'],
      ['more', ''],
      ['camera', ''],
    ],
    meta: '<strong>@your_channel</strong><span>Reels preview. Safe captions avoid the lower caption stack.</span><br><span>Music name</span>',
  },
};

function updatePlatformPreview(platform = makePreviewPlatform) {
  makePreviewPlatform = PLATFORM_PREVIEWS[platform] ? platform : 'tiktok';
  const config = PLATFORM_PREVIEWS[makePreviewPlatform];
  const shell = $('makePhonePreviewInner');
  if (shell) shell.dataset.platform = makePreviewPlatform;
  const tabs = document.querySelector('#makePlatformUi .platform-tabs');
  const topLeft = document.querySelector('#makePlatformUi .platform-top-left');
  const topIcons = document.querySelector('#makePlatformUi .platform-top-icons');
  const actions = $('makePlatformActions');
  const meta = $('makePlatformMeta');
  if (tabs) tabs.innerHTML = config.tabs;
  if (topLeft) topLeft.textContent = config.topLeft;
  if (topIcons) topIcons.innerHTML = config.topIcons;
  if (actions) {
    actions.innerHTML = config.actions.map(([icon, count]) => `
      <div class="platform-action"><span class="icon" data-icon="${escapeHtml(icon)}"></span>${count ? `<small>${escapeHtml(count)}</small>` : ''}</div>
    `).join('');
  }
  if (meta) meta.innerHTML = config.meta;
  document.querySelectorAll('#makePlatformPreviewToggle button').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.platform === makePreviewPlatform);
  });
}

function updateStudioPreview() {
  const title = $('makePreferredTitle')?.value.trim() || selectedCardText('[id^="makeRecipeCards"]','Phantomline short');
  const topic = $('makeTopic')?.value.trim() || '';
  const hookLine = topic.split(/[.!?\n]/).map(s => s.trim()).find(Boolean) || 'Pick a hook';
  const words = hookLine.split(/\s+/).filter(Boolean).slice(0, 4);
  const captionWords = words.length ? words : ['Pick', 'a', 'hook'];
  const highlighted = `${escapeHtml(captionWords.slice(0, -1).join(' '))} <span>${escapeHtml(captionWords[captionWords.length - 1])}</span>`.trim();
  const durationLabels = {
    70: '30 sec', 95: '40 sec', 140: '1 min', 280: '2 min',
    700: '5 min', 1400: '10 min', 2800: '20 min', 4200: '30 min', 8400: '60 min',
  };
  if ($('makePreviewTitle')) $('makePreviewTitle').textContent = title.slice(0, 54);
  if ($('makePreviewCaption')) $('makePreviewCaption').innerHTML = highlighted;
  if ($('makePreviewRatio')) $('makePreviewRatio').textContent = $('makeAspect')?.value || '9:16';
  if ($('makePreviewMeta')) $('makePreviewMeta').textContent = isViralStoryMode() ? 'Loop-ready' : 'Local render';
  if ($('makeSpecFormat')) $('makeSpecFormat').textContent = selectedCardText('[id^="makeRecipeCards"]','Custom');
  if ($('makeSpecVisual')) $('makeSpecVisual').textContent = $('makeVideoMode')?.value === 'source' ? 'Retention' : 'AI scenes';
  if ($('makeSpecLength')) $('makeSpecLength').textContent = durationLabels[$('makeDuration')?.value] || 'Custom';
  updatePlatformPreview();
}

document.querySelectorAll('#makePlatformPreviewToggle button').forEach(btn => {
  btn.addEventListener('click', () => updatePlatformPreview(btn.dataset.platform || 'tiktok'));
});

document.querySelectorAll('[id^="makeRecipeCards"] .choice-card').forEach(card => {
  card.addEventListener('click', () => {
    $('makeRecipe').value = card.dataset.recipe || 'custom';
    makeIdeaSelected = false;
    makeSelectedIdea = null;
    $('makePreferredTitle').value = '';
    $('makeTitleDeck').style.display = 'none';
    $('makeTitleDeck').innerHTML = '';
    applyChannelRecipe(true);
    updateMakeChoiceCards();
    updateViralStage();
    updateTitleIdeaGate();
    updateMakeReadiness();
  });
});

document.querySelectorAll('#makeTensionCards .choice-card').forEach(card => {
  card.addEventListener('click', () => {
    $('makeTensionFormat').value = card.dataset.tension || 'betrayal_drama';
    updateViralStage();
    updateMakeReadiness();
  });
});

$('makeTensionFormat').addEventListener('change', () => { updateViralStage(); updateMakeReadiness(); });
$('makeLoopType').addEventListener('change', updateMakeReadiness);
$('makePinnedComment').addEventListener('input', updateMakeReadiness);
$('makeHashtags').addEventListener('input', updateMakeReadiness);

document.querySelectorAll('#makeVisualSourceCards .choice-card').forEach(card => {
  card.addEventListener('click', () => {
    $('makeVideoMode').value = card.dataset.mode || 'generated';
    updateMakeVideoMode();
    updateMakeChoiceCards();
    updateMakeReadiness();
  });
});

function viralHookLooksStrong(text) {
  const first = (text || '').split(/[.!?\n]/).map(s => s.trim()).find(Boolean) || '';
  const words = first.split(/\s+/).filter(Boolean);
  if (words.length < 5 || words.length > 16) return false;
  const lower = first.toLowerCase();
  const hasPerson = /\b(i|my|me|mom|dad|boyfriend|girlfriend|wife|husband|best friend|roommate|neighbor|sister|brother|someone|he|she)\b/.test(lower);
  const hasAction = /\b(checked|opened|found|sent|saw|heard|called|texted|lied|hid|married|invited|answered|followed)\b/.test(lower);
  const hasDanger = /\b(wrong|second|secret|never|again|tomorrow|already|missing|name|number|door|phone|message|screenshot|dead|same)\b/.test(lower);
  const weakStart = /^(this is|one day|have you ever|let me tell|today|in this video)\b/.test(lower);
  return hasPerson && hasAction && hasDanger && !weakStart;
}

function setMakeNextAction(text, label, handler) {
  const textEl = $('makeNextActionText');
  const btn = $('makeNextActionBtn');
  if (textEl) textEl.textContent = text;
  if (btn) {
    btn.textContent = label;
    btn.onclick = handler;
  }
}

function updateMakeNextAction(score) {
  const mode = $('makeVideoMode').value;
  const sourceMode = mode === 'source';
  const libraryMode = mode === 'library';
  const usesVideoSource = sourceMode || libraryMode;
  const hasIdea = $('makeTopic').value.trim().length > 20;
  const hasTitle = $('makePreferredTitle').value.trim().length > 8;
  const hasSource = !!(($('makeSourceVideoFile')?.files && $('makeSourceVideoFile').files[0]) || makeSourceLibraryPick);
  if (!hasIdea) {
    setMakeNextAction('Start with a proven package: load a demo or generate fresh ideas for this niche.', 'Generate ideas', () => {
      $('makeShuffleIdeaBtn')?.closest('.make-stage')?.scrollIntoView({behavior:'smooth', block:'center'});
      $('makeShuffleIdeaBtn')?.click();
    });
  } else if (!makeIdeaSelected) {
    setMakeNextAction('Pick one idea so titles, script, description, and hashtags anchor to the same concept.', 'Pick an idea', () => {
      $('makeIdeaDeck')?.scrollIntoView({behavior:'smooth', block:'center'});
    });
  } else if (!hasTitle) {
    setMakeNextAction('Generate titles from the selected idea so the upload package stays specific and clickable.', 'Generate titles', () => {
      $('makeTitleIdeasBtn')?.click();
    });
  } else if (libraryMode && !hasSource) {
    setMakeNextAction('Pick a Phantomline footage clip to use as your visual layer.', 'Browse footage', () => {
      $('browseFootageLibraryBtn')?.click();
    });
  } else if (sourceMode && !hasSource) {
    setMakeNextAction('Upload the retention footage that will carry the short-form visual layer.', 'Upload footage', () => {
      $('makeSourceVideoFile')?.click();
    });
  } else if (score >= 85) {
    setMakeNextAction('This package is ready. Render the MP4, then Phantomline will prepare the publish draft.', 'Make video', () => {
      $('makeVideoBtn')?.click();
    });
  } else {
    setMakeNextAction('One or two quality gates still need attention before rendering.', 'Review checklist', () => {
      $('makeReadinessItems')?.scrollIntoView({behavior:'smooth', block:'center'});
    });
  }
}

function updateMakeReadiness() {
  const mode = $('makeVideoMode').value;
  const sourceMode = mode === 'source';
  const libraryMode = mode === 'library';
  const usesVideoSource = sourceMode || libraryMode;
  const viralMode = isViralStoryMode();
  const titleWords = $('makePreferredTitle').value.trim().split(/\s+/).filter(Boolean).length;
  const sourceLabel = libraryMode ? 'Phantomline footage chosen' : (sourceMode ? 'Source video chosen' : 'Local visuals ready');
  const sourceOk = usesVideoSource
    ? !!(($('makeSourceVideoFile').files && $('makeSourceVideoFile').files[0]) || makeSourceLibraryPick)
    : true;
  const checks = [
    { label: 'Idea selected', ok: $('makeTopic').value.trim().length > 20 },
    { label: 'Title/package chosen', ok: $('makePreferredTitle').value.trim().length > 8 },
    { label: 'Format selected', ok: $('makeRecipe').value !== 'custom' || $('makeNiche').value.trim().length > 3 },
    { label: viralMode ? '9:16 viral short ready' : 'Short-form ratio ready', ok: $('makeAspect').value === '9:16' || $('makeDuration').value !== '70' },
    ...(viralMode ? [
      { label: 'Tension format chosen', ok: !!$('makeTensionFormat').value },
      { label: 'Hook has crisis energy', ok: viralHookLooksStrong($('makeTopic').value.trim()) },
      { label: 'Loop ending selected', ok: !!$('makeLoopType').value },
      { label: 'Short title shape', ok: titleWords > 0 && titleWords <= 6 },
      { label: 'Pinned comment ready', ok: $('makePinnedComment').value.trim().length > 8 },
    ] : []),
    { label: sourceLabel, ok: sourceOk },
    { label: 'Captions / keywords configured', ok: !usesVideoSource || $('makeCaptions').value === '1' },
  ];
  const score = Math.round((checks.filter(c => c.ok).length / checks.length) * 100);
  const scoreEl = $('makeReadinessScore');
  const prevScore = scoreEl.textContent;
  scoreEl.textContent = score;
  if (prevScore !== String(score)) {
    scoreEl.classList.remove('bump');
    void scoreEl.offsetWidth;
    scoreEl.classList.add('bump');
  }
  $('makeReadinessItems').innerHTML = checks.map(c =>
    `<div class="readiness-item ${c.ok ? 'ok' : 'miss'}">${c.ok ? '✓' : 'Needs'} ${escapeHtml(c.label)}</div>`
  ).join('').replace(/âœ“/g, 'Ready');
  updateStudioPreview();
  updateMakeNextAction(score);
}

function makeLaunchChecklist() {
  return [
    { label: 'Final MP4 rendered', ok: !!latestMakeVideoProjectId },
    { label: 'Preview playable', ok: !!$('makePreviewVideo')?.getAttribute('src') },
    { label: 'Title ready', ok: $('publishTitle')?.value.trim().length > 8 || $('makePreferredTitle')?.value.trim().length > 8 },
    { label: 'Description ready', ok: $('publishCaption')?.value.trim().length > 40 },
    { label: 'Tags ready', ok: $('publishTags')?.value.trim().length > 3 || $('makeHashtags')?.value.trim().length > 3 },
    { label: 'Schedule time set', ok: !!$('publishScheduledAt')?.value },
  ];
}

function renderMakeHandoffChecklist() {
  const el = $('makeHandoffChecklist');
  if (!el) return;
  el.innerHTML = makeLaunchChecklist().map(item =>
    `<div class="check-chip ${item.ok ? 'ok' : ''}">${item.ok ? 'Ready' : 'Check'} - ${escapeHtml(item.label)}</div>`
  ).join('');
}

function renderPublishReadinessChecklist() {
  const el = $('publishReadinessChecklist');
  if (!el) return;
  const checks = [
    { label: 'MP4 selected', ok: !!$('publishVideoProject')?.value },
    { label: 'Title', ok: $('publishTitle')?.value.trim().length > 8 },
    { label: 'Description', ok: $('publishCaption')?.value.trim().length > 40 },
    { label: 'Tags', ok: $('publishTags')?.value.trim().length > 3 },
    { label: 'Schedule', ok: !!$('publishScheduledAt')?.value },
    { label: 'YouTube', ok: !!publishStatus?.youtube_connected },
  ];
  el.innerHTML = checks.map(item =>
    `<div class="check-chip ${item.ok ? 'ok' : ''}">${item.ok ? 'Ready' : 'Needs'} - ${escapeHtml(item.label)}</div>`
  ).join('');
}

async function shuffleMakeIdeas() {
  const btn = $('makeShuffleIdeaBtn');
  const deck = $('makeIdeaDeck');
  const status = $('makeIdeaStatus');
  btn.disabled = true;
  status.textContent = 'Asking Ollama for fresh angles...';
  deck.style.display = 'none';
  deck.innerHTML = '';
  $('makeTitleDeck').style.display = 'none';
  $('makeTitleDeck').innerHTML = '';
  makeIdeaSelected = false;
  makeSelectedIdea = null;
  updateTitleIdeaGate();
  try {
    const d = await apiJson('/api/ideas/video', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        model: $('makeModel').value.trim(),
        recipe: $('makeRecipe').value,
        niche: $('makeNiche').value.trim(),
        audience: $('makeAudience').value.trim(),
        format: $('makeFormat').value,
        hook_style: $('makeHookStyle').value,
        tension_format: $('makeTensionFormat').value,
        loop_type: $('makeLoopType').value,
        current_topic: $('makeTopic').value.trim(),
      }),
    });
    for (const idea of d.ideas || []) {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'idea-card';
      card.innerHTML = `
        <strong>${escapeHtml(idea.title || 'Video idea')}</strong>
        <span>${escapeHtml(idea.topic || '')}</span>
        <div class="idea-badges">
          ${idea.hook ? '<span class="idea-badge">Hook</span>' : ''}
          ${idea.structure && idea.structure.twist ? '<span class="idea-badge">Twist</span>' : ''}
          ${idea.structure && idea.structure.loop_ending ? '<span class="idea-badge">Loop</span>' : ''}
          ${idea.pinned_comment ? '<span class="idea-badge">Comment</span>' : ''}
        </div>
        ${idea.hook ? `<span style="margin-top:6px;">Hook: ${escapeHtml(idea.hook)}</span>` : ''}
        ${idea.structure && idea.structure.twist ? `<span style="margin-top:6px;">Twist: ${escapeHtml(idea.structure.twist)}</span>` : ''}
        ${idea.pinned_comment ? `<span style="margin-top:6px;">Comment: ${escapeHtml(idea.pinned_comment)}</span>` : ''}
      `;
      card.addEventListener('click', () => {
        $('makeTopic').value = idea.topic || '';
        makeIdeaSelected = true;
        makeSelectedIdea = idea;
        updateTitleIdeaGate();
        $('makeTitlePackage').style.display = 'block';
        $('makePreferredTitle').value = '';
        $('makeTitleDeck').style.display = 'none';
        $('makeTitleDeck').innerHTML = '';
        const structure = idea.structure || {};
        const viralNotes = [
          idea.hook ? `Chosen hook: ${idea.hook}` : '',
          structure.setup ? `Setup: ${structure.setup}` : '',
          structure.escalation ? `Escalation: ${structure.escalation}` : '',
          structure.twist ? `Twist: ${structure.twist}` : '',
          structure.loop_ending ? `Loop ending: ${structure.loop_ending}` : '',
          idea.caption_beats && idea.caption_beats.length
            ? `Caption beats: ${idea.caption_beats.map(b => `${b.text}${b.highlight ? ` [${b.highlight}]` : ''}`).join(' / ')}`
            : '',
        ].filter(Boolean).join('\n');
        if (viralNotes) $('makeDesc').value = `${viralNotes}\n\n${$('makeDesc').value.trim()}`.trim();
        if (idea.pinned_comment) $('makePinnedComment').value = idea.pinned_comment;
        if (idea.hashtags && idea.hashtags.length) $('makeHashtags').value = idea.hashtags.join(' ');
        if (idea.visual) $('makeVisualStyle').value = idea.visual;
        if (idea.music) $('makeMusicPrompt').value = idea.music;
        updateMakeReadiness();
        toast('Idea loaded. Generate titles next.');
        // Auto-pull-forward: focus and reveal the next action.
        const titleBtn = $('makeTitleIdeasBtn');
        if (titleBtn) {
          titleBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
          setTimeout(() => titleBtn.focus({ preventScroll: true }), 350);
        }
      });
      deck.appendChild(card);
    }
    deck.style.display = deck.children.length ? 'grid' : 'none';
    status.textContent = deck.children.length ? 'Pick one to load it.' : 'No ideas returned.';
  } catch (e) {
    status.textContent = '';
    toast(e.message || 'Could not generate ideas', true);
  } finally {
    btn.disabled = false;
  }
}
$('makeShuffleIdeaBtn').addEventListener('click', shuffleMakeIdeas);

async function generateMakeTitles() {
  if (!makeIdeaSelected) {
    toast('Generate ideas and pick one first', true);
    return;
  }
  const btn = $('makeTitleIdeasBtn');
  const deck = $('makeTitleDeck');
  const status = $('makeIdeaStatus');
  btn.disabled = true;
  status.textContent = 'Generating title options...';
  deck.style.display = 'none';
  deck.innerHTML = '';
  try {
    const d = await apiJson('/api/ideas/titles', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        model: $('makeModel').value.trim(),
        topic: $('makeTopic').value.trim(),
        niche: $('makeNiche').value.trim(),
        audience: $('makeAudience').value.trim(),
        recipe: $('makeRecipe').value,
        format: $('makeFormat').value,
        hook_style: $('makeHookStyle').value,
        tension_format: $('makeTensionFormat').value,
        loop_type: $('makeLoopType').value,
        selected_idea: makeSelectedIdea,
      }),
    });
    for (const title of d.titles || []) {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'idea-card';
      const fit = title.fit || null;
      const fitBadge = fit && d.insights_configured ? `
        <span class="fit-badge fit-${fit.verdict}" title="${escapeHtml((fit.reasons||[]).join(' • ') || 'No matched signal')}">
          ${fit.verdict === 'strong_fit' ? '✓ Strong fit' :
            fit.verdict === 'good_fit'   ? '✓ Good fit'   :
            fit.verdict === 'stretch'    ? '~ Stretch'    :
            fit.verdict === 'risky'      ? '! Risky'      : 'Neutral'}
          · ${fit.score}
        </span>` : '';
      const fitWhy = fit && d.insights_configured && (fit.reasons || []).length
        ? `<span class="fit-why">${escapeHtml(fit.reasons[0])}</span>` : '';
      card.innerHTML = `
        <strong>${escapeHtml(title.title || 'Untitled')}</strong>
        <div class="idea-badges">
          ${fitBadge || '<span class="idea-badge">Title</span>'}
          ${title.pinned_comment ? '<span class="idea-badge">Comment</span>' : ''}
          ${title.platform ? `<span class="idea-badge">${escapeHtml(title.platform)}</span>` : ''}
        </div>
        ${title.angle ? `<span>${escapeHtml(title.angle)}</span>` : ''}
        ${fitWhy}
        ${title.pinned_comment ? `<span style="margin-top:6px;">Comment: ${escapeHtml(title.pinned_comment)}</span>` : ''}
      `;
      card.addEventListener('click', () => {
        $('makePreferredTitle').value = title.title || '';
        if (title.pinned_comment) $('makePinnedComment').value = title.pinned_comment;
        $('makeTitlePackage').style.display = 'block';
        updateMakeReadiness();
        toast('Title selected');
        // Auto-pull-forward to the render button. the next obvious action.
        const renderBtn = $('makeVideoBtn');
        if (renderBtn) {
          renderBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
          setTimeout(() => renderBtn.focus({ preventScroll: true }), 350);
        }
      });
      deck.appendChild(card);
    }
    deck.style.display = deck.children.length ? 'grid' : 'none';
    $('makeTitlePackage').style.display = 'block';
    status.textContent = deck.children.length ? 'Pick a title to package the video.' : 'No titles returned.';
  } catch (e) {
    status.textContent = '';
    toast(e.message || 'Could not generate titles', true);
  } finally {
    updateTitleIdeaGate();
  }
}
$('makeTitleIdeasBtn').addEventListener('click', generateMakeTitles);

const LAUNCH_DEMOS = {
  reddit: {
    label: 'Reddit parkour short',
    recipe: 'viral-story',
    videoMode: 'source',
    duration: '140',
    aspect: '9:16',
    tension: 'betrayal_drama',
    loop: 'circular',
    title: 'I Checked His Phone',
    topic: 'I checked his phone while he was asleep and saw my name saved under a message thread I had never opened.',
    niche: 'viral Reddit-style story shorts',
    audience: 'short-form viewers who stop for betrayal, secrets, and relationship twists',
    tone: 'fast, tense, conversational, first-person, addictive',
    hookStyle: 'conflict',
    desc: 'Use a Reddit-style first-person confession. Start mid-crisis. Structure: shock hook, setup, escalation, twist, loop ending. Captions should be 2-5 word attention anchors. Use uploaded Minecraft parkour or obstacle-course footage as retention footage.',
    pinned: 'Would you have checked the phone?',
    hashtags: '#shorts #storytime #redditstory #plotwist #facelessyoutube',
    music: 'low suspense pulse, quiet ticking texture, soft bass tension, no vocals, ducked under narration',
    captionStyle: 'tiktok',
  },
  horror: {
    label: 'Rule horror short',
    recipe: 'viral-story',
    videoMode: 'generated',
    duration: '95',
    aspect: '9:16',
    tension: 'rule_horror',
    loop: 'hard-cut',
    title: 'Never Open The Red Door',
    topic: 'My mom left one rule on the fridge: if the red basement door is open, do not look inside.',
    niche: 'rule-based horror shorts',
    audience: 'short-form viewers who like simple rules, dread, and loop endings',
    tone: 'quiet, tense, cinematic, simple, unsettling',
    hookStyle: 'conflict',
    desc: 'Rule-based horror short. State one rule immediately, break it, escalate with one impossible clue, and end slightly before the explanation.',
    pinned: 'Would you have opened it?',
    hashtags: '#shorts #scarystory #horrorstory #plotwist #storytime',
    visualPreset: 'cinematic-photoreal',
    visualStyle: 'cinematic horror still, red basement door, realistic house interior, strong silhouette, no text',
    ambience: 'dark hallway, red warning glow, quiet dust, heavy shadow, suspense',
    music: 'minimal horror drone, low pulse, distant piano hit, no vocals',
    captionStyle: 'horror',
  },
  survival: {
    label: 'Survival tips host',
    recipe: 'survival',
    videoMode: 'generated',
    duration: '140',
    aspect: '9:16',
    title: 'Three Survival Mistakes',
    topic: 'Three survival mistakes that make people panic faster when they get lost outdoors.',
    niche: 'survival tips and outdoor safety',
    audience: 'beginners who want simple practical outdoor safety tips',
    tone: 'clear, calm, useful, visual, practical',
    hookStyle: 'mistake',
    desc: 'Use a small recurring backpacker host demonstrating each tip. Make every line visual and useful. Keep it beginner-friendly and avoid fearmongering.',
    pinned: 'Which survival tip did you learn too late?',
    hashtags: '#shorts #survivaltips #outdoorsafety #camping #facelessyoutube',
    visualPreset: 'survival-channel',
    music: 'light adventure bed, soft percussion, warm outdoor tone, no vocals',
    captionStyle: 'clean',
  },
  'ai-tool': {
    label: 'AI tool review',
    recipe: 'custom',
    videoMode: 'generated',
    duration: '140',
    aspect: '9:16',
    title: 'This AI Tool Saves Hours',
    topic: 'A faceless review explaining what makko.ai does, who it helps, and the one workflow where it saves the most time.',
    niche: 'AI tools and software reviews',
    audience: 'creators, founders, and small teams looking for practical AI tools',
    tone: 'clear, useful, specific, proof-driven, no hype',
    hookStyle: 'secret',
    desc: 'Search-friendly AI tool explainer. Open with a specific pain point, show the workflow, compare it to the old way, and end with who should try it.',
    pinned: 'What AI tool should I test next?',
    hashtags: '#shorts #aitools #software #productivity #facelessyoutube',
    visualPreset: 'cinematic-photoreal',
    visualStyle: 'premium software explainer visuals, modern desk setup, UI-inspired light, cinematic product review style, no text',
    ambience: 'clean dark studio, teal glow, focused productive mood',
    music: 'modern tech pulse, subtle synth, clean rhythm, no vocals',
    captionStyle: 'documentary',
  },
};

function applyLaunchDemo(key, {openCreate=true} = {}) {
  const demo = LAUNCH_DEMOS[key] || LAUNCH_DEMOS.reddit;
  $('makeRecipe').value = demo.recipe || 'custom';
  applyChannelRecipe(true);
  $('makeVideoMode').value = demo.videoMode || 'generated';
  $('makeDuration').value = demo.duration || '140';
  $('makeAspect').value = demo.aspect || '9:16';
  if (demo.tension) $('makeTensionFormat').value = demo.tension;
  if (demo.loop) $('makeLoopType').value = demo.loop;
  $('makePreferredTitle').value = demo.title || '';
  $('makeTopic').value = demo.topic || '';
  $('makeNiche').value = demo.niche || $('makeNiche').value;
  $('makeAudience').value = demo.audience || $('makeAudience').value;
  $('makeTone').value = demo.tone || $('makeTone').value;
  $('makeHookStyle').value = demo.hookStyle || $('makeHookStyle').value;
  $('makeDesc').value = demo.desc || $('makeDesc').value;
  $('makePinnedComment').value = demo.pinned || '';
  $('makeHashtags').value = demo.hashtags || '';
  $('makeMusicPrompt').value = demo.music || $('makeMusicPrompt').value;
  if (demo.captionStyle) $('makeCaptionStyle').value = demo.captionStyle;
  if (demo.visualPreset) {
    $('makeVisualPreset').value = demo.visualPreset;
    applyVisualPreset('make');
  }
  if (demo.visualStyle) $('makeVisualStyle').value = demo.visualStyle;
  if (demo.ambience) $('makeAmbience').value = demo.ambience;
  makeIdeaSelected = true;
  makeSelectedIdea = {
    title: demo.title,
    topic: demo.topic,
    hook: demo.topic,
    pinned_comment: demo.pinned,
    hashtags: (demo.hashtags || '').split(/\s+/).filter(Boolean),
    visual: demo.visualStyle || $('makeVisualStyle').value,
    music: demo.music,
    structure: {
      setup: 'Establish the person, object, or problem immediately.',
      escalation: 'Add one new detail that makes the situation harder to ignore.',
      twist: 'Reveal why the first line was more dangerous than it seemed.',
      loop_ending: 'End by pointing back to the opening line.',
    },
  };
  updateMakeVideoMode();
  updateMakeChoiceCards();
  updateViralStage();
  updateTitleIdeaGate();
  updateMakeDurationHint();
  updateMakeReadiness();
  $('makeTitlePackage').style.display = 'block';
  $('launchDemoStatus').textContent = `${demo.label} loaded. Review the Create Video page, upload footage if needed, then render.`;
  toast(`${demo.label} loaded`);
  if (openCreate) document.querySelector('.tab-btn[data-tab="make"]')?.click();
}

/* Client-side capability detector for the hosted readiness payload.
 * Server returns status: "check_client" + a client_check key; we run
 * the matching probe here and resolve the dot color before render so
 * users never see "checking…" lingering. */
function _evaluateClientCheck(check) {
  if (check.status !== 'check_client') return check;
  const probe = check.client_check;
  let ok = false;
  try {
    if (probe === 'webgpu') {
      ok = !!(navigator.gpu && typeof navigator.gpu.requestAdapter === 'function');
    } else if (probe === 'speech_synthesis') {
      ok = ('speechSynthesis' in window) && Array.isArray(window.speechSynthesis.getVoices?.());
    } else if (probe === 'ffmpeg_wasm') {
      ok = (typeof WebAssembly !== 'undefined') && (typeof WebAssembly.instantiate === 'function');
    }
  } catch (e) { ok = false; }
  return {
    ...check,
    status: ok ? 'ready' : (check.required ? 'missing' : 'optional'),
    detail: ok ? (check.ready_detail || check.detail) : (check.missing_detail || check.detail),
  };
}

function renderLaunchReadiness(data) {
  const isHosted = data.mode === 'hosted';
  // Resolve any client_check items before deciding score / readiness text,
  // so the server's "check_client" placeholder never reaches the DOM.
  const checks = (data.checks || []).map(_evaluateClientCheck);
  // Hosted score: % of required browser checks that resolved "ready".
  // Local score: server already computed it.
  let score;
  if (isHosted) {
    const required = checks.filter(c => c.required);
    const ready = required.filter(c => c.status === 'ready');
    score = required.length ? Math.round((ready.length / required.length) * 100) : 100;
  } else {
    score = data.score;
  }
  const launchReady = isHosted
    ? checks.filter(c => c.required).every(c => c.status === 'ready')
    : !!data.launch_ready;
  $('launchScore').textContent = String(score ?? '--');
  // Rename the label so the user reads the score correctly. On hosted
  // it's BROWSER readiness only, since the page can't detect optional
  // local installs (CORS blocks HTTPS to localhost). On local desktop
  // it's overall studio readiness.
  const labelEl = $('launchReadinessLabel');
  if (labelEl) labelEl.textContent = isHosted ? 'Browser readiness' : 'Launch readiness';
  // Make it explicit on hosted: the score is for BROWSER MODE only. Any
  // optional power-user upgrades (Ollama, Kokoro, Forge) are local
  // installs the hosted page genuinely cannot detect. Tell the user that
  // instead of letting the 100 read as "everything installed".
  if (isHosted) {
    const optionalInstalls = checks.filter(c => !c.required && (c.actions || []).some(a => (a.value || '').startsWith('/install/')));
    const upgradeNote = optionalInstalls.length
      ? ` ${optionalInstalls.length} optional upgrade${optionalInstalls.length === 1 ? '' : 's'} (Ollama, Kokoro voices, Forge) can be installed on your computer for higher quality.`
      : '';
    $('launchReadinessText').textContent = launchReady
      ? `Browser mode ready (${score}/100). Everything below runs in this browser, no install required.${upgradeNote}`
      : 'Your browser is missing one of the required engines. Try Chrome, Edge, or another modern Chromium.';
  } else {
    $('launchReadinessText').textContent = launchReady
      ? 'Core local studio is ready. Optional tools can be connected when needed.'
      : 'Finish the required setup items before charging through the full workflow.';
  }
  $('launchChecks').innerHTML = checks.map(c => {
    // 'desktop_only' renders like 'optional'. gray dot, action button shown.
    // The status label below ("desktop only") makes the gating obvious.
    const cls = c.status === 'ready' ? 'ready'
              : c.status === 'missing' ? 'missing'
              : c.status === 'desktop_only' ? 'optional desktop-only'
              : 'optional';
    // Render action buttons for any non-ready item that has actions.
    // The dataset attributes carry the action's kind/value back to the
    // delegated click handler below. keeps this template free of
    // inline JS so escapeHtml does its job and we don't open XSS.
    const actions = (c.actions || []).map((a, i) => {
      const primary = i === 0 ? ' primary' : '';
      const icon = a.kind === 'link' ? '↗' : a.kind === 'copy' ? '⧉' : a.kind === 'pull' ? '⤓' : '→';
      return `<button type="button" class="launch-action${primary}"
                data-kind="${escapeHtml(a.kind)}"
                data-value="${escapeHtml(a.value)}"
                data-check="${escapeHtml(c.id)}">
        <span class="launch-action-icon">${icon}</span>
        <span>${escapeHtml(a.label || '')}</span>
      </button>`;
    }).join('');
    const actionsBlock = actions ? `<div class="launch-check-actions">${actions}</div>` : '';
    return `
      <div class="launch-check ${cls}" data-check-id="${escapeHtml(c.id)}">
        <span class="launch-dot"></span>
        <div>
          <strong>${escapeHtml(c.label || '')}${c.required ? ' *' : ''}</strong>
          <div class="hint">${escapeHtml(c.detail || '')}</div>
          ${actionsBlock}
        </div>
        <span class="launch-status">${escapeHtml(c.status || '')}</span>
      </div>
    `;
  }).join('');
}

/* Delegated click handler for the per-check action buttons. Lives outside
 * renderLaunchReadiness so we attach it once at startup, not on every
 * re-render (avoids dupe handlers leaking event listeners). */
async function handleLaunchActionClick(ev) {
  const btn = ev.target.closest('.launch-action');
  if (!btn) return;
  const kind = btn.dataset.kind;
  const value = btn.dataset.value;
  const checkId = btn.dataset.check;
  if (!kind || !value) return;

  if (kind === 'link') {
    window.open(value, '_blank', 'noopener');
    return;
  }
  if (kind === 'copy') {
    try {
      await navigator.clipboard.writeText(value);
      toast(`Copied: ${value.length > 40 ? value.slice(0, 37) + '...' : value}`);
    } catch (e) {
      toast('Copy failed. Copy manually: ' + value, true);
    }
    return;
  }
  if (kind === 'internal') {
    // Two patterns supported: "tab:<id>" jumps to a top-level tab,
    // "demo:<id>" loads a demo workflow.
    if (value.startsWith('tab:')) {
      const target = value.slice(4);
      document.querySelector(`.tab-btn[data-tab="${target}"]`)?.click();
    } else if (value.startsWith('demo:')) {
      const id = value.slice(5);
      if (typeof applyLaunchDemo === 'function') applyLaunchDemo(id);
    }
    return;
  }
  if (kind === 'pull') {
    // Server-side install (e.g. ollama pull). Disable button, show a
    // pending state, then re-run readiness on completion so the dot
    // flips green automatically.
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="launch-action-icon">…</span><span>Installing</span>';
    try {
      const r = await fetch('/api/launch/install', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: value}),
      });
      const d = await r.json();
      if (d.ok) {
        toast(`${checkId} installed. Re-checking readiness.`);
        await runLaunchReadinessCheck();
      } else {
        toast(d.error || 'Install failed', true);
        btn.disabled = false;
        btn.innerHTML = original;
      }
    } catch (e) {
      toast('Install request failed: ' + (e.message || e), true);
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }
}
document.addEventListener('click', handleLaunchActionClick);

async function runLaunchReadinessCheck() {
  const btn = $('launchCheckBtn');
  if (btn) btn.disabled = true;
  $('launchReadinessText').textContent = 'Checking Ollama, voices, Forge, YouTube, analytics, and saved projects...';
  try {
    // Forward ?mode=hosted/local from the page URL so dev can preview the
    // hosted checklist from localhost without spoofing the host header.
    const pageMode = new URLSearchParams(window.location.search).get('mode');
    const url = pageMode ? `/api/launch/readiness?mode=${encodeURIComponent(pageMode)}` : '/api/launch/readiness';
    const d = await apiJson(url);
    renderLaunchReadiness(d);
  } catch (e) {
    $('launchReadinessText').textContent = e.message || 'Readiness check failed.';
    toast(e.message || 'Readiness check failed', true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function runLaunchTestRender() {
  const btn = $('launchTestRenderBtn');
  const status = $('launchTestStatus');
  const result = $('launchTestResult');
  if (btn) btn.disabled = true;
  if (status) status.textContent = 'Rendering a tiny local MP4 test...';
  if (result) {
    result.style.display = 'none';
    result.innerHTML = '';
  }
  try {
    const d = await apiJson('/api/launch/test-render', {method:'POST'});
    const project = d.project || {};
    latestMakeVideoProjectId = project.id || latestMakeVideoProjectId;
    if (status) status.textContent = 'Test render worked. Encoding and playback are ready.';
    if (result && d.video_url) {
      result.style.display = 'block';
      result.innerHTML = `<video src="${escapeHtml(d.video_url)}" controls></video>`;
    }
    loadLibrary();
    if (typeof loadPublishWorkspace === 'function') loadPublishWorkspace();
    addNotification({type:'info', title:'Test render complete', body:'Phantomline successfully created a local MP4.'});
  } catch (e) {
    if (status) status.textContent = e.message || 'Test render failed.';
    toast(e.message || 'Test render failed', true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

$('launchCheckBtn')?.addEventListener('click', runLaunchReadinessCheck);
$('launchDemoBtn')?.addEventListener('click', () => applyLaunchDemo('reddit'));
$('launchStartBtn')?.addEventListener('click', () => document.querySelector('.tab-btn[data-tab="make"]')?.click());
$('launchTestRenderBtn')?.addEventListener('click', runLaunchTestRender);
$('launchOpenSettingsBtn')?.addEventListener('click', () => document.querySelector('.tab-btn[data-tab="settings"]')?.click());
$('launchOpenConnectionsBtn')?.addEventListener('click', () => {
  document.querySelector('.tab-btn[data-tab="publish"]')?.click();
  if (typeof switchPublishView === 'function') switchPublishView('connections');
});
$('launchUseSourceModeBtn')?.addEventListener('click', () => {
  document.querySelector('.tab-btn[data-tab="make"]')?.click();
  $('makeVideoMode').value = 'source';
  updateMakeVideoMode();
  updateMakeChoiceCards();
  updateMakeReadiness();
});
$('makeLoadDemoBtn')?.addEventListener('click', () => applyLaunchDemo('reddit', {openCreate:false}));
document.querySelectorAll('.demo-tile').forEach(tile => {
  tile.addEventListener('click', () => applyLaunchDemo(tile.dataset.demo || 'reddit'));
});
document.querySelector('.tab-btn[data-tab="launch"]')?.addEventListener('click', runLaunchReadinessCheck);
runLaunchReadinessCheck();

applyVisualPreset('video');
applyVisualPreset('make');
applyChannelRecipe(false);
updateMakeChoiceCards();
updateViralStage();
updateTitleIdeaGate();
updateMakeReadiness();

// ===== Channel Insights panel =====
async function loadInsightsPanel() {
  try {
    const r = await fetch('/api/insights');
    const d = await r.json();
    const body = $('insightsBody');
    const status = $('insightsStatus');
    if (!d.ok || !d.configured) {
      status.textContent = 'not configured';
      body.className = 'ip-empty';
      body.innerHTML = "Upload your YouTube Studio analytics to make every idea, title, and script align with what's already working for your channel.";
      return;
    }
    const ins = d.insights || {};
    const updated = d.updated_at ? relTime(d.updated_at) : '';
    status.textContent = 'connected' + (updated ? ' · ' + updated : '');
    const assets = (ins.seo_assets || []).slice(0, 3);
    const gap = (ins.gap_keywords || []).slice(0, 4);
    const winning = (ins.winning_titles || []).slice(0, 3);
    const trafficShares = (ins.traffic_sources || {}).shares || {};

    let html = '';

    if (Object.keys(trafficShares).length) {
      const order = ['search', 'browse', 'suggested', 'external', 'shorts_feed', 'channel', 'playlist', 'other'];
      const tiles = order
        .filter(k => trafficShares[k])
        .map(k => `<span class="idea-badge">${escapeHtml(k.replace('_',' '))} ${trafficShares[k]}%</span>`)
        .join(' ');
      html += `<div class="ip-section"><div class="ip-section-label">Traffic mix</div><div>${tiles}</div></div>`;
    }

    if (assets.length) {
      html += `<div class="ip-section"><div class="ip-section-label">SEO assets</div><div class="ip-list">` +
        assets.map(a => `<div class="ip-asset">${escapeHtml(a.title || '')}` +
          (a.views ? `<span class="ip-meta">${(a.views).toLocaleString()} views${a.search_share_pct ? ` · ${Math.round(a.search_share_pct)}% search` : ''}</span>` : '') +
          `</div>`).join('') +
        `</div></div>`;
    } else if (winning.length) {
      html += `<div class="ip-section"><div class="ip-section-label">Best titles so far</div><div class="ip-list">` +
        winning.map(t => `<div class="ip-asset">${escapeHtml(t)}</div>`).join('') +
        `</div></div>`;
    }

    if (gap.length) {
      html += `<div class="ip-section"><div class="ip-section-label">Untapped search demand · click to use</div><div class="ip-list">` +
        gap.map(g => `<div class="ip-gap" data-gap="${escapeHtml(g)}">${escapeHtml(g)}</div>`).join('') +
        `</div></div>`;
    }

    if (!html) {
      body.className = 'ip-empty';
      body.innerHTML = "Insights are loaded but no SEO assets, search terms, or gaps surfaced yet. Try a richer analytics export.";
      return;
    }
    body.className = '';
    body.innerHTML = html;
    body.querySelectorAll('.ip-gap').forEach(el => {
      el.addEventListener('click', () => {
        const topic = $('makeTopic');
        if (topic) {
          topic.value = el.dataset.gap;
          topic.focus();
          toast('Topic set to gap keyword');
        }
      });
    });
  } catch (e) { /* ignore. panel is non-blocking */ }
}

async function uploadInsightsCsv(endpoint, file) {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch(endpoint, { method: 'POST', body: fd });
  const d = await r.json();
  if (!d.ok) throw new Error(d.error || 'upload failed');
  return d;
}

$('insightsImportAnalyticsBtn').addEventListener('click', () => $('insightsAnalyticsFile').click());
$('insightsImportTrafficBtn').addEventListener('click', () => $('insightsTrafficFile').click());
$('insightsImportSearchBtn').addEventListener('click', () => $('insightsSearchFile').click());

$('insightsAnalyticsFile').addEventListener('change', async (e) => {
  const f = e.target.files[0]; if (!f) return;
  try {
    toast('Analyzing analytics…');
    const fd = new FormData(); fd.append('file', f);
    const r = await fetch('/api/publish/analytics/analyze', { method: 'POST', body: fd });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'analytics analyze failed');
    toast('Analytics imported');
    loadInsightsPanel();
  } catch (err) { toast(err.message || 'Import failed', true); }
  finally { e.target.value = ''; }
});

$('insightsTrafficFile').addEventListener('change', async (e) => {
  const f = e.target.files[0]; if (!f) return;
  try {
    await uploadInsightsCsv('/api/insights/import-traffic', f);
    toast('Traffic sources imported');
    loadInsightsPanel();
  } catch (err) { toast(err.message || 'Traffic import failed', true); }
  finally { e.target.value = ''; }
});

$('insightsSearchFile').addEventListener('change', async (e) => {
  const f = e.target.files[0]; if (!f) return;
  try {
    await uploadInsightsCsv('/api/insights/import-search', f);
    toast('Search terms imported');
    loadInsightsPanel();
  } catch (err) { toast(err.message || 'Search-terms import failed', true); }
  finally { e.target.value = ''; }
});

$('insightsClearBtn').addEventListener('click', async () => {
  if (!confirm('Clear all stored channel insights? Idea/title generation will fall back to generic prompts.')) return;
  await fetch('/api/insights/clear', { method: 'POST' });
  loadInsightsPanel();
  toast('Cleared');
});

loadInsightsPanel();
// Re-fetch when the user comes back to Make Video so they always see fresh state.
document.querySelector('.tab-btn[data-tab="make"]').addEventListener('click', loadInsightsPanel);

// ===== Optimize Library =====
let optimizeVideos = [];
let optimizeFilter = 'all';
let optimizeSearchTerm = '';
let optimizeSelectedId = null;

document.querySelectorAll('[data-go-tab]').forEach(b => {
  b.addEventListener('click', () => {
    const t = b.dataset.goTab;
    const btn = document.querySelector(`.tab-btn[data-tab="${t}"]`);
    if (btn) btn.click();
  });
});

async function checkOptimizeConnection() {
  try {
    const r = await fetch('/api/publish/status');
    const d = await r.json();
    const connected = !!(d.ok && d.youtube_connected);
    $('optimizeConnect').style.display = connected ? 'none' : '';
    $('optimizeWorkspace').style.display = connected ? 'block' : 'none';
    if (connected) {
      const ch = d.youtube_channel || {};
      $('optimizeChannelLabel').textContent = ch.title || ch.id || 'Connected';
    }
    return connected;
  } catch {
    return false;
  }
}

async function loadOptimizeVideos() {
  const list = $('optimizeList');
  $('optimizeRefreshBtn').disabled = true;
  list.innerHTML = '<div class="ip-empty" style="padding:30px 12px;">Pulling channel videos...</div>';
  try {
    const r = await fetch('/api/optimize/videos?limit=200');
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'failed to fetch videos');
    optimizeVideos = d.videos || [];
    $('optimizeCount').textContent = `${d.count} videos`;
    renderOptimizeList();
  } catch (e) {
    list.innerHTML = `<div class="ip-empty" style="padding:30px 12px; color:var(--bad);">${escapeHtml(e.message)}</div>`;
  } finally {
    $('optimizeRefreshBtn').disabled = false;
  }
}

function renderOptimizeList() {
  const list = $('optimizeList');
  const term = (optimizeSearchTerm || '').toLowerCase();
  let filtered = optimizeVideos;
  if (optimizeFilter !== 'all') filtered = filtered.filter(v => v.tier === optimizeFilter);
  if (term) filtered = filtered.filter(v => (v.title || '').toLowerCase().includes(term));

  if (!filtered.length) {
    list.innerHTML = '<div class="ip-empty" style="padding:30px 12px;">No videos match.</div>';
    return;
  }
  list.innerHTML = filtered.map(v => {
    const views = (v.views || 0).toLocaleString();
    const date = v.publishedAt ? new Date(v.publishedAt).toLocaleDateString() : '';
    return `
      <div class="vid-card${v.id === optimizeSelectedId ? ' selected' : ''}" data-id="${escapeHtml(v.id)}">
        <div class="thumb" style="background-image:url('${escapeHtml(v.thumbnail || '')}')"></div>
        <div class="meta">
          <div class="vt">${escapeHtml(v.title || 'Untitled')}</div>
          <div class="vstats">${views} views · ${escapeHtml(date)}</div>
          <div class="vbadges">
            <span class="tier-badge tier-${v.tier || 'mid'}">${v.tier || 'mid'}</span>
          </div>
        </div>
      </div>`;
  }).join('');
  list.querySelectorAll('.vid-card').forEach(card => {
    card.addEventListener('click', () => {
      optimizeSelectedId = card.dataset.id;
      list.querySelectorAll('.vid-card').forEach(c => c.classList.toggle('selected', c.dataset.id === optimizeSelectedId));
      const v = optimizeVideos.find(x => x.id === optimizeSelectedId);
      if (v) renderOptimizeDetail(v);
    });
  });
}

function renderOptimizeDetail(video, analysis = null) {
  const detail = $('optimizeDetail');
  const tags = video.tags || [];
  const date = video.publishedAt ? new Date(video.publishedAt).toLocaleDateString() : '';
  const hasAnalysis = !!analysis;
  const ana = hasAnalysis ? (analysis.analysis || {}) : null;
  const sug = hasAnalysis ? (ana.suggestions || {}) : null;

  let html = `
    <div class="opt-current">
      <div class="opt-thumb" style="background-image:url('${escapeHtml(video.thumbnail || '')}')"></div>
      <div>
        <h3>${escapeHtml(video.title || 'Untitled')}</h3>
        <div class="opt-stats">
          ${(video.views || 0).toLocaleString()} views · ${(video.likes || 0).toLocaleString()} likes · ${(video.comments || 0).toLocaleString()} comments · ${escapeHtml(date)}
        </div>
        <div class="opt-stats">
          ${escapeHtml(video.privacyStatus || '')}${video.duration ? ' · ' + escapeHtml(video.duration) : ''}
        </div>
        <div class="opt-tags">
          ${tags.length ? tags.slice(0, 12).map(t => `<span>${escapeHtml(t)}</span>`).join('') : '<span style="opacity:0.6;">no tags</span>'}
        </div>
      </div>
    </div>

    <div style="display:flex; gap:8px; flex-wrap:wrap;">
      <button id="optAnalyzeBtn" class="btn" type="button" style="width:auto; margin-top:0;" ${hasAnalysis ? 'disabled' : ''}>${hasAnalysis ? 'Analyzed' : 'Deep Analyze with Ollama'}</button>
      ${hasAnalysis ? '<button id="optReanalyzeBtn" class="btn secondary" type="button" style="width:auto; margin-top:0;">Re-analyze</button>' : ''}
      <a class="btn secondary" type="button" style="width:auto; margin-top:0; text-decoration:none;" href="https://youtube.com/watch?v=${encodeURIComponent(video.id)}" target="_blank" rel="noopener">Open on YouTube ↗</a>
    </div>
  `;

  if (hasAnalysis && ana) {
    const verdict = ana.verdict || 'needs_repackaging';
    const verdictLabel = verdict.replace(/_/g, ' ');
    html += `
      <div class="opt-section">
        <div class="opt-label">Verdict</div>
        <span class="opt-verdict-pill opt-verdict-${escapeHtml(verdict)}">${escapeHtml(verdictLabel)}</span>
        ${typeof ana.fit_score === 'number' ? `<span style="margin-left:10px; color:var(--muted); font-size:12px;">Confidence ${ana.fit_score}/100</span>` : ''}
      </div>
      <div class="opt-section">
        <div class="opt-label">Diagnosis</div>
        <div class="opt-diagnosis">${escapeHtml(ana.diagnosis || '(no diagnosis returned)')}</div>
      </div>
    `;

    if (verdict === 'healthy' && ana.do_nothing_reason) {
      html += `
        <div class="opt-section">
          <div class="opt-label">Why we should leave this alone</div>
          <div class="opt-diagnosis" style="background:rgba(34, 231, 245,0.05); border-color:rgba(34, 231, 245,0.2);">
            ${escapeHtml(ana.do_nothing_reason)}
          </div>
        </div>
      `;
    } else if (sug) {
      const newTitle = (sug.title || {}).new || '';
      const titleWhy = (sug.title || {}).why || '';
      const newDesc = (sug.description || {}).new || '';
      const descWhy = (sug.description || {}).why || '';
      const tagAdd = ((sug.tags || {}).add || []);
      const tagRemove = ((sug.tags || {}).remove || []);
      const tagWhy = (sug.tags || {}).why || '';
      const titleFit = analysis.title_fit;

      html += `
        <div class="opt-section">
          <div class="opt-label">Title. proposed</div>
          <div class="opt-diff">
            <div class="col">
              <div class="col-label">Current</div>
              <div class="col-text">${escapeHtml(video.title || '')}</div>
            </div>
            <div class="col new">
              <div class="col-label">Proposed${titleFit && titleFit.verdict ? ` · <span class="fit-badge fit-${escapeHtml(titleFit.verdict)}">${escapeHtml(titleFit.verdict.replace('_',' '))} ${titleFit.score || ''}</span>` : ''}</div>
              <div class="col-text">${escapeHtml(newTitle || '(no proposal)')}</div>
              ${titleWhy ? `<div class="opt-why">${escapeHtml(titleWhy)}</div>` : ''}
            </div>
          </div>
        </div>

        <div class="opt-section">
          <div class="opt-label">Description. proposed</div>
          <div class="opt-diff">
            <div class="col">
              <div class="col-label">Current</div>
              <div class="col-text">${escapeHtml((video.description || '').slice(0, 800))}${(video.description || '').length > 800 ? '…' : ''}</div>
            </div>
            <div class="col new">
              <div class="col-label">Proposed</div>
              <div class="col-text">${escapeHtml(newDesc || '(no proposal)')}</div>
              ${descWhy ? `<div class="opt-why">${escapeHtml(descWhy)}</div>` : ''}
            </div>
          </div>
        </div>

        <div class="opt-section">
          <div class="opt-label">Tag changes</div>
          <div class="opt-tag-changes">
            ${tagAdd.map(t => `<span class="opt-tag-add">+ ${escapeHtml(t)}</span>`).join('')}
            ${tagRemove.map(t => `<span class="opt-tag-remove">${escapeHtml(t)}</span>`).join('')}
            ${(!tagAdd.length && !tagRemove.length) ? '<span style="color:var(--muted); font-size:12px;">no changes proposed</span>' : ''}
          </div>
          ${tagWhy ? `<div class="opt-why" style="margin-top:8px;">${escapeHtml(tagWhy)}</div>` : ''}
        </div>
      `;
    }

    if ((ana.ranking_keywords || []).length || (ana.missed_keywords || []).length) {
      html += `<div class="opt-section">
        <div class="opt-label">Keywords</div>
        ${(ana.ranking_keywords || []).length ? `<div style="font-size:12.5px; color:var(--muted); margin-bottom:4px;">Ranking: ${(ana.ranking_keywords || []).map(k => `<span class="idea-badge">${escapeHtml(k)}</span>`).join(' ')}</div>` : ''}
        ${(ana.missed_keywords || []).length ? `<div style="font-size:12.5px; color:var(--muted);">Missed: ${(ana.missed_keywords || []).map(k => `<span class="idea-badge" style="background:rgba(243,201,105,0.10); color:#f3c969;">${escapeHtml(k)}</span>`).join(' ')}</div>` : ''}
      </div>`;
    }

    if ((ana.risks || []).length) {
      html += `<div class="opt-section">
        <div class="opt-label">Risks before applying</div>
        <ul class="opt-risks">${(ana.risks || []).map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>
      </div>`;
    }

    html += `<div class="opt-section" style="color:var(--muted); font-size:11.5px; padding-top:10px; border-top:1px solid var(--border);">
      Phase 1: this is a suggestion only. Nothing has been written back to YouTube. Apply support is coming next.
    </div>`;
  }

  detail.innerHTML = html;
  const analyzeBtn = document.getElementById('optAnalyzeBtn');
  if (analyzeBtn && !hasAnalysis) {
    analyzeBtn.addEventListener('click', () => analyzeOptimizeVideo(video));
  }
  const reanalyzeBtn = document.getElementById('optReanalyzeBtn');
  if (reanalyzeBtn) reanalyzeBtn.addEventListener('click', () => analyzeOptimizeVideo(video));
}

async function analyzeOptimizeVideo(video) {
  const btn = document.getElementById('optAnalyzeBtn') || document.getElementById('optReanalyzeBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Analyzing...'; }
  try {
    // Always pull fresh detail so the model sees current title/desc/tags.
    const dr = await fetch('/api/optimize/video/' + encodeURIComponent(video.id));
    const dd = await dr.json();
    if (!dd.ok) throw new Error(dd.error || 'failed to refresh video');
    const fresh = dd.video;
    const r = await fetch('/api/optimize/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video: fresh })
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'analyze failed');
    renderOptimizeDetail(fresh, d);
    toast('Analysis ready');
  } catch (e) {
    toast(e.message || 'Analyze failed', true);
    if (btn) { btn.disabled = false; btn.textContent = 'Deep Analyze with Ollama'; }
  }
}

$('optimizeRefreshBtn').addEventListener('click', loadOptimizeVideos);
document.querySelectorAll('.optimize-filter-row .filter-btn').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.optimize-filter-row .filter-btn').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    optimizeFilter = b.dataset.vfilter;
    renderOptimizeList();
  });
});
$('optimizeSearch').addEventListener('input', (e) => {
  optimizeSearchTerm = e.target.value;
  renderOptimizeList();
});

document.querySelector('.tab-btn[data-tab="optimize"]').addEventListener('click', async () => {
  const connected = await checkOptimizeConnection();
  if (connected && !optimizeVideos.length) loadOptimizeVideos();
});

$('videoPlanBtn').addEventListener('click', async () => {
  const script = $('videoScript').value.trim();
  if (!script) { toast('Paste a script first', true); return; }
  const body = {
    script,
    title: $('videoTitle').value.trim(),
    workflow: $('videoWorkflow').value,
    aspect: $('videoAspect').value,
    scene_seconds: parseInt($('videoSceneSeconds').value, 10) || 8,
    ...visualSettings('video'),
  };
  $('videoPlanBtn').disabled = true;
  try {
    const r = await fetch('/api/video/plan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'failed to create plan');
    renderVideoPlan(d.plan, d.project);
    await loadVideoAssets();
    if (d.project) $('timelinePlan').value = d.project.id;
    loadRecentWork();
    toast('Video plan ready');
  } catch (e) {
    toast(e.message || 'Video plan failed', true);
  } finally {
    $('videoPlanBtn').disabled = false;
  }
});

$('videoCopyBtn').addEventListener('click', async () => {
  if (!videoPlan) { toast('Create a video plan first', true); return; }
  const text = promptsText(videoPlan);
  try { await navigator.clipboard.writeText(text); toast('Copied prompts'); }
  catch { toast('Copy failed', true); }
});

$('videoDownloadPlanBtn').addEventListener('click', () => {
  if (!videoProject) return;
  window.location = `/api/projects/${videoProject.id}/file/scene_plan?download=1`;
});

$('videoDownloadPromptsBtn').addEventListener('click', () => {
  if (!videoProject) return;
  window.location = `/api/projects/${videoProject.id}/file/prompts?download=1`;
});

async function loadVideoAssets() {
  try {
    const r = await fetch('/api/video/assets');
    const d = await r.json();
    if (!d.ok) return;
    const planSel = $('timelinePlan');
    const narrSel = $('timelineNarration');
    const prevPlan = planSel.value;
    const prevNarr = narrSel.value;
    planSel.innerHTML = '<option value="">- choose video plan -</option>';
    for (const p of d.plans || []) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = `${p.title || 'Untitled'} (${(p.params || {}).scene_count || '?'} scenes)`;
      planSel.appendChild(opt);
    }
    narrSel.innerHTML = '<option value="">- choose narration -</option>';
    for (const p of d.narrations || []) {
      const opt = document.createElement('option');
      opt.value = p.id;
      const dur = p.duration_seconds ? ` · ${Math.round(p.duration_seconds)}s` : '';
      opt.textContent = `${p.title || 'narration'}${dur}`;
      narrSel.appendChild(opt);
    }
    if (prevPlan) planSel.value = prevPlan;
    if (prevNarr) narrSel.value = prevNarr;
    if (!planSel.value && (d.plans || []).length) planSel.value = d.plans[0].id;
    if (!narrSel.value && (d.narrations || []).length) narrSel.value = d.narrations[0].id;
  } catch (e) { /* ignore */ }
}

function renderTimeline(tl, project) {
  timeline = tl;
  timelineProject = project || null;
  $('timelinePanel').style.display = 'block';
  $('timelineScenes').textContent = (tl.scene_count || 0).toLocaleString();
  $('timelineDuration').textContent = tl.audio_duration || '-';
  $('timelinePathHint').textContent = project ? 'saved to project library' : '';
  $('timelineDownloadJsonBtn').disabled = !(project && project.files && project.files.timeline);
  $('timelineDownloadTxtBtn').disabled = !(project && project.files && project.files.edit_list);
  const scenes = tl.scenes || [];
  const shown = scenes.slice(0, 80);
  $('timelineScenesGrid').innerHTML = shown.map(scene => `
    <div class="scene-card">
      <div class="scene-head">
        <span>${escapeHtml(scene.clip_file)}</span>
        <span>${escapeHtml(scene.start)} - ${escapeHtml(scene.end)}</span>
      </div>
      <p>${escapeHtml(scene.narration)}</p>
      <div class="prompt-box">${escapeHtml(scene.edit_note)}</div>
    </div>
  `).join('') + (scenes.length > shown.length ? `
    <div class="empty-state" style="grid-column:1/-1;">
      <h3>${(scenes.length - shown.length).toLocaleString()} more scenes in the exported timeline</h3>
      <p>The page previews the first 80 timing cards.</p>
    </div>` : '');
}

$('refreshTimelineAssetsBtn').addEventListener('click', loadVideoAssets);

$('timelineBtn').addEventListener('click', async () => {
  const plan_project_id = $('timelinePlan').value;
  const narration_project_id = $('timelineNarration').value;
  if (!plan_project_id || !narration_project_id) {
    toast('Choose a video plan and narration first', true);
    return;
  }
  $('timelineBtn').disabled = true;
  try {
    const r = await fetch('/api/video/timeline', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ plan_project_id, narration_project_id }),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'failed to build timeline');
    renderTimeline(d.timeline, d.project);
    loadRecentWork();
    toast('Aligned timeline ready');
  } catch (e) {
    toast(e.message || 'Timeline failed', true);
  } finally {
    $('timelineBtn').disabled = false;
  }
});

$('timelineCopyBtn').addEventListener('click', async () => {
  if (!timeline) { toast('Build a timeline first', true); return; }
  try { await navigator.clipboard.writeText(timelineText(timeline)); toast('Copied edit list'); }
  catch { toast('Copy failed', true); }
});

$('timelineDownloadJsonBtn').addEventListener('click', () => {
  if (!timelineProject) return;
  window.location = `/api/projects/${timelineProject.id}/file/timeline?download=1`;
});

$('timelineDownloadTxtBtn').addEventListener('click', () => {
  if (!timelineProject) return;
  window.location = `/api/projects/${timelineProject.id}/file/edit_list?download=1`;
});

async function renderDraftVideoFromCurrentTimeline() {
  if (!timelineProject) {
    toast('Build a timeline first', true);
    return;
  }
  $('draftVideoBtn').disabled = true;
  $('draftVideoDownloadBtn').disabled = true;
  $('draftVideoStatus').textContent = 'Starting draft MP4 render...';
  try {
    const r = await fetch('/api/video/draft/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        timeline_project_id: timelineProject.id,
        image_provider: $('videoImageProvider').value,
        image_quality: $('videoImageQuality').value,
        forge_url: $('videoForgeUrl').value.trim(),
        forge_checkpoint: $('videoForgeCheckpoint').value.trim(),
      }),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'failed to start draft render');
    draftVideoJob = d.job_id;
    if (draftVideoTimer) clearInterval(draftVideoTimer);
    draftVideoTimer = setInterval(pollDraftVideo, 1500);
    pollDraftVideo();
    return draftVideoJob;
  } catch (e) {
    $('draftVideoBtn').disabled = false;
    $('draftVideoStatus').textContent = e.message || 'Draft render failed';
    toast(e.message || 'Draft render failed', true);
  }
}

async function waitForDraftVideo(jobId, onStatus) {
  for (;;) {
    if (typeof _makeRenderAbortIfNeeded === 'function') _makeRenderAbortIfNeeded();
    const d = await apiJson('/api/video/draft/status/' + jobId);
    const j = d.job;
    if (onStatus) onStatus(j.status || 'working');
    if (j.error) throw new Error(j.error);
    if (j.done) {
      addNotification({
        id: `video-finished:${jobId}`,
        type: 'video',
        title: 'Video finished',
        body: `${j.title || 'Your Phantomline video'} is ready to preview, download, or schedule.`,
      });
      return j.project_id;
    }
    await new Promise(resolve => setTimeout(resolve, 1500));
  }
}

async function pollDraftVideo() {
  if (!draftVideoJob) return;
  try {
    const r = await fetch('/api/video/draft/status/' + draftVideoJob);
    const d = await r.json();
    if (!d.ok) return;
    const j = d.job;
    $('draftVideoStatus').textContent = j.status || 'working';
    if (j.error) toast('Video render error: ' + j.error, true);
    if (j.done) {
      clearInterval(draftVideoTimer); draftVideoTimer = null;
      $('draftVideoBtn').disabled = false;
      if (!j.error) {
        $('draftVideoStatus').textContent = 'Draft MP4 ready. Motion clips and stills were used where available, with fallback cards only for missing scenes.';
        $('draftVideoDownloadBtn').disabled = false;
        addNotification({
          id: `video-finished:${draftVideoJob}`,
          type: 'video',
          title: 'Video finished',
          body: `${j.title || 'Your draft MP4'} is ready in Video Studio.`,
        });
        loadRecentWork();
      }
    }
  } catch (e) { /* keep polling */ }
}

$('draftVideoBtn').addEventListener('click', renderDraftVideoFromCurrentTimeline);

$('draftVideoDownloadBtn').addEventListener('click', () => {
  if (!draftVideoJob) return;
  window.location = '/api/video/draft/download/' + draftVideoJob;
});

let makeVideoJob = null;
let makeSourceVideoProjectId = null;
var makeSourceLibraryPick = null;
var makeSourceLibraryMeta = null;
let latestMakeVideoProjectId = null;

async function uploadMakeSourceVideo() {
  if (makeSourceLibraryPick && makeSourceVideoProjectId && makeSourceLibraryMeta) {
    return makeSourceLibraryMeta;
  }
  const file = $('makeSourceVideoFile').files && $('makeSourceVideoFile').files[0];
  if (!file) throw new Error('Choose a source video first');
  $('makeSourceVideoStatus').textContent = `Uploading ${file.name}...`;
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch('/api/upload/source-video', { method: 'POST', body: fd });
  const d = await r.json();
  if (!d.ok) throw new Error(d.error || 'video upload failed');
  makeSourceVideoProjectId = d.project_id;
  const meta = d.duration_seconds
    ? `, ${Math.round(d.duration_seconds)}s${d.width && d.height ? `, ${d.width}x${d.height}` : ''}`
    : '';
  $('makeSourceVideoStatus').innerHTML =
    `<strong style="color:var(--text);">Using uploaded:</strong> ${escapeHtml(d.original_name)} (${d.size_mb} MB${meta})`;
  loadLibrary();
  return d;
}

function updateMakeVideoMode() {
  const mode = $('makeVideoMode').value;
  const sourceMode = mode === 'source';
  const libraryMode = mode === 'library';
  const usesVideoSource = sourceMode || libraryMode;
  $('makeSourceVideoPanel').style.display = usesVideoSource ? '' : 'none';
  const uploadRow = $('makeSourceUploadRow');
  const libraryRow = $('makeSourceLibraryRow');
  if (uploadRow) uploadRow.hidden = !sourceMode;
  if (libraryRow) libraryRow.hidden = !libraryMode;
  if (libraryMode && makeSourceLibraryPick === null && $('makeSourceVideoFile')?.files?.[0]) {
    $('makeSourceVideoFile').value = '';
  }
  if (sourceMode && makeSourceLibraryPick) clearLibrarySourcePick();
  $('makeOpenAdvancedBtn').textContent = usesVideoSource ? 'Open Library' : 'Edit scenes';
  const visualStep = $('makeStepScenes')?.querySelector('span');
  if (visualStep) {
    visualStep.textContent = libraryMode
      ? 'Pick a Phantomline clip as your visual layer.'
      : sourceMode
        ? 'Upload and prep your chosen footage.'
        : 'Generate scene art locally.';
  }
  updateMakeChoiceCards();
  updateMakeReadiness();
}

// Track per-step start times so we can show wall-clock elapsed during long
// renders. Without this, users stare at a frozen-looking UI for minutes.
const _makeStepStarts = {};
let _makeStepTicker = null;

function _formatElapsed(ms) {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m ${r}s`;
}

function _tickRunningSteps() {
  document.querySelectorAll('.workflow-step.running').forEach(el => {
    const start = _makeStepStarts[el.id];
    const stateEl = el.querySelector('.state');
    if (!start || !stateEl) return;
    const baseLabel = el.dataset.runningLabel || stateEl.textContent.replace(/\s*·\s*\d+(s| ?m \d+s)$/, '');
    el.dataset.runningLabel = baseLabel;
    stateEl.textContent = `${baseLabel} · ${_formatElapsed(Date.now() - start)}`;
  });
}

function setMakeStep(id, state, label) {
  const el = $(id);
  if (!el) return;
  const wasRunning = el.classList.contains('running');
  el.classList.remove('running', 'done', 'error');
  if (state) el.classList.add(state);
  const stateEl = el.querySelector('.state');
  // Reveal the Production line panel the first time any step transitions
  // from waiting → running. Until then it's empty noise.
  if (state === 'running' || state === 'done' || state === 'error') {
    const header = $('makeProductionLineHeader');
    const panel = $('makeProductionLine');
    if (header) header.style.display = '';
    if (panel) panel.style.display = '';
  }
  if (state === 'running') {
    if (!_makeStepStarts[id]) _makeStepStarts[id] = Date.now();
    el.dataset.runningLabel = label || 'running';
    if (stateEl) stateEl.textContent = `${label || 'running'} · 0s`;
  } else if (state === 'done' || state === 'error') {
    const start = _makeStepStarts[id];
    const elapsed = start ? ` · ${_formatElapsed(Date.now() - start)}` : '';
    delete _makeStepStarts[id];
    if (stateEl) stateEl.textContent = (label || state) + elapsed;
  } else {
    delete _makeStepStarts[id];
    if (stateEl) stateEl.textContent = label || 'waiting';
  }
  // Ensure the ticker is running while any step is running.
  if (document.querySelector('.workflow-step.running')) {
    if (!_makeStepTicker) _makeStepTicker = setInterval(_tickRunningSteps, 1000);
  } else if (_makeStepTicker) {
    clearInterval(_makeStepTicker);
    _makeStepTicker = null;
  }
}

function resetMakeSteps() {
  for (const k of Object.keys(_makeStepStarts)) delete _makeStepStarts[k];
  if (_makeStepTicker) { clearInterval(_makeStepTicker); _makeStepTicker = null; }
  for (const id of ['makeStepScript', 'makeStepNarration', 'makeStepScenes', 'makeStepMusic', 'makeStepMix', 'makeStepTimeline', 'makeStepVideo']) {
    setMakeStep(id, '', 'waiting');
  }
  $('makeResult').classList.remove('shown');
  $('makePreviewVideo').removeAttribute('src');
  const phoneVideo = $('makePhonePreviewVideo');
  if (phoneVideo) {
    phoneVideo.removeAttribute('src');
    phoneVideo.style.display = 'none';
  }
  $('makePhonePreviewInner')?.classList.remove('has-video');
}

async function waitForStoryJob(jobId, onStatus) {
  for (;;) {
    if (typeof _makeRenderAbortIfNeeded === 'function') _makeRenderAbortIfNeeded();
    const d = await apiJson('/api/status/' + jobId);
    const j = d.job;
    if (onStatus) onStatus(j.status || 'working', j);
    if (j.error) throw new Error(j.error);
    if (j.done) {
      const sd = await apiJson('/api/script/' + jobId);
      return { text: sd.text, title: j.title || 'Phantomline video', project_id: j.project_id };
    }
    await new Promise(resolve => setTimeout(resolve, 1500));
  }
}

async function waitForMusicProject(jobId, onStatus) {
  for (;;) {
    if (typeof _makeRenderAbortIfNeeded === 'function') _makeRenderAbortIfNeeded();
    const d = await apiJson('/api/music/status/' + jobId);
    const j = d.job;
    if (onStatus) onStatus(j.status || 'working', j);
    if (j.error) throw new Error(j.error);
    if (j.done) return j.project_id;
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
}

async function waitForMixProject(jobId, onStatus) {
  for (;;) {
    if (typeof _makeRenderAbortIfNeeded === 'function') _makeRenderAbortIfNeeded();
    const d = await apiJson('/api/mix/status/' + jobId);
    const j = d.job;
    if (onStatus) onStatus(j.status || 'working', j);
    if (j.error) throw new Error(j.error);
    if (j.done) return j.project_id;
    await new Promise(resolve => setTimeout(resolve, 1500));
  }
}

// Render-cancel flag. Set by the Stop Watching button; read by the polling
// loops so they can bail mid-flight. The backend thread keeps running, but
// the UI returns to idle and the result will land in Library when done.
let _makeRenderAborted = false;
function _makeRenderAbortIfNeeded() {
  if (_makeRenderAborted) {
    const err = new Error('Render cancelled. The job is still finishing in the background. Check Library.');
    err.cancelled = true;
    throw err;
  }
}

async function makeVideoWorkflow() {
  resetMakeSteps();
  _makeRenderAborted = false;
  $('makeVideoBtn').disabled = true;
  const cancelBtn = $('makeCancelBtn');
  if (cancelBtn) {
    cancelBtn.style.display = '';
    cancelBtn.disabled = false;
    cancelBtn.removeAttribute('data-blocked-reason');
  }
  try {
    const videoModeValue = $('makeVideoMode').value;
    const useSourceVideo = videoModeValue === 'source' || videoModeValue === 'library';
    let sourceVideoProjectId = makeSourceVideoProjectId;
    let sourceVideoMeta = null;
    if (useSourceVideo) {
      const isLibrary = videoModeValue === 'library' && makeSourceLibraryPick;
      setMakeStep('makeStepScenes', 'running', isLibrary ? 'caching library clip' : 'uploading video');
      sourceVideoMeta = await uploadMakeSourceVideo();
      sourceVideoProjectId = sourceVideoMeta.project_id;
      setMakeStep('makeStepScenes', 'done', isLibrary ? 'library clip ready' : 'source video ready');
    }

    setMakeStep('makeStepScript', 'running', 'writing');
    const sourceWords = sourceVideoMeta?.duration_seconds && !isViralStoryMode()
      ? Math.max(40, Math.round(sourceVideoMeta.duration_seconds * 2.33))
      : null;
    const wordCount = sourceWords || parseInt($('makeDuration').value, 10) || 280;
    const longForm = wordCount > 1400;
    const scriptStart = await apiJson(longForm ? '/api/start' : '/api/start_short', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        topic: $('makeTopic').value.trim(),
        description: makeCreativeBrief(),
        genre: $('makeNiche').value.trim(),
        tone: $('makeTone').value.trim() || (window.GHOSTLINE_DEFAULTS && window.GHOSTLINE_DEFAULTS.tone) || '',
        word_count: wordCount,
        model: $('makeModel').value.trim(),
      }),
    });
    const script = await waitForStoryJob(scriptStart.job_id, (status, job) => {
      const section = longForm && job.section ? `section ${job.section} · ` : '';
      setMakeStep('makeStepScript', 'running', `${section}${status} · ${(job.words || 0).toLocaleString()} words`);
    });
    setMakeStep('makeStepScript', 'done', 'done');

    setMakeStep('makeStepNarration', 'running', 'starting');
    const ttsStart = await apiJson('/api/tts/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        text: script.text,
        title: script.title,
        voice: $('makeVoice').value || 'af_nicole',
        speed: 1.0,
        format: 'mp3',
      }),
    });
    const narrationProjectId = await waitForTtsProject(ttsStart.job_id, msg => {
      setMakeStep('makeStepNarration', 'running', msg);
    });
    setMakeStep('makeStepNarration', 'done', 'done');

    let planData = null;
    if (!useSourceVideo) {
      setMakeStep('makeStepScenes', 'running', 'planning');
      planData = await apiJson('/api/video/plan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          script: script.text,
          title: script.title,
          workflow: 'image-to-video',
          aspect: $('makeAspect').value,
          scene_seconds: parseInt($('makeSceneSeconds').value, 10) || 8,
          ...visualSettings('make'),
        }),
      });
      renderVideoPlan(planData.plan, planData.project);
      setMakeStep('makeStepScenes', 'done', `${planData.plan.scene_count} scenes`);
    }

    setMakeStep('makeStepMusic', 'running', 'composing');
    const musicMinutes = Math.max(0.5, wordCount / 140);
    const musicStart = await apiJson('/api/music/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        prompt: $('makeMusicPrompt').value.trim(),
        minutes: musicMinutes,
        model_size: 'small',
        fade_seconds: 2,
        format: 'mp3',
        name: script.title + ' music bed',
      }),
    });
    const musicProjectId = await waitForMusicProject(musicStart.job_id, msg => {
      setMakeStep('makeStepMusic', 'running', msg);
    });
    setMakeStep('makeStepMusic', 'done', 'done');

    setMakeStep('makeStepMix', 'running', 'mixing');
    const mixStart = await apiJson('/api/mix/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        tts_job_id: ttsStart.job_id,
        music_job_id: musicStart.job_id,
        music_db_below_speech: 18,
        format: 'mp3',
        name: script.title,
      }),
    });
    const mixProjectId = await waitForMixProject(mixStart.job_id, msg => {
      setMakeStep('makeStepMix', 'running', msg);
    });
    setMakeStep('makeStepMix', 'done', 'done');

    let draftStart = null;
    if (useSourceVideo) {
      setMakeStep('makeStepTimeline', 'done', 'source video');
      setMakeStep('makeStepVideo', 'running', 'rendering source video');
      draftStart = await apiJson('/api/video/source/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          source_video_project_id: sourceVideoProjectId,
          audio_project_id: mixProjectId,
          title: script.title,
          aspect: $('makeAspect').value,
          fit: $('makeSourceFit').value,
          captions: $('makeCaptions').value === '1',
          caption_text: script.text,
          caption_style: $('makeCaptionStyle').value,
          keyword_mode: $('makeKeywordMode').value,
          pattern_interrupts: $('makePatternInterrupts').value === '1',
          source_enhance: $('makeSourceEnhance').value,
          title_style: $('makeTitleStyle').value,
        }),
      });
    } else {
      setMakeStep('makeStepTimeline', 'running', 'aligning');
      const timelineData = await apiJson('/api/video/timeline', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          plan_project_id: planData.project.id,
          narration_project_id: narrationProjectId,
        }),
      });
      renderTimeline(timelineData.timeline, timelineData.project);
      setMakeStep('makeStepTimeline', 'done', timelineData.timeline.audio_duration);

      setMakeStep('makeStepVideo', 'running', 'rendering');
      draftStart = await apiJson('/api/video/draft/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          timeline_project_id: timelineData.project.id,
          audio_project_id: mixProjectId,
          image_provider: $('videoImageProvider').value,
          image_quality: $('videoImageQuality').value,
          forge_url: $('videoForgeUrl').value.trim(),
          forge_checkpoint: $('videoForgeCheckpoint').value.trim(),
        }),
      });
    }
    makeVideoJob = draftStart.job_id;
    const finalVideoProjectId = await waitForDraftVideo(makeVideoJob, msg => {
      setMakeStep('makeStepVideo', 'running', msg);
    });
    latestMakeVideoProjectId = finalVideoProjectId;
    setMakeStep('makeStepVideo', 'done', 'done');

    const finishedVideoUrl = '/api/video/draft/download/' + makeVideoJob + '?inline=1';
    $('makePreviewVideo').src = finishedVideoUrl;
    const phoneVideo = $('makePhonePreviewVideo');
    if (phoneVideo) {
      phoneVideo.src = finishedVideoUrl;
      phoneVideo.style.display = 'block';
      phoneVideo.load();
      $('makePhonePreviewInner')?.classList.add('has-video');
    }
    $('makeResultHint').textContent = useSourceVideo
      ? 'This MP4 uses your uploaded video as the visual layer with generated narration, music, and optional burned-in captions.'
      : 'This draft MP4 is fully timed with narration and background music. Matching scene clips and stills are used when they exist, with generated local images for missing scenes.';
    $('makeResult').classList.add('shown');
    preparePublishDraft({
      videoProjectId: finalVideoProjectId,
      title: $('makePreferredTitle').value.trim() || script.title,
      caption: makeCreativeBrief(),
      scriptText: script.text,
      tags: $('makeHashtags').value.trim(),
      pinnedComment: $('makePinnedComment').value.trim(),
    });
    renderMakeHandoffChecklist();
    // Bundle this whole session so it shows up in Library as one navigable
    // record. Best-effort. never let a bundle failure obscure the rendered
    // video; the artifacts are already saved as individual projects.
    try {
      const members = {};
      if (script?.project_id) members.script = script.project_id;
      if (typeof narrationProjectId === 'string') members.narration = narrationProjectId;
      if (typeof mixProjectId === 'string') members.mix = mixProjectId;
      if (typeof musicProjectId === 'string') members.music = musicProjectId;
      if (typeof sourceVideoProjectId === 'string' && sourceVideoProjectId) members.source_video = sourceVideoProjectId;
      if (typeof finalVideoProjectId === 'string') members.video = finalVideoProjectId;
      const bundleParams = {
        topic: $('makeTopic')?.value || '',
        title: $('makePreferredTitle')?.value || script.title || '',
        preferredTitle: $('makePreferredTitle')?.value || '',
        niche: $('makeNiche')?.value || '',
        audience: $('makeAudience')?.value || '',
        format: $('makeFormat')?.value || '',
        recipe: $('makeRecipe')?.value || '',
        hookStyle: $('makeHookStyle')?.value || '',
        tensionFormat: $('makeTensionFormat')?.value || '',
        loopType: $('makeLoopType')?.value || '',
        tone: $('makeTone')?.value || '',
        videoMode: $('makeVideoMode')?.value || '',
        duration: $('makeDuration')?.value || '',
        aspect: $('makeAspect')?.value || '',
        captions: $('makeCaptions')?.value || '',
        captionStyle: $('makeCaptionStyle')?.value || '',
        voice: $('makeVoice')?.value || '',
        musicPrompt: $('makeMusicPrompt')?.value || '',
        visualPreset: $('makeVisualPreset')?.value || '',
        visualStyle: $('makeVisualStyle')?.value || '',
        visualAmbience: $('makeAmbience')?.value || '',
        visualCharacter: $('makeVisualCharacter')?.value || '',
        pinnedComment: $('makePinnedComment')?.value || '',
        hashtags: $('makeHashtags')?.value || '',
        selectedIdea: typeof makeSelectedIdea !== 'undefined' ? makeSelectedIdea : null,
      };
      await fetch('/api/bundles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: $('makePreferredTitle')?.value || script.title || 'Untitled video',
          params: bundleParams,
          members,
        }),
      });
    } catch (bundleErr) {
      window.ghTelemetry && window.ghTelemetry('bundle-create-failed', { message: String(bundleErr?.message || '') });
    }
    loadRecentWork();
    loadLibrary();
    toast('Video ready');
    // Auto-pull-forward to the handoff card so the user immediately sees
    // "preview / download / publish draft" without scrolling.
    const handoff = $('makeHandoffChecklist') || $('makeOpenPublishBtn');
    if (handoff) {
      handoff.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(() => $('makePreviewFinalBtn')?.focus({ preventScroll: true }), 400);
    }
    // Telemetry: capture render success so we can build ETAs over time.
    window.ghTelemetry && window.ghTelemetry('render-complete', {
      ratio: $('makeAspect')?.value, mode: $('makeVideoMode')?.value,
    });
  } catch (e) {
    const running = document.querySelector('.workflow-step.running');
    if (e.cancelled) {
      // Friendly cancel: don't paint the step red. Just reset.
      if (running) {
        running.classList.remove('running');
        const s = running.querySelector('.state');
        if (s) s.textContent = 'cancelled';
      }
      toast('Render cancelled. Check Library when the background job finishes.');
      window.ghTelemetry && window.ghTelemetry('render-cancelled', { step: running?.dataset?.step || '' });
    } else {
      if (running) {
        running.classList.remove('running');
        running.classList.add('error');
        const s = running.querySelector('.state');
        if (s) s.textContent = e.message || 'error';
      }
      toast(e.message || 'Make Video failed', true);
      window.ghTelemetry && window.ghTelemetry('render-error', {
        message: String(e.message || ''),
        step: running?.dataset?.step || '',
      });
    }
  } finally {
    $('makeVideoBtn').disabled = false;
    const cancelBtn = $('makeCancelBtn');
    if (cancelBtn) {
      cancelBtn.style.display = 'none';
      cancelBtn.disabled = true;
    }
    _makeRenderAborted = false;
  }
}

// Cancel button: trips the abort flag. The active await chain throws on
// next check; the UI resets cleanly. Backend thread keeps going. its
// artifacts will appear in Library when complete.
$('makeCancelBtn')?.addEventListener('click', () => {
  _makeRenderAborted = true;
  const cancelBtn = $('makeCancelBtn');
  if (cancelBtn) {
    cancelBtn.disabled = true;
    cancelBtn.textContent = 'Cancelling...';
  }
});

function updateMakeDurationHint() {
  const words = parseInt($('makeDuration').value, 10) || 280;
  const hint = $('makeDurationHint');
  if (!hint) return;
  if (isViralStoryMode()) {
    hint.textContent = 'Viral short mode: aims for a 35-45s hook, twist, and loop ending.';
  } else if (words > 1400) {
    hint.textContent = 'Long-form mode: writes in sections, then builds one full-length narrated timeline.';
  } else {
    hint.textContent = 'Short-form mode: writes one tight script, then builds the narrated draft.';
  }
}

$('makeVideoBtn').addEventListener('click', makeVideoWorkflow);
$('makeVideoMode').addEventListener('change', updateMakeVideoMode);
$('makeSourceVideoFile').addEventListener('change', () => {
  makeSourceVideoProjectId = null;
  if (makeSourceLibraryPick) clearLibrarySourcePick();
  const file = $('makeSourceVideoFile').files && $('makeSourceVideoFile').files[0];
  $('makeSourceVideoStatus').textContent = file
    ? `Ready to upload: ${file.name}`
    : 'Upload gameplay, parkour, drone footage, screen recordings, or any clip you want narrated.';
  updateMakeReadiness();
});

/* ─── Footage library picker ───────────────────────────────────────────── */
function clearLibrarySourcePick() {
  makeSourceLibraryPick = null;
  makeSourceLibraryMeta = null;
  makeSourceVideoProjectId = null;
  const pick = $('makeSourceLibraryPick');
  if (pick) {
    pick.hidden = true;
    pick.querySelector('.source-library-pick-id').textContent = '';
    pick.querySelector('.source-library-pick-id').dataset.clipId = '';
  }
  $('makeSourceVideoStatus').textContent = 'Upload gameplay, parkour, drone footage, screen recordings, or any clip you want narrated.';
  updateMakeReadiness();
}

var _footageLibraryLoaded = false;
var _footageActiveCategory = '';   // '' = All
async function openFootageLibrary() {
  const modal = $('footageLibraryModal');
  if (!modal) return;
  modal.classList.add('shown');
  modal.setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
  if (!_footageLibraryLoaded) {
    await loadFootageLibrary();
    _footageLibraryLoaded = true;
  }
}

function closeFootageLibrary() {
  const modal = $('footageLibraryModal');
  if (!modal) return;
  modal.classList.remove('shown');
  modal.setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
}

async function loadFootageLibrary(category) {
  const grid = $('footageLibraryGrid');
  const status = $('footageLibraryStatus');
  if (!grid) return;
  if (typeof category === 'string') _footageActiveCategory = category;
  grid.setAttribute('aria-busy', 'true');
  grid.innerHTML = '';
  status.classList.remove('error');
  status.textContent = 'Loading library…';
  try {
    let url = '/api/library/footage?aspect=9:16';
    if (_footageActiveCategory) url += `&category=${encodeURIComponent(_footageActiveCategory)}`;
    const r = await fetch(url);
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'Failed to load library');
    if (!_footageLibraryLoaded) renderFootageFilters(d.clips || []);
    if (!d.clips || d.clips.length === 0) {
      grid.innerHTML = '<div class="gh-footage-empty">No footage in this category.</div>';
      status.textContent = '';
      return;
    }
    grid.innerHTML = '';
    d.clips.forEach(clip => grid.appendChild(buildFootageThumb(clip)));
    const filter = _footageActiveCategory ? ` · filter: ${_footageActiveCategory}` : '';
    status.textContent = `${d.clips.length} clips${filter} · click one to use as your source video.`;
  } catch (e) {
    status.classList.add('error');
    status.textContent = e.message || 'Failed to load library';
  } finally {
    grid.setAttribute('aria-busy', 'false');
  }
}

function renderFootageFilters(clipsForUnion) {
  const row = $('footageLibraryFilters');
  if (!row) return;
  const cats = new Set();
  clipsForUnion.forEach(c => (c.categories || []).forEach(t => { if (t) cats.add(t); }));
  const ordered = ['', ...Array.from(cats).sort()];
  row.innerHTML = '';
  ordered.forEach(cat => {
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'gh-footage-pill' + (cat === _footageActiveCategory ? ' is-active' : '');
    pill.dataset.category = cat;
    pill.setAttribute('role', 'tab');
    pill.setAttribute('aria-selected', cat === _footageActiveCategory ? 'true' : 'false');
    pill.textContent = cat || 'All';
    pill.addEventListener('click', () => {
      if (cat === _footageActiveCategory) return;
      Array.from(row.children).forEach(p => {
        const on = p.dataset.category === cat;
        p.classList.toggle('is-active', on);
        p.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      loadFootageLibrary(cat);
    });
    row.appendChild(pill);
  });
}

// SVG icons for the Shorts chrome — copied verbatim from the landing-page
// hero so the picker tiles read as the same Shorts mock.
const _GH_FOOTAGE_SVG = {
  signal: '<svg viewBox="0 0 16 10" width="14" height="9" fill="currentColor" aria-hidden="true"><rect x="0" y="6" width="3" height="4"/><rect x="4" y="4" width="3" height="6"/><rect x="8" y="2" width="3" height="8"/><rect x="12" y="0" width="3" height="10"/></svg>',
  wifi: '<svg viewBox="0 0 16 12" width="13" height="10" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M2 5a9 9 0 0 1 12 0M4.5 7.5a5 5 0 0 1 7 0M7 10a1.5 1.5 0 0 1 2 0"/></svg>',
  battery: '<svg viewBox="0 0 22 10" width="18" height="8" fill="none" stroke="currentColor" stroke-width="1" aria-hidden="true"><rect x="0.5" y="0.5" width="18" height="9" rx="2"/><rect x="2" y="2" width="14" height="6" rx="1" fill="currentColor"/><rect x="19.5" y="3" width="2" height="4" rx="0.5" fill="currentColor"/></svg>',
  search: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>',
  kebab: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><circle cx="12" cy="5" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="12" cy="19" r="1.6"/></svg>',
  like: '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9A2 2 0 0 0 19.7 9zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>',
  dislike: '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9A2 2 0 0 0 4.32 15zM17 2h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3"/></svg>',
  comment: '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-9 8.38 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.2A8.5 8.5 0 0 1 4 12 8.38 8.38 0 0 1 12.5 3.5 8.38 8.38 0 0 1 21 11.5z"/></svg>',
  share: '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg>',
  remix: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  yt: '<svg viewBox="0 0 28 20" width="22" height="16"><rect width="28" height="20" rx="5" fill="#ff0033"/><polygon points="11,5 11,15 19,10" fill="#fff"/></svg>',
};

// Stable pseudo-random metric per clip id so the rail counts don't
// change between reloads or after a filter click. Returns "1.4M" / "812K".
function _ghFakeMetric(seed, salt) {
  let h = 0;
  const s = seed + ':' + salt;
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  const n = Math.abs(h) % 9500 + 100;   // 100..9599
  if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'M';
  return n + 'K';
}

function buildFootageThumb(clip) {
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'gh-footage-thumb';
  card.dataset.clipId = clip.id;
  const labelTitle = clip.title || clip.id;
  card.setAttribute('aria-label', `Use ${labelTitle} as source video`);
  card.title = clip.description ? `${labelTitle} — ${clip.description}` : labelTitle;

  const img = document.createElement('img');
  img.alt = '';
  img.decoding = 'async';
  if (clip.thumbnail_url) img.src = clip.thumbnail_url;
  card.appendChild(img);

  let video = null;
  let videoLoaded = false;
  let isHovering = false;
  const ensureVideo = () => {
    if (video) return video;
    video = document.createElement('video');
    video.src = clip.url;
    video.preload = 'metadata';
    video.muted = true;
    video.loop = true;
    video.playsInline = true;
    video.style.opacity = '0';
    video.addEventListener('loadeddata', () => {
      videoLoaded = true;
      // Only reveal if the user is still hovering — otherwise the user
      // already moved on and we'd flash a paused video frame over the JPG.
      if (isHovering) video.style.opacity = '1';
    }, { once: true });
    card.appendChild(video);
    return video;
  };
  const playPreview = () => {
    isHovering = true;
    const v = ensureVideo();
    if (videoLoaded) v.style.opacity = '1';
    v.play().catch(() => {});
  };
  const stopPreview = () => {
    isHovering = false;
    if (!video) return;
    video.pause();
    video.style.opacity = '0';
  };
  card.addEventListener('mouseenter', playPreview);
  card.addEventListener('focus', playPreview);
  card.addEventListener('mouseleave', stopPreview);
  card.addEventListener('blur', stopPreview);

  // Static-thumbnail 404 fallback: show the video poster frame instead.
  // visibility:hidden (not display:none) keeps the img's aspect-ratio
  // layout box so the grid row stays the right height.
  img.addEventListener('error', () => {
    img.style.visibility = 'hidden';
    const v = ensureVideo();
    v.style.opacity = '1';
    v.addEventListener('loadedmetadata', () => {
      v.currentTime = Math.min(0.5, (v.duration || 1) * 0.05);
    }, { once: true });
  }, { once: true });

  // Shorts chrome overlay — same structure as the landing-page hero
  // (hero-shorts-*). Bottom strip uses the curated clip title so each
  // tile reads as a unique Short.
  const firstCat = (clip.categories && clip.categories[0]) || '';
  const I = _GH_FOOTAGE_SVG;
  const likes = _ghFakeMetric(clip.id, 'l');
  const cmts = _ghFakeMetric(clip.id, 'c');
  const shrs = _ghFakeMetric(clip.id, 's');
  const rmx = _ghFakeMetric(clip.id, 'r');
  const desc = firstCat
    ? `${labelTitle} · #${firstCat}`
    : `${labelTitle} · #shorts`;
  const chrome = document.createElement('div');
  chrome.className = 'gh-footage-thumb-chrome';
  chrome.innerHTML = `
    <div class="gh-footage-top">
      <span class="gh-footage-time">9:41</span>
      <span class="gh-footage-system">${I.signal}${I.wifi}${I.battery}</span>
    </div>
    <div class="gh-footage-topactions" aria-hidden="true">${I.search}${I.kebab}</div>
    <div class="gh-footage-rail" aria-hidden="true">
      <div class="gh-footage-action">${I.like}<span>${likes}</span></div>
      <div class="gh-footage-action">${I.dislike}<span>Dislike</span></div>
      <div class="gh-footage-action">${I.comment}<span>${cmts}</span></div>
      <div class="gh-footage-action">${I.share}<span>${shrs}</span></div>
      <div class="gh-footage-action">${I.remix}<span>${rmx}</span></div>
      <div class="gh-footage-yt">${I.yt}</div>
    </div>
    <div class="gh-footage-bottom">
      <span class="gh-footage-creator">
        <span class="gh-footage-avatar"><img src="/static/phantomline-app-icon.png" alt="" width="22" height="22"></span>
        <strong>@phantomline.xyz</strong>
      </span>
      <span class="gh-footage-desc">${escapeHtml(desc)}</span>
    </div>
  `;
  card.appendChild(chrome);

  card.addEventListener('click', () => pickFootageClip(clip));
  return card;
}

async function pickFootageClip(clip) {
  const status = $('footageLibraryStatus');
  status.classList.remove('error');
  status.textContent = `Caching ${clip.id}…`;
  try {
    const r = await fetch(`/api/upload/source-video-from-library/${encodeURIComponent(clip.id)}`, { method: 'POST' });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'Pick failed');
    makeSourceLibraryPick = clip.id;
    makeSourceLibraryMeta = d;
    makeSourceVideoProjectId = d.project_id;
    const fileInput = $('makeSourceVideoFile');
    if (fileInput) fileInput.value = '';
    const pick = $('makeSourceLibraryPick');
    if (pick) {
      pick.hidden = false;
      const idEl = pick.querySelector('.source-library-pick-id');
      idEl.textContent = clip.id;
      idEl.dataset.clipId = clip.id;
    }
    const dur = d.duration_seconds ? `, ${Math.round(d.duration_seconds)}s` : '';
    const dim = d.width && d.height ? `, ${d.width}x${d.height}` : '';
    $('makeSourceVideoStatus').innerHTML =
      `<strong style="color:var(--text);">Library:</strong> ${escapeHtml(clip.id)} (${d.size_mb} MB${dur}${dim})${d.cached ? ' · cached' : ' · downloaded'}`;
    closeFootageLibrary();
    updateMakeReadiness();
    loadLibrary?.();
  } catch (e) {
    status.classList.add('error');
    status.textContent = e.message || 'Pick failed';
  }
}

const _browseFootageBtn = document.getElementById('browseFootageLibraryBtn');
if (_browseFootageBtn) _browseFootageBtn.addEventListener('click', openFootageLibrary);

const _footageModal = document.getElementById('footageLibraryModal');
if (_footageModal) {
  _footageModal.querySelector('.gh-footage-close')?.addEventListener('click', closeFootageLibrary);
  _footageModal.addEventListener('click', (e) => {
    if (e.target === _footageModal) closeFootageLibrary();
  });
}

const _libraryPickClear = document.querySelector('#makeSourceLibraryPick .source-library-pick-clear');
if (_libraryPickClear) _libraryPickClear.addEventListener('click', clearLibrarySourcePick);

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const modal = document.getElementById('footageLibraryModal');
    if (modal && modal.classList.contains('shown')) closeFootageLibrary();
  }
});
updateMakeVideoMode();
$('makeDuration').addEventListener('change', updateMakeDurationHint);
updateMakeDurationHint();
[
  'makeTopic', 'makePreferredTitle', 'makeNiche', 'makeAudience', 'makeFormat',
  'makeHookStyle', 'makeKeywordMode', 'makeVoice', 'makeTone', 'makeAspect',
  'makeCaptions', 'makeCaptionStyle', 'makePatternInterrupts', 'makeSourceEnhance',
  'makeTitleStyle', 'makeVisualPreset', 'makeAmbience', 'makeVisualStyle',
  'makeVisualCharacter', 'makeMusicPrompt', 'makeDuration', 'makeTensionFormat', 'makeLoopType',
  'makePinnedComment', 'makeHashtags'
].forEach(id => {
  const el = $(id);
  if (!el) return;
  el.addEventListener('input', updateMakeReadiness);
  el.addEventListener('change', updateMakeReadiness);
});
$('makeDownloadVideoBtn').addEventListener('click', () => {
  downloadMakeVideo();
});
$('makeOpenLibraryBtn').addEventListener('click', () => {
  document.querySelector('.tab-btn[data-tab="library"]').click();
});
$('makeOpenAdvancedBtn').addEventListener('click', () => {
  document.querySelector('.tab-btn[data-tab="video"]').click();
});

function makeDownloadFilename() {
  const raw = $('publishTitle')?.value || $('makePreferredTitle')?.value || $('makePreviewTitle')?.textContent || 'phantomline-video';
  const safe = raw.toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80) || 'phantomline-video';
  return safe + '.mp4';
}

async function downloadMakeVideo() {
  if (!makeVideoJob) return;
  const filename = makeDownloadFilename();
  const url = '/api/video/draft/download/' + makeVideoJob + '?filename=' + encodeURIComponent(filename);
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: filename,
        types: [{ description: 'MP4 video', accept: { 'video/mp4': ['.mp4'] } }],
      });
      const res = await fetch(url);
      if (!res.ok) throw new Error('Download failed');
      const writable = await handle.createWritable();
      await writable.write(await res.blob());
      await writable.close();
      toast('Video saved');
      return;
    } catch (e) {
      if (e && e.name === 'AbortError') return;
    }
  }
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
$('makeOpenPublishBtn').addEventListener('click', () => {
  document.querySelector('.tab-btn[data-tab="publish"]').click();
  loadPublishWorkspace();
});
$('makePublishDraftBtn')?.addEventListener('click', () => {
  document.querySelector('.tab-btn[data-tab="publish"]').click();
  loadPublishWorkspace();
});
$('makePreviewFinalBtn')?.addEventListener('click', () => {
  const video = $('makePreviewVideo');
  if (!video) return;
  video.scrollIntoView({ behavior: 'smooth', block: 'center' });
  try { video.play(); } catch {}
});

function syncSettingsFromCreate() {
  if ($('settingsModel') && $('makeModel')) $('settingsModel').value = $('makeModel').value;
  if ($('settingsAspect') && $('makeAspect')) $('settingsAspect').value = $('makeAspect').value;
  if ($('settingsCaptionStyle') && $('makeCaptionStyle')) $('settingsCaptionStyle').value = $('makeCaptionStyle').value;
  if ($('settingsVoice') && $('makeVoice')) $('settingsVoice').value = $('makeVoice').value;
  if ($('settingsForgeUrl') && $('videoForgeUrl')) $('settingsForgeUrl').value = $('videoForgeUrl').value;
  if ($('settingsForgeCheckpoint') && $('videoForgeCheckpoint')) $('settingsForgeCheckpoint').value = $('videoForgeCheckpoint').value;
}

function _settingsPayload() {
  return {
    model: $('settingsModel')?.value || '',
    aspect: $('settingsAspect')?.value || '',
    captionStyle: $('settingsCaptionStyle')?.value || '',
    voice: $('settingsVoice')?.value || '',
    musicLevel: $('settingsMusicLevel')?.value || '',
    forgeUrl: ($('settingsForgeUrl')?.value || '').trim(),
    forgeCheckpoint: ($('settingsForgeCheckpoint')?.value || '').trim(),
    simpleMode: !!$('settingsSimpleMode')?.checked,
    telemetry: !!$('settingsTelemetry')?.checked,
  };
}

async function applySettingsDefaults() {
  if ($('settingsModel')?.value && $('makeModel')) $('makeModel').value = $('settingsModel').value;
  if ($('settingsAspect')?.value && $('makeAspect')) $('makeAspect').value = $('settingsAspect').value;
  if ($('settingsCaptionStyle')?.value && $('makeCaptionStyle')) $('makeCaptionStyle').value = $('settingsCaptionStyle').value;
  if ($('settingsVoice')?.value && $('makeVoice')) $('makeVoice').value = $('settingsVoice').value;
  if ($('settingsForgeUrl')?.value && $('videoForgeUrl')) $('videoForgeUrl').value = $('settingsForgeUrl').value.trim();
  if ($('settingsForgeCheckpoint')?.value && $('videoForgeCheckpoint')) $('videoForgeCheckpoint').value = $('settingsForgeCheckpoint').value.trim();
  updateMakeReadiness();
  // Persist to disk so defaults survive restart.
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings: _settingsPayload() }),
    });
    const data = await res.json();
    if (data.ok) {
      if ($('settingsStatus')) $('settingsStatus').textContent = 'Defaults saved and applied to Create Video.';
      toast('Settings saved');
    } else {
      if ($('settingsStatus')) $('settingsStatus').textContent = data.error || 'Defaults applied (could not save to disk).';
      toast('Applied locally, save failed', true);
    }
  } catch {
    if ($('settingsStatus')) $('settingsStatus').textContent = 'Defaults applied. Server unreachable; not saved.';
    toast('Applied locally, server unreachable', true);
  }
}

async function loadSavedSettings() {
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    const s = (data && data.settings) || {};
    // Push saved values into Settings tab inputs so Apply (or auto-apply) uses them.
    if (s.aspect && $('settingsAspect')) $('settingsAspect').value = s.aspect;
    if (s.captionStyle && $('settingsCaptionStyle')) $('settingsCaptionStyle').value = s.captionStyle;
    if (s.musicLevel && $('settingsMusicLevel')) $('settingsMusicLevel').value = s.musicLevel;
    if (s.forgeUrl && $('settingsForgeUrl')) $('settingsForgeUrl').value = s.forgeUrl;
    if (s.forgeCheckpoint && $('settingsForgeCheckpoint')) $('settingsForgeCheckpoint').value = s.forgeCheckpoint;
    // Model and voice selects populate async. defer.
    setTimeout(() => {
      if (s.model && $('settingsModel') && [...$('settingsModel').options].some(o => o.value === s.model)) {
        $('settingsModel').value = s.model;
      }
      if (s.voice && $('settingsVoice') && [...$('settingsVoice').options].some(o => o.value === s.voice)) {
        $('settingsVoice').value = s.voice;
      }
      // Push the saved values forward into the live Create Video form too.
      if (s.aspect && $('makeAspect')) $('makeAspect').value = s.aspect;
      if (s.captionStyle && $('makeCaptionStyle')) $('makeCaptionStyle').value = s.captionStyle;
      if (s.model && $('makeModel') && [...$('makeModel').options].some(o => o.value === s.model)) $('makeModel').value = s.model;
      if (s.voice && $('makeVoice') && [...$('makeVoice').options].some(o => o.value === s.voice)) $('makeVoice').value = s.voice;
      if (s.forgeUrl && $('videoForgeUrl')) $('videoForgeUrl').value = s.forgeUrl;
      if (s.forgeCheckpoint && $('videoForgeCheckpoint')) $('videoForgeCheckpoint').value = s.forgeCheckpoint;
      updateMakeReadiness && updateMakeReadiness();
    }, 1500);
    // Also seed simple-mode + telemetry checkboxes from server (server is the source of truth on a fresh device).
    if (typeof s.simpleMode === 'boolean' && $('settingsSimpleMode')) {
      $('settingsSimpleMode').checked = s.simpleMode;
      ghSavePrefs({ simpleMode: s.simpleMode });
      applySimpleMode();
    }
    if (typeof s.telemetry === 'boolean' && $('settingsTelemetry')) {
      $('settingsTelemetry').checked = s.telemetry;
      ghSavePrefs({ telemetry: s.telemetry });
    }
  } catch {}
}

$('settingsApplyBtn')?.addEventListener('click', applySettingsDefaults);
window.addEventListener('load', loadSavedSettings);
$('settingsOpenConnectionsBtn')?.addEventListener('click', () => {
  document.querySelector('.tab-btn[data-tab="publish"]').click();
  switchPublishView('connections');
});
$('settingsOpenCreateBtn')?.addEventListener('click', () => {
  document.querySelector('.tab-btn[data-tab="make"]').click();
});
document.querySelector('.tab-btn[data-tab="settings"]')?.addEventListener('click', syncSettingsFromCreate);

// ---------- Publish Studio (Cadence port) ----------
let publishPosts = [];
let publishVideos = [];
let publishStatus = null;
let latestAnalyticsContext = null;

function localDateTimeValue(date) {
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function setPublishDefaultTime() {
  if (!$('publishScheduledAt').value) {
    $('publishScheduledAt').value = localDateTimeValue(new Date(Date.now() + 60 * 60 * 1000));
  }
}

function switchPublishView(view) {
  document.querySelectorAll('.publish-nav button').forEach(b => b.classList.toggle('active', b.dataset.pubView === view));
  document.querySelectorAll('.publish-view').forEach(v => v.style.display = v.id === 'pubView-' + view ? '' : 'none');
  if (view === 'calendar') renderPublishCalendar();
  if (view === 'queue') renderPublishQueue();
  if (view === 'templates') loadPublishTemplates();
  if (view === 'recurring') loadPublishRecurring();
  if (view === 'analytics') renderPublishAnalytics();
  if (view === 'media') renderPublishMedia();
}

document.querySelectorAll('.publish-nav button').forEach(btn => {
  btn.addEventListener('click', () => switchPublishView(btn.dataset.pubView));
});

function updatePublishPreview() {
  const projectId = $('publishVideoProject').value;
  const project = publishVideos.find(v => v.id === projectId);
  $('publishPreviewBox').innerHTML = project
    ? `<video controls src="/api/projects/${project.id}/file/video?inline=1"></video>`
    : '<div class="empty">No media selected</div>';
  $('publishPreviewTitle').textContent = $('publishTitle').value.trim() || 'Untitled video';
  $('publishPreviewCaption').textContent = ($('publishCaption').value.trim() || 'Description preview').slice(0, 180);
  $('publishCaptionCount').textContent = $('publishCaption').value.length;
  renderMakeHandoffChecklist();
  renderPublishReadinessChecklist();
}

async function loadPublishWorkspace() {
  setPublishDefaultTime();
  await Promise.all([loadPublishStatus(), loadPublishVideos(), loadPublishPosts()]);
  updatePublishPreview();
}

async function loadPublishStatus() {
  try {
    const d = await apiJson('/api/publish/status');
    publishStatus = d;
    const channel = d.youtube_channel && d.youtube_channel.title ? d.youtube_channel.title : 'not connected';
    $('publishConnectionStatus').textContent = d.youtube_connected ? channel : 'not connected';
    $('connectionsText').textContent = d.youtube_connected
      ? `YouTube connected: ${channel}`
      : (d.youtube_configured ? 'YouTube OAuth is configured. Connect a channel to schedule uploads.' : 'YouTube OAuth keys were not found.');
    $('publishConnectYouTubeBtn').textContent = d.youtube_connected ? 'Reconnect YouTube' : 'Connect YouTube';
    $('connectionsYoutubeBtn').textContent = d.youtube_connected ? 'Reconnect YouTube' : 'Connect YouTube';
    renderPublishReadinessChecklist();
  } catch {
    $('publishConnectionStatus').textContent = 'error';
  }
}

async function loadPublishVideos() {
  const d = await apiJson('/api/projects?kind=video');
  publishVideos = d.projects || [];
  const sel = $('publishVideoProject');
  const prev = sel.value;
  sel.innerHTML = '<option value="">- choose a rendered MP4 -</option>';
  for (const p of publishVideos) {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = p.title || p.id;
    sel.appendChild(opt);
  }
  if (latestMakeVideoProjectId) sel.value = latestMakeVideoProjectId;
  else if (prev) sel.value = prev;
  renderPublishMedia();
}

async function loadPublishPosts() {
  const d = await apiJson('/api/publish/posts');
  const nextPosts = d.posts || [];
  if (publishStatusWatchReady) {
    for (const post of nextPosts) {
      const id = post.id || post.externalPostId || post.title;
      if (!id) continue;
      const previous = knownPublishStatuses[id];
      const current = post.status || '';
      if (previous && previous !== current && current === 'POSTED') {
        addNotification({
          id: `post-live:${id}`,
          type: 'publish',
          title: 'Post is live',
          body: `${post.title || 'Your Phantomline post'} finished uploading to YouTube.`,
          url: post.externalUrl || '',
        });
      }
      if (previous && previous !== current && current === 'FAILED') {
        addNotification({
          id: `post-failed:${id}`,
          type: 'error',
          title: 'Post failed',
          body: `${post.title || 'A scheduled post'} could not publish${post.error ? ': ' + post.error : '.'}`,
        });
      }
    }
  }
  knownPublishStatuses = Object.fromEntries(nextPosts.map(p => [p.id || p.externalPostId || p.title, p.status || '']));
  publishStatusWatchReady = true;
  publishPosts = nextPosts;
  renderPublishQueue();
  renderPublishCalendar();
  renderPublishAnalytics();
}

function fallbackYoutubeDescription({title, topic, tags, pinnedComment}) {
  const cleanTitle = (title || 'this story').replace(/\s+/g, ' ').trim();
  const cleanTopic = (topic || cleanTitle).replace(/\s+/g, ' ').trim();
  const tagLine = (tags || '#shorts #viralshorts #storytime')
    .split(/\s+/)
    .map(t => t.startsWith('#') ? t : '#' + t)
    .filter(Boolean)
    .slice(0, 8)
    .join(' ');
  return [
    `${cleanTitle}: a fast faceless short about ${cleanTopic}.`,
    'Watch to the end for the twist.',
    pinnedComment || 'What would you do?',
    tagLine,
  ].filter(Boolean).join('\n\n').slice(0, 5000);
}

async function generatePublishDescription({title, caption, scriptText, tags, pinnedComment}) {
  try {
    return await apiJson('/api/publish/description', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        title,
        topic: $('makeTopic')?.value.trim() || '',
        script: scriptText || '',
        creative_brief: caption || '',
        niche: $('makeNiche')?.value.trim() || '',
        recipe: $('makeRecipe')?.value || '',
        format: $('makeFormat')?.value || '',
        hashtags: tags || '',
        pinned_comment: pinnedComment || '',
        model: $('makeModel')?.value.trim() || '',
        selected_idea: makeSelectedIdea || null,
      }),
    });
  } catch (e) {
    window.ghTelemetry && window.ghTelemetry('publish-description-failed', { message: String(e?.message || '') });
    return null;
  }
}

async function preparePublishDraft({videoProjectId, title, caption, scriptText, tags, pinnedComment}) {
  latestMakeVideoProjectId = videoProjectId || latestMakeVideoProjectId;
  $('publishTitle').value = title || $('publishTitle').value;
  const cleanTags = tags || $('makeHashtags')?.value || '#shorts #viralshorts';
  $('publishTags').value = cleanTags.replace(/#/g, '').trim();
  $('publishPinnedComment').value = pinnedComment || $('publishPinnedComment').value || 'What would you do?';
  $('publishCaption').value = 'Writing a viewer-facing YouTube Shorts description with Ollama...';
  updatePublishPreview();
  setPublishDefaultTime();
  loadPublishVideos().then(() => {
    if (videoProjectId) $('publishVideoProject').value = videoProjectId;
    updatePublishPreview();
  });
  $('publishStatusLine').textContent = 'Writing YouTube description, tags, and comment with Ollama...';
  const generated = await generatePublishDescription({
    title: $('publishTitle').value,
    caption,
    scriptText,
    tags: cleanTags,
    pinnedComment: $('publishPinnedComment').value,
  });
  if (generated && generated.ok && generated.description) {
    $('publishCaption').value = generated.description.slice(0, 5000);
    if (generated.tags && generated.tags.length) $('publishTags').value = generated.tags.join(' ');
    if (generated.pinned_comment) $('publishPinnedComment').value = generated.pinned_comment;
    $('publishStatusLine').textContent = 'Publish draft auto-filled with a viewer-ready YouTube Shorts package.';
  } else {
    $('publishCaption').value = fallbackYoutubeDescription({
      title: $('publishTitle').value,
      topic: $('makeTopic')?.value || caption || '',
      tags: cleanTags,
      pinnedComment: $('publishPinnedComment').value,
    });
    $('publishStatusLine').textContent = 'Ollama description failed, so Phantomline used a clean viewer-facing fallback.';
  }
  updatePublishPreview();
}

async function schedulePublishPost() {
  const body = {
    video_project_id: $('publishVideoProject').value,
    title: $('publishTitle').value.trim(),
    caption: $('publishCaption').value.trim(),
    tags: $('publishTags').value.trim(),
    pinned_comment: $('publishPinnedComment').value.trim(),
    privacy: $('publishPrivacy').value,
    categoryId: $('publishCategory').value,
    scheduled_at: $('publishScheduledAt').value,
    syntheticMedia: true,
    madeForKids: false,
  };
  if (!body.video_project_id) { toast('Choose a finished MP4 first', true); return; }
  if (!body.title) { toast('Add a YouTube title', true); return; }
  $('publishScheduleBtn').disabled = true;
  try {
    const d = await apiJson('/api/publish/schedule', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    $('publishStatusLine').textContent = `Scheduled: ${d.post.title}`;
    await loadPublishPosts();
    switchPublishView('queue');
    toast('Post scheduled');
  } catch (e) {
    toast(e.message || 'Could not schedule post', true);
  } finally {
    $('publishScheduleBtn').disabled = false;
  }
}

function renderPublishQueue() {
  const el = $('publishQueue');
  if (!el) return;
  if (!publishPosts.length) { el.innerHTML = '<div class="hint">No scheduled posts yet.</div>'; return; }
  el.innerHTML = publishPosts.map(p => `
    <div class="publish-item">
      <strong>${escapeHtml(p.title || 'Untitled')}</strong>
      <div class="hint">${escapeHtml(p.status || 'SCHEDULED')} - ${escapeHtml(p.scheduled_at || 'no time')}</div>
      ${p.externalUrl ? `<a href="${escapeHtml(p.externalUrl)}" target="_blank">Open YouTube post</a>` : ''}
      ${p.error ? `<div class="hint" style="color:var(--bad);">${escapeHtml(p.error)}</div>` : ''}
      ${p.status === 'SCHEDULED' ? `<button class="btn secondary" type="button" data-upload-now="${p.id}">Upload now</button>` : ''}
    </div>
  `).join('');
  el.querySelectorAll('[data-upload-now]').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      const d = await apiJson('/api/publish/upload-now', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ post_id: btn.dataset.uploadNow }),
      });
      if (!d.ok) toast(d.error || 'Upload failed', true);
      await loadPublishPosts();
    });
  });
}

function renderPublishCalendar() {
  const el = $('publishCalendar');
  if (!el) return;
  const today = new Date();
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - today.getDay());
  const days = [];
  for (let i = 0; i < 35; i++) {
    const d = new Date(start.getTime() + i * 86400000);
    const key = d.toISOString().slice(0, 10);
    const posts = publishPosts.filter(p => (p.scheduled_at || '').slice(0, 10) === key);
    days.push(`<div class="calendar-day"><strong>${d.getMonth() + 1}/${d.getDate()}</strong>${posts.map(p => `<div class="calendar-dot">${escapeHtml(p.title || 'Post')}</div>`).join('')}</div>`);
  }
  el.innerHTML = days.join('');
}

function renderPublishAnalytics() {
  const count = s => publishPosts.filter(p => p.status === s).length;
  if ($('anaScheduled')) $('anaScheduled').textContent = count('SCHEDULED');
  if ($('anaPosted')) $('anaPosted').textContent = count('POSTED');
  if ($('anaFailed')) $('anaFailed').textContent = count('FAILED');
}

function updateSeoAnalyticsContextControls() {
  const toggle = $('seoUseAnalyticsContext');
  const status = $('seoAnalyticsContextStatus');
  if (!toggle || !status) return;
  const hasContext = !!latestAnalyticsContext;
  toggle.disabled = !hasContext;
  if (!hasContext) {
    toggle.checked = false;
    status.textContent = "Upload analytics first if you want SEO ranked against your channel's proven winners.";
    return;
  }
  const top = latestAnalyticsContext.summary?.top_by_views?.[0]?.title || 'latest analytics import';
  status.textContent = `Available: ${top}. Turn this on to rank SEO by both search opportunity and channel fit.`;
}

function renderAnalyticsAnalysis(data) {
  const a = data.analysis || {};
  const section = (title, items) => {
    const arr = Array.isArray(items) ? items : (items ? [items] : []);
    if (!arr.length) return '';
    return `<div class="publish-item"><strong>${escapeHtml(title)}</strong><ul style="margin:8px 0 0 18px; color:var(--muted);">${arr.map(x => `<li>${escapeHtml(typeof x === 'string' ? x : JSON.stringify(x))}</li>`).join('')}</ul></div>`;
  };
  $('analyticsResult').innerHTML = `
    <div class="publish-item">
      <strong>Diagnosis</strong>
      <div class="hint">${escapeHtml(a.diagnosis || 'No diagnosis returned.')}</div>
      <div class="hint">Rows analyzed: ${escapeHtml(String(data.summary?.rows || 0))}</div>
    </div>
    ${section('Winning patterns', a.winning_patterns)}
    ${section('Problems', a.problems)}
    ${section('Next video rules', a.next_video_rules)}
    ${section('Hook guidance', a.hook_guidance)}
    ${section('Title guidance', a.title_guidance)}
    ${section('SEO keywords', a.seo_keywords)}
    ${section('Content angles', a.content_angles)}
    ${section('Posting guidance', a.posting_guidance)}
    ${section('Experiments', a.experiments)}
  `;
  updateSeoAnalyticsContextControls();
}

function renderSeoResults(data) {
  const rows = data.keywords || [];
  if (!rows.length) {
    $('seoResult').innerHTML = '<div class="publish-card"><div class="hint">No keyword opportunities came back. Try a broader niche phrase.</div></div>';
    return;
  }
  const contextCard = data.analytics_context_used ? `
    <div class="publish-card">
      <div class="make-section-title"><strong>Analytics context active</strong><span>SEO + channel fit</span></div>
      <div class="hint">Ranking now blends YouTube search opportunity with your uploaded channel winners and weak spots.</div>
      ${(data.analytics_context_summary?.top_titles || []).slice(0, 3).map(t => `<div class="hint">Winner signal: ${escapeHtml(t)}</div>`).join('')}
    </div>` : '';
  const packageCard = data.mode === 'ranked' ? `
    <div class="publish-card">
      <div class="make-section-title"><strong>Best package</strong><span>${escapeHtml(data.niche || '')}</span></div>
      <div class="publish-pill-row">
        ${(data.tags || []).slice(0, 12).map(t => `<button class="publish-pill" type="button" data-seo-tag="${escapeHtml(t)}">${escapeHtml(t)}</button>`).join('')}
      </div>
      <div class="hint" style="margin-top:10px;">Use the top 3-5 phrases naturally in the title, first two description lines, tags, and spoken script. Do not stuff every phrase everywhere.</div>
      <div class="script-actions" style="margin-top:12px;">
        <button id="seoApplyToPublishBtn" class="btn secondary" type="button">Apply to Publish tags</button>
        <button id="seoApplyToMakeBtn" class="btn secondary" type="button">Use in Make Video</button>
      </div>
    </div>` : '';
  $('seoResult').innerHTML = contextCard + packageCard + rows.map(row => {
    const score = row.opportunityScore == null ? 'Candidate' : `${row.opportunityScore}/100`;
    const fit = row.channelFitScore == null ? '' : ` - Channel fit ${row.channelFitScore}/100`;
    const meta = row.opportunityScore == null
      ? escapeHtml(row.why || 'Candidate phrase')
      : `Demand ${row.demandScore}/100 - Velocity ${row.velocityScore}/100 - Shorts fit ${row.shortsFitScore}/100 - Competition penalty ${row.competitionPenalty}/100${fit}`;
    const reasons = (row.analyticsFitReasons || []).slice(0, 2).map(r => `<div class="hint">Analytics fit: ${escapeHtml(r)}</div>`).join('');
    const videos = (row.topVideos || []).slice(0, 3).map(v => `
      <div class="hint">• ${escapeHtml(v.title || 'Video')} · ${Number(v.views || 0).toLocaleString()} views · ${Number(v.viewsPerDay || 0).toLocaleString()} views/day</div>
    `).join('');
    return `
      <div class="publish-card">
        <div class="make-section-title"><strong>${escapeHtml(row.phrase || '')}</strong><span>${score}</span></div>
        <div class="hint">${meta}</div>
        ${reasons}
        ${row.medianViews != null ? `<div class="status-grid" style="margin-top:12px;">
          <div class="stat"><div class="label">Median views</div><div class="value" style="font-size:16px;">${Number(row.medianViews || 0).toLocaleString()}</div></div>
          <div class="stat"><div class="label">Views / day</div><div class="value" style="font-size:16px;">${Number(row.medianViewsPerDay || 0).toLocaleString()}</div></div>
          <div class="stat"><div class="label">Shorts fit</div><div class="value" style="font-size:16px;">${Math.round((row.shortsRatio || 0) * 100)}%</div></div>
        </div>` : ''}
        ${videos ? `<div style="margin-top:10px;">${videos}</div>` : ''}
      </div>`;
  }).join('');
  $('seoApplyToPublishBtn')?.addEventListener('click', () => {
    $('publishTags').value = (data.tags || []).join(' ');
    $('publishCaption').value = ($('publishCaption').value.trim() + '\n\n' + (data.hashtags || []).join(' ')).trim();
    updatePublishPreview();
    toast('SEO phrases applied to Publish');
  });
  $('seoApplyToMakeBtn')?.addEventListener('click', () => {
    $('makeNiche').value = data.niche || $('seoNicheInput').value.trim();
    const phrases = (data.title_phrases || data.tags || []).slice(0, 5).join(', ');
    $('makeDesc').value = [`YouTube SEO target phrases: ${phrases}.`, $('makeDesc').value.trim()].filter(Boolean).join('\n\n');
    updateMakeReadiness();
    toast('SEO phrases added to Make Video');
  });
}

async function runSeoResearch() {
  const niche = $('seoNicheInput').value.trim();
  if (!niche) { toast('Type any niche or product first', true); return; }
  $('seoResearchBtn').disabled = true;
  const useAnalytics = $('seoUseAnalyticsContext')?.checked && latestAnalyticsContext;
  $('seoResearchStatus').textContent = useAnalytics
    ? 'Expanding phrases, checking YouTube opportunity, and weighting against your analytics...'
    : 'Expanding phrases with Ollama and checking YouTube opportunity...';
  $('seoResult').innerHTML = '';
  try {
    const d = await apiJson('/api/research/youtube/seo', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        niche,
        model: $('makeModel')?.value.trim() || '',
        max_phrases: 12,
        analytics_context: $('seoUseAnalyticsContext')?.checked ? latestAnalyticsContext : null,
      }),
    });
    $('seoResearchStatus').textContent = d.mode === 'ranked'
      ? `Checked ${d.candidates_checked || 0} phrases. Sorted by best ranking opportunity${d.analytics_context_used ? ' + channel fit.' : '.'}`
      : d.message || 'Candidate phrases generated.';
    renderSeoResults(d);
  } catch (e) {
    $('seoResearchStatus').textContent = '';
    toast(e.message || 'SEO research failed', true);
  } finally {
    $('seoResearchBtn').disabled = false;
  }
}

async function analyzeYouTubeAnalyticsUpload() {
  const file = $('analyticsUploadFile').files && $('analyticsUploadFile').files[0];
  if (!file) { toast('Upload a YouTube analytics CSV first', true); return; }
  const fd = new FormData();
  fd.append('file', file);
  fd.append('model', $('makeModel')?.value.trim() || '');
  $('analyticsAnalyzeBtn').disabled = true;
  $('analyticsUploadStatus').textContent = `Analyzing ${file.name} with Ollama...`;
  $('analyticsResult').innerHTML = '';
  try {
    const r = await fetch('/api/publish/analytics/analyze', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok || d.ok === false) throw new Error(d.error || 'analysis failed');
    latestAnalyticsContext = d;
    $('analyticsUploadStatus').textContent = 'Analysis complete. Use these findings to steer future Phantomline ideas and titles.';
    renderAnalyticsAnalysis(d);
  } catch (e) {
    $('analyticsUploadStatus').textContent = '';
    toast(e.message || 'Analytics analysis failed', true);
  } finally {
    $('analyticsAnalyzeBtn').disabled = false;
  }
}

function renderPublishMedia() {
  const el = $('publishMediaList');
  if (!el) return;
  el.innerHTML = publishVideos.length ? publishVideos.map(v => `
    <div class="publish-item"><strong>${escapeHtml(v.title || 'Video')}</strong><div class="hint">${escapeHtml(v.id)}</div></div>
  `).join('') : '<div class="hint">No rendered videos yet.</div>';
}

async function loadPublishTemplates() {
  const d = await apiJson('/api/publish/templates');
  $('templateList').innerHTML = (d.templates || []).length ? (d.templates || []).map(t => `
    <div class="publish-item"><strong>${escapeHtml(t.name)}</strong><div class="hint">${escapeHtml(t.title || '')}</div></div>
  `).join('') : '<div class="hint">No templates yet.</div>';
}

async function loadPublishRecurring() {
  const d = await apiJson('/api/publish/recurring');
  $('recurringList').innerHTML = (d.recurring || []).length ? (d.recurring || []).map(r => `
    <div class="publish-item"><strong>${escapeHtml(r.name)}</strong><div class="hint">${escapeHtml(r.time)} - ${escapeHtml((r.days || []).join(', '))}</div></div>
  `).join('') : '<div class="hint">No recurring slots yet.</div>';
}

// Connect YouTube routing. Two paths:
// 1. Single-OAuth (PRIORITY 5). hosted user with a Supabase session
//    in localStorage. Opens /account?yt-connect=1 in a popup. The
//    /account JS calls supabase.auth.signInWithOAuth with YouTube
//    scopes (incremental authorization), captures the resulting
//    provider_token, POSTs it to /api/youtube/store-token. Server
//    upserts into user_youtube_tokens (RLS-scoped). Per-user channel.
// 2. Legacy file-based flow. local install with YOUTUBE_CLIENT_ID +
//    YOUTUBE_CLIENT_SECRET in .env. Opens /api/youtube/connect which
//    redirects to Google's OAuth screen. Single shared connection
//    file, fine for a single-user desktop install.
// We pick path 1 if there's a Supabase session in localStorage AND
// the studio fetch shim (_phantomlineAuthToken) returns a token.
// that means store-token will be authenticated. Otherwise legacy.
function _routeYouTubeConnect() {
  const token = (typeof _phantomlineAuthToken === 'function')
    ? _phantomlineAuthToken() : null;
  if (token) {
    // Open the incremental-auth flow in a small popup. We listen for
    // the "phantomline:yt-connected" postMessage to refresh status.
    const w = window.open(
      '/account?yt-connect=1',
      'phantomline-yt-connect',
      'width=520,height=720,toolbar=no,menubar=no'
    );
    if (!w) {
      // Popup blocked. fall back to opening in a new tab.
      window.open('/account?yt-connect=1', '_blank');
    }
    return;
  }
  // Legacy local-install path.
  window.open('/api/youtube/connect', '_blank');
}
$('publishConnectYouTubeBtn').addEventListener('click', _routeYouTubeConnect);
$('connectionsYoutubeBtn').addEventListener('click', _routeYouTubeConnect);
// Refresh publish status when the popup posts back saying YT was connected.
window.addEventListener('message', (event) => {
  if (event.origin !== window.location.origin) return;
  if (event.data?.type === 'phantomline:yt-connected') {
    if (typeof loadPublishStatus === 'function') loadPublishStatus();
  }
});
$('publishVideoProject').addEventListener('change', updatePublishPreview);
$('publishTitle').addEventListener('input', updatePublishPreview);

/* -------- Thumbnail generator (Publish > Compose) ----------------------- */
/* Generates 4 thumbnail variants from the publishTitle, lets user pick one,
 * stores the selection on window so the publish payload can attach it.
 * Uses /api/thumbnail/{presets,batch}; falls back to PIL composition if no
 * AI image source is reachable (server side handles that). */
let _thumbPresetsLoaded = false;
async function _loadThumbPresets() {
  if (_thumbPresetsLoaded) return;
  try {
    const r = await fetch('/api/thumbnail/presets');
    const d = await r.json();
    if (d.ok && Array.isArray(d.presets)) {
      const sel = $('thumbStyle');
      d.presets.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.key;
        opt.textContent = p.label;
        sel.appendChild(opt);
      });
    }
  } catch (e) { /* keep auto-only dropdown */ }
  _thumbPresetsLoaded = true;
}

window.selectedThumbnail = null; // {b64, mode, preset, width, height, seed}

function _renderThumbVariants(variants) {
  // Single-thumbnail mode: render the first (and only) variant full-width
  // and auto-attach it. User clicks Regenerate to roll a new one. This
  // beats the old 4-at-once flow because each call now gets full timeout
  // budget against Pollinations FLUX-realism so all four don't degrade
  // into PIL fallbacks under rate-limit pressure.
  const grid = $('thumbVariantsGrid');
  if (!variants.length) {
    grid.innerHTML = '<div class="hint">No image returned. Click Regenerate.</div>';
    return;
  }
  const v = variants[0];
  grid.innerHTML = `
    <div style="border:2px solid #22E7F5; border-radius:10px; overflow:hidden; background:#0a1722;">
      <img src="data:image/png;base64,${v.png_b64}" alt="Generated thumbnail"
        style="width:100%; height:auto; display:block;" />
      <div style="padding:8px 12px; font-size:12px; color:#7a96ad; display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap;">
        <span>${escapeHtml(v.preset || 'auto')} · ${escapeHtml(v.mode || '')}${v.seed ? ` · seed ${v.seed}` : ''}</span>
        <span style="color:${v.mode === 'pil_fallback' ? '#e0a019' : '#22E7F5'};">
          ${v.mode === 'pil_fallback' ? 'AI image source unreachable. Text-only fallback. Try Regenerate.' : 'Attached to post'}
        </span>
      </div>
    </div>
  `;
  // Auto-attach the rendered thumbnail
  window.selectedThumbnail = {
    b64: v.png_b64, mode: v.mode, preset: v.preset,
    width: v.width, height: v.height, seed: v.seed,
  };
  $('thumbSelectedRow').style.display = '';
  $('thumbSelectedPreview').src = `data:image/png;base64,${v.png_b64}`;
  $('thumbSelectedMeta').textContent =
    `${v.width}x${v.height} · ${v.preset || 'auto'} · ${v.mode}${v.fallback_reason ? ' (' + v.fallback_reason + ')' : ''}`;
  $('thumbStatus').textContent = v.mode === 'pil_fallback' ? 'Fallback (regenerate)' : 'Attached';
  // Reveal the Regenerate button now that we have something to roll over
  const regenBtn = $('thumbRegenerateBtn');
  if (regenBtn) regenBtn.style.display = '';
}

async function generateThumbnails(opts) {
  opts = opts || {};
  const isRegen = !!opts.regenerate;
  const title = ($('publishTitle').value || '').trim();
  const projectId = ($('publishVideoProject')?.value || '').trim();
  const status = $('thumbStatusLine');
  // Title comes from Create Video which autofills here, so missing title
  // also means no video has been selected. That's the gate.
  if (!projectId && !title) {
    toast('Pick a finished video above first', true);
    $('publishVideoProject')?.focus();
    return;
  }
  const btn = isRegen ? $('thumbRegenerateBtn') : $('thumbGenerateBtn');
  btn.disabled = true;
  const originalLabel = btn.textContent;
  btn.textContent = isRegen ? 'Regenerating...' : 'Generating...';
  status.textContent = projectId
    ? 'Reading your script + scenes, then rendering one full-quality thumbnail (30-60 sec).'
    : 'Rendering one thumbnail from your title (20-50 sec).';
  try {
    const r = await fetch('/api/thumbnail/batch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        title,
        aspect: $('thumbAspect').value || '16:9',
        style: 'auto',
        count: 1,  // single-shot for full quality
        // Server uses this to fetch the bundle's script + scene plan and
        // build a story-specific enhanced prompt instead of guessing from
        // the title alone.
        project_id: projectId,
        enhance_prompt: true,
      }),
    });
    const d = await r.json();
    if (!d.ok) {
      status.textContent = d.error || 'Generation failed.';
      toast(d.error || 'Thumbnail generation failed', true);
      return;
    }
    const fromScript = d.script_used ? ' (grounded in your script)' : '';
    status.textContent = `Thumbnail ready${fromScript}. Don't like it? Click Regenerate.`;
    _renderThumbVariants(d.variants || []);
  } catch (e) {
    status.textContent = e.message || 'Network error.';
    toast('Thumbnail request failed: ' + (e.message || e), true);
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}
$('thumbGenerateBtn').addEventListener('click', () => generateThumbnails());
$('thumbRegenerateBtn')?.addEventListener('click', () => generateThumbnails({ regenerate: true }));
$('thumbDownloadBtn').addEventListener('click', () => {
  if (!window.selectedThumbnail) return;
  const a = document.createElement('a');
  a.href = `data:image/png;base64,${window.selectedThumbnail.b64}`;
  const safeTitle = ($('publishTitle').value || 'thumbnail').replace(/[^a-z0-9_-]+/gi, '_').slice(0, 60);
  a.download = `${safeTitle}.png`;
  document.body.appendChild(a); a.click(); a.remove();
});
// Lazy-load presets when the user first opens the Publish tab.
document.querySelector('.tab-btn[data-tab="publish"]')?.addEventListener('click', _loadThumbPresets);
/* ------------------------------------------------------------------------ */

$('publishCaption').addEventListener('input', updatePublishPreview);
$('publishTags').addEventListener('input', updatePublishPreview);
$('publishScheduledAt').addEventListener('input', renderPublishReadinessChecklist);
$('publishScheduleBtn').addEventListener('click', schedulePublishPost);
$('publishNewPostBtn').addEventListener('click', () => {
  $('publishVideoProject').value = '';
  $('publishTitle').value = '';
  $('publishCaption').value = '';
  $('publishTags').value = '';
  $('publishPinnedComment').value = '';
  updatePublishPreview();
});
$('publishSaveTemplateBtn').addEventListener('click', async () => {
  await apiJson('/api/publish/templates', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: $('publishTitle').value.trim() || 'Phantomline template',
      title: $('publishTitle').value.trim(),
      caption: $('publishCaption').value.trim(),
      tags: $('publishTags').value.trim(),
      privacy: $('publishPrivacy').value,
    }),
  });
  toast('Template saved');
});
$('recurringSaveBtn').addEventListener('click', async () => {
  await apiJson('/api/publish/recurring', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ name: $('recurringName').value.trim(), time: $('recurringTime').value }),
  });
  await loadPublishRecurring();
  toast('Recurring slot saved');
});
$('analyticsAnalyzeBtn').addEventListener('click', analyzeYouTubeAnalyticsUpload);
$('seoResearchBtn').addEventListener('click', runSeoResearch);
$('seoNicheInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') runSeoResearch();
});
updateSeoAnalyticsContextControls();
document.querySelectorAll('[data-schedule-quick]').forEach(btn => {
  btn.addEventListener('click', () => {
    const d = new Date();
    if (btn.dataset.scheduleQuick === '1h') d.setHours(d.getHours() + 1);
    if (btn.dataset.scheduleQuick === 'tonight') { d.setHours(19, 0, 0, 0); if (d < new Date()) d.setDate(d.getDate() + 1); }
    if (btn.dataset.scheduleQuick === 'tomorrow') { d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0); }
    $('publishScheduledAt').value = localDateTimeValue(d);
    renderPublishReadinessChecklist();
  });
});
document.querySelectorAll('#publishHashtagPills .publish-pill').forEach(btn => {
  btn.addEventListener('click', () => {
    const tag = btn.textContent.replace('#', '').trim();
    const cur = $('publishTags').value.trim();
    $('publishTags').value = cur ? `${cur} ${tag}` : tag;
    updatePublishPreview();
  });
});
document.querySelector('.tab-btn[data-tab="publish"]').addEventListener('click', loadPublishWorkspace);

async function buildProductionKitFromShort() {
  const script = $('shortScriptText').value.trim();
  const title = $('shortTitleDisplay').textContent || 'Phantomline video';
  if (!script) {
    toast('Generate a short script first', true);
    return;
  }
  $('shortProductionBtn').disabled = true;
  $('shortProductionHint').style.display = '';
  try {
    $('shortProductionHint').textContent = '1/4 Creating scene prompts...';
    const planRes = await fetch('/api/video/plan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        script,
        title,
        workflow: 'image-to-video',
        aspect: $('videoAspect')?.value || '16:9',
        scene_seconds: 8,
        ...visualSettings('video'),
      }),
    });
    const planData = await planRes.json();
    if (!planData.ok) throw new Error(planData.error || 'video plan failed');
    renderVideoPlan(planData.plan, planData.project);

    $('shortProductionHint').textContent = '2/4 Narrating locally with Kokoro...';
    const ttsRes = await fetch('/api/tts/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        text: script,
        title,
        voice: $('voice')?.value || 'af_nicole',
        speed: 1.0,
        format: 'mp3',
      }),
    });
    const ttsData = await ttsRes.json();
    if (!ttsData.ok) throw new Error(ttsData.error || 'narration failed');
    const narrationProjectId = await waitForTtsProject(ttsData.job_id, msg => {
      $('shortProductionHint').textContent = '2/4 Narrating locally with Kokoro.. ' + msg;
    });

    $('shortProductionHint').textContent = '3/4 Aligning scene timing to narration...';
    const tlRes = await fetch('/api/video/timeline', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        plan_project_id: planData.project.id,
        narration_project_id: narrationProjectId,
      }),
    });
    const tlData = await tlRes.json();
    if (!tlData.ok) throw new Error(tlData.error || 'timeline failed');
    renderTimeline(tlData.timeline, tlData.project);

    $('shortProductionHint').textContent = '4/4 Rendering draft MP4 with narration attached...';
    document.querySelector('.tab-btn[data-tab="video"]').click();
    const videoJobId = await renderDraftVideoFromCurrentTimeline();
    await waitForDraftVideo(videoJobId, msg => {
      $('shortProductionHint').textContent = '4/4 Rendering draft MP4 with narration attached.. ' + msg;
    });
    $('shortProductionHint').textContent = 'Production kit created. Open Video Studio or Library to download the MP4.';
  } catch (e) {
    $('shortProductionHint').textContent = e.message || 'Production kit failed';
    toast(e.message || 'Production kit failed', true);
  } finally {
    $('shortProductionBtn').disabled = false;
  }
}

async function waitForTtsProject(jobId, onStatus) {
  for (;;) {
    if (typeof _makeRenderAbortIfNeeded === 'function') _makeRenderAbortIfNeeded();
    const d = await apiJson('/api/tts/status/' + jobId);
    const j = d.job;
    const total = j.chars_total || 1;
    const pct = Math.min(100, Math.round(((j.chars_done || 0) / total) * 100));
    if (onStatus) onStatus((j.status || 'working') + ' ' + pct + '%');
    if (j.error) throw new Error(j.error);
    if (j.done && j.project_id) return j.project_id;
    await new Promise(resolve => setTimeout(resolve, 1500));
  }
}

$('shortProductionBtn').addEventListener('click', buildProductionKitFromShort);

// ---------- Music & Mix tab ----------
const MUSIC_PRESETS = [
  { label: "Calm cinematic ambient",
    prompt: "calm cinematic ambient pad, distant piano, low strings, soft breath of warm reverb, no drums, sleep-friendly, slow tempo" },
  { label: "Deep space drone",
    prompt: "deep space ambient, slow synth pad, sub-bass drone, ethereal, distant chimes, no rhythm" },
  { label: "Dark ocean mystery",
    prompt: "dark ocean ambient, low rumble, distant sonar pings, foreboding, minimal melody, underwater reverb" },
  { label: "Eerie sparse piano",
    prompt: "eerie sparse piano, single notes, slow strings, suspenseful, nighttime, minimal, calm dread" },
  { label: "Cosmic horror drone",
    prompt: "cosmic horror drone, dissonant strings, low pulsing bass, atmospheric, unsettling, slow crescendos, no melody" },
  { label: "Lo-fi sleep beats",
    prompt: "lo-fi ambient, soft piano, vinyl crackle, melancholy chord progression, slow tempo, no vocals" },
  { label: "Forest at night",
    prompt: "ambient nature pad, soft cello, distant owl, gentle wind, calming, sleep-friendly, no drums" },
  { label: "Abandoned town hum",
    prompt: "abandoned town ambient, low electric hum, distant wind, faint music box, eerie stillness" },
];

(function buildMusicChips() {
  const wrap = document.getElementById('musicChips');
  if (!wrap) return;
  for (const p of MUSIC_PRESETS) {
    const el = document.createElement('span');
    el.className = 'tag';
    el.textContent = p.label;
    el.title = p.prompt;
    el.addEventListener('click', () => {
      $('musicPrompt').value = p.prompt;
      wrap.querySelectorAll('.tag').forEach(t => t.classList.remove('active'));
      el.classList.add('active');
    });
    wrap.appendChild(el);
  }
})();

let musicJob = null;
let musicTimer = null;

$('musicBtn').addEventListener('click', async () => {
  const body = {
    prompt: $('musicPrompt').value.trim(),
    minutes: parseFloat($('musicMinutes').value) || 60,
    model_size: $('musicModelSize').value,
    fade_seconds: parseFloat($('musicFade').value) || 2,
    format: $('musicFmt').value,
    name: $('musicName').value.trim() || 'music_bed',
  };
  if (!body.prompt) { toast('Enter a music prompt or pick a preset', true); return; }

  $('musicBtn').disabled = true;
  $('musicDownloadBtn').disabled = true;
  $('musicAudio').style.display = 'none';
  $('musicAudio').removeAttribute('src');
  $('musicPanel').style.display = 'block';
  $('musicStatus').textContent = 'starting…';
  $('musicLen').textContent = body.minutes + ' min';
  $('musicModelStat').textContent = body.model_size;
  $('musicProgress').style.width = '0%';
  $('musicLog').innerHTML = '';
  $('musicPathHint').textContent = '';

  try {
    const r = await fetch('/api/music/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'failed to start');
    musicJob = d.job_id;
    if (musicTimer) clearInterval(musicTimer);
    musicTimer = setInterval(pollMusic, 1500);
    pollMusic();
  } catch (e) {
    toast(e.message || 'Music failed to start', true);
    $('musicBtn').disabled = false;
    $('musicStatus').textContent = 'error';
  }
});

async function pollMusic() {
  if (!musicJob) return;
  try {
    const r = await fetch('/api/music/status/' + musicJob);
    const d = await r.json();
    if (!d.ok) return;
    const j = d.job;
    $('musicStatus').textContent = j.status || '-';

    // Fake progress: bump bar based on phase since exact tokens are slow to surface.
    const status = (j.status || '').toLowerCase();
    let pct = 0;
    if (status.includes('loading')) pct = 10;
    else if (status.includes('composing')) pct = 35;
    else if (status.includes('crossfade')) pct = 70;
    else if (status.includes('encoding')) pct = 90;
    else if (j.done && !j.error) pct = 100;
    $('musicProgress').style.width = pct + '%';

    // Log
    if (j.log && j.log.length) {
      $('musicLog').innerHTML = j.log.slice(-60).map(l =>
        `<span class="line"><span class="t">${fmt(l.t)}</span>${escapeHtml(l.msg)}</span>`
      ).join('');
      $('musicLog').scrollTop = $('musicLog').scrollHeight;
    }

    if (j.error) toast('Music error: ' + j.error, true);
    if (j.done) {
      clearInterval(musicTimer); musicTimer = null;
      $('musicBtn').disabled = false;
      if (!j.error) {
        $('musicStatus').textContent = 'done';
        $('musicProgress').style.width = '100%';
        const audio = $('musicAudio');
        audio.src = '/api/music/download/' + musicJob;
        audio.style.display = 'block';
        $('musicDownloadBtn').disabled = false;
        $('musicPathHint').textContent = j.audio_path ? ('saved to ' + j.audio_path) : '';
        refreshMixDropdowns();
      }
    }
  } catch (e) { /* keep polling */ }
}

$('musicDownloadBtn').addEventListener('click', () => {
  if (!musicJob) return;
  window.location = '/api/music/download/' + musicJob;
});

// ---- Mix narration + music ----
let mixJob = null;
let mixTimer = null;
let uploadedNarrationPath = null;

$('mixDuck').addEventListener('input', (e) => {
  $('mixDuckVal').textContent = e.target.value + ' dB';
});

$('mixUploadFile').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const sizeMb = (file.size / (1024 * 1024)).toFixed(1);
  $('mixUploadStatus').textContent = `Uploading ${file.name} (${sizeMb} MB)…`;
  try {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/api/upload/narration', { method: 'POST', body: fd });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'upload failed');
    uploadedNarrationPath = d.path;
    $('mixUploadStatus').innerHTML =
      `<strong style="color:var(--text);">Using uploaded:</strong> ${escapeHtml(d.original_name)} (${d.size_mb} MB)`;
    $('mixClearUploadBtn').style.display = '';
    // Disable the dropdown to make it visually obvious which input wins.
    $('mixTts').disabled = true;
    $('mixTts').style.opacity = 0.5;
  } catch (err) {
    uploadedNarrationPath = null;
    $('mixUploadStatus').textContent = 'Upload failed: ' + (err.message || err);
  }
});

$('mixClearUploadBtn').addEventListener('click', () => {
  uploadedNarrationPath = null;
  $('mixUploadFile').value = '';
  $('mixUploadStatus').textContent = 'Supports MP3, WAV, FLAC, OGG, M4A, AAC. Up to 1 GB. If a file is uploaded, it overrides the dropdown above.';
  $('mixClearUploadBtn').style.display = 'none';
  $('mixTts').disabled = false;
  $('mixTts').style.opacity = 1;
});

async function refreshMixDropdowns() {
  try {
    const r = await fetch('/api/recent/jobs');
    const d = await r.json();
    const ttsSel = $('mixTts');
    const musSel = $('mixMusic');
    const prevTts = ttsSel.value;
    const prevMus = musSel.value;
    ttsSel.innerHTML = '<option value="">- none yet -</option>';
    for (const j of (d.tts || [])) {
      const opt = document.createElement('option');
      opt.value = j.id; opt.textContent = j.label;
      ttsSel.appendChild(opt);
    }
    musSel.innerHTML = '<option value="">- none yet -</option>';
    for (const j of (d.music || [])) {
      const opt = document.createElement('option');
      opt.value = j.id; opt.textContent = j.label;
      musSel.appendChild(opt);
    }
    if (prevTts) ttsSel.value = prevTts;
    if (prevMus) musSel.value = prevMus;
    // Auto-pick the most recent of each if none chosen.
    if (!ttsSel.value && (d.tts || []).length) ttsSel.value = d.tts[0].id;
    if (!musSel.value && (d.music || []).length) musSel.value = d.music[0].id;
  } catch (e) { /* ignore */ }
}

$('mixBtn').addEventListener('click', async () => {
  const ttsId = $('mixTts').value;
  const musId = $('mixMusic').value;
  if (!musId) { toast('Pick a background music track', true); return; }
  if (!uploadedNarrationPath && !ttsId) {
    toast('Pick a narration or upload one', true); return;
  }
  const body = {
    music_job_id: musId,
    music_db_below_speech: parseFloat($('mixDuck').value) || 18,
    format: 'mp3',
  };
  if (uploadedNarrationPath) {
    body.narration_path = uploadedNarrationPath;
    // Use the uploaded filename as the output stem.
    const fname = $('mixUploadFile').files[0]?.name || 'narration';
    body.name = fname.replace(/\.[^.]+$/, '');
  } else {
    body.tts_job_id = ttsId;
  }
  $('mixBtn').disabled = true;
  $('mixDownloadBtn').disabled = true;
  $('mixAudio').style.display = 'none';
  $('mixAudio').removeAttribute('src');
  $('mixPanel').style.display = 'block';
  $('mixStatus').textContent = 'starting…';
  $('mixPathHint').textContent = '';

  try {
    const r = await fetch('/api/mix/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'failed to mix');
    mixJob = d.job_id;
    if (mixTimer) clearInterval(mixTimer);
    mixTimer = setInterval(pollMix, 1000);
    pollMix();
  } catch (e) {
    toast(e.message || 'Mix failed', true);
    $('mixBtn').disabled = false;
    $('mixStatus').textContent = 'error';
  }
});

async function pollMix() {
  if (!mixJob) return;
  try {
    const r = await fetch('/api/mix/status/' + mixJob);
    const d = await r.json();
    if (!d.ok) return;
    const j = d.job;
    $('mixStatus').textContent = j.status || '-';
    if (j.error) toast('Mix error: ' + j.error, true);
    if (j.done) {
      clearInterval(mixTimer); mixTimer = null;
      $('mixBtn').disabled = false;
      if (!j.error) {
        $('mixStatus').textContent = 'done';
        const audio = $('mixAudio');
        audio.src = '/api/mix/download/' + mixJob;
        audio.style.display = 'block';
        $('mixDownloadBtn').disabled = false;
        $('mixPathHint').textContent = j.audio_path ? ('saved to ' + j.audio_path) : '';
      }
    }
  } catch (e) { /* keep polling */ }
}

$('mixDownloadBtn').addEventListener('click', () => {
  if (!mixJob) return;
  window.location = '/api/mix/download/' + mixJob;
});

// Refresh the mix dropdowns when the Music tab opens.
document.querySelector('.tab-btn[data-tab="music"]').addEventListener('click', refreshMixDropdowns);
document.querySelector('.tab-btn[data-tab="video"]').addEventListener('click', loadVideoAssets);
refreshMixDropdowns();
loadVideoAssets();

// ---------- Library tab ----------
let libFilter = 'bundle';  // bundles ("Videos") are the default Library view
const KIND_LABELS = {
  story: 'Long story',
  short_script: 'Short',
  narration: 'Narration',
  music: 'Music bed',
  mix: 'Final mix',
  upload: 'Uploaded',
  video_plan: 'Video plan',
  timeline: 'Timeline',
  video: 'Video',
};

function relTime(epoch) {
  if (!epoch) return '';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

async function loadLibrary() {
  const grid = $('libGrid');
  if (!grid) return;
  if (libFilter === 'bundle') {
    return loadLibraryBundles(grid);
  }
  try {
    const r = await fetch('/api/projects' + (libFilter !== 'all' ? '?kind=' + libFilter : ''));
    const d = await r.json();
    const items = (d.projects || []).filter(p => p.kind !== 'bundle');
    $('libCount').textContent = items.length + (items.length === 1 ? ' project' : ' projects');
    if (items.length === 0) {
      grid.innerHTML = `
        <div class="empty-state" style="grid-column:1/-1;">
          <div class="big">🗂</div>
          <h3>No projects yet</h3>
          <p>Generate a story, narrate it, score it. Each finished piece will land here.</p>
          <button class="btn" id="emptyStartBtn" style="width:auto; margin-top:0;">Start a story</button>
        </div>`;
      const btn = document.getElementById('emptyStartBtn');
      if (btn) btn.addEventListener('click', () => {
        document.querySelector('.tab-btn[data-tab="generate"]').click();
      });
      return;
    }
    grid.innerHTML = items.map(p => {
      const hasAudio = (p.files || {}).audio;
      const hasScript = (p.files || {}).script;
      const hasPrompts = (p.files || {}).prompts;
      const hasScenePlan = (p.files || {}).scene_plan;
      const hasTimeline = (p.files || {}).timeline;
      const hasEditList = (p.files || {}).edit_list;
      const hasVideo = (p.files || {}).video;
      const audioUrl = hasAudio ? `/api/projects/${p.id}/file/audio` : '';
      const scriptUrl = hasScript ? `/api/projects/${p.id}/file/script?download=1` : '';
      const kindLabel = KIND_LABELS[p.kind] || p.kind;
      const wc = p.word_count ? `${p.word_count.toLocaleString()} words · ` : '';
      return `
        <div class="proj-card" data-id="${p.id}">
          <div class="row1">
            <h3>${escapeHtml(p.title || 'Untitled')}</h3>
            <span class="kind-badge">${kindLabel}</span>
          </div>
          <div class="meta">${wc}${relTime(p.created_at)}</div>
          ${hasAudio ? `<audio controls src="${audioUrl}"></audio>` : ''}
          <div class="actions">
            ${hasAudio ? `<button class="iconbtn" data-act="dlAudio">Download MP3</button>` : ''}
            ${hasScript ? `<button class="iconbtn" data-act="dlScript">Download .txt</button>` : ''}
            ${hasPrompts ? `<button class="iconbtn" data-act="dlPrompts">Prompts</button>` : ''}
            ${hasScenePlan ? `<button class="iconbtn" data-act="dlPlan">Plan JSON</button>` : ''}
            ${hasTimeline ? `<button class="iconbtn" data-act="dlTimeline">Timeline</button>` : ''}
            ${hasEditList ? `<button class="iconbtn" data-act="dlEditList">Edit list</button>` : ''}
            ${hasVideo ? `<button class="iconbtn" data-act="dlVideo">Download MP4</button>` : ''}
            <button class="iconbtn danger" data-act="del">Delete</button>
          </div>
        </div>`;
    }).join('');

    // Wire actions per card.
    grid.querySelectorAll('.proj-card').forEach(card => {
      const id = card.dataset.id;
      card.querySelectorAll('.iconbtn').forEach(b => {
        b.addEventListener('click', async () => {
          const act = b.dataset.act;
          if (act === 'dlAudio') window.location = `/api/projects/${id}/file/audio?download=1`;
          else if (act === 'dlScript') window.location = `/api/projects/${id}/file/script?download=1`;
          else if (act === 'dlPrompts') window.location = `/api/projects/${id}/file/prompts?download=1`;
          else if (act === 'dlPlan') window.location = `/api/projects/${id}/file/scene_plan?download=1`;
          else if (act === 'dlTimeline') window.location = `/api/projects/${id}/file/timeline?download=1`;
          else if (act === 'dlEditList') window.location = `/api/projects/${id}/file/edit_list?download=1`;
          else if (act === 'dlVideo') window.location = `/api/projects/${id}/file/video?download=1`;
          else if (act === 'del') optimisticDelete(card, id);
        });
      });
    });
  } catch (e) {
    grid.innerHTML = '<div class="empty-state"><h3>Could not load library</h3></div>';
  }
}

// ---------- Bundles view: one card per Make Video session ----------

async function loadLibraryBundles(grid) {
  try {
    const r = await fetch('/api/bundles');
    const d = await r.json();
    const bundles = d.bundles || [];
    $('libCount').textContent = bundles.length + (bundles.length === 1 ? ' video' : ' videos');
    if (bundles.length === 0) {
      grid.innerHTML = `
        <div class="empty-state" style="grid-column:1/-1;">
          <div class="big">🎬</div>
          <h3>No videos yet</h3>
          <p>Render your first video in Create Video and it will appear here as one bundle. script, audio, MP4, and publish draft, all together.</p>
          <button class="btn" id="emptyStartBundleBtn" style="width:auto; margin-top:0;">Open Create Video</button>
        </div>`;
      document.getElementById('emptyStartBundleBtn')?.addEventListener('click', () => {
        document.querySelector('.tab-btn[data-tab="make"]')?.click();
      });
      return;
    }
    grid.innerHTML = bundles.map(b => {
      const children = b.children || {};
      const video = children.video || null;
      const script = children.script || null;
      const mix = children.mix || null;
      const narration = children.narration || null;
      const sourceVideo = children.source_video || null;
      const videoUrl = video ? `/api/projects/${video.id}/file/video?inline=1` : '';
      const memberPills = [
        video ? '<span class="kind-badge">MP4</span>' : '',
        (mix || narration) ? '<span class="kind-badge">Audio</span>' : '',
        script ? '<span class="kind-badge">Script</span>' : '',
        sourceVideo ? '<span class="kind-badge">Source</span>' : '',
      ].filter(Boolean).join('');
      const params = b.params || {};
      const tagline = (params.preferredTitle || params.title || params.topic || '').toString().slice(0, 140);
      return `
        <div class="proj-card bundle-card" data-id="${b.id}">
          <div class="row1">
            <h3>${escapeHtml(b.title || 'Untitled video')}</h3>
            <span class="kind-badge" style="background: rgba(34, 231, 245,0.18); color: var(--accent);">Bundle</span>
          </div>
          <div class="meta">${memberPills} · ${relTime(b.created_at)}</div>
          ${tagline ? `<div class="bundle-tagline" style="color:var(--muted); font-size:13px; margin-top:6px;">${escapeHtml(tagline)}</div>` : ''}
          ${video ? `<video controls preload="metadata" src="${videoUrl}" style="margin-top:10px; width:100%; max-height:280px; border-radius:10px;"></video>` : ''}
          <div class="actions">
            <button class="iconbtn" data-act="reopen">Reopen in Create Video</button>
            ${video ? `<button class="iconbtn" data-act="dlVideo" data-pid="${video.id}">Download MP4</button>` : ''}
            ${mix ? `<button class="iconbtn" data-act="dlAudio" data-pid="${mix.id}">Download MP3</button>` : (narration ? `<button class="iconbtn" data-act="dlAudio" data-pid="${narration.id}">Download MP3</button>` : '')}
            ${script ? `<button class="iconbtn" data-act="dlScript" data-pid="${script.id}">Download .txt</button>` : ''}
            <button class="iconbtn" data-act="openPublish">Publish draft</button>
            <button class="iconbtn danger" data-act="delBundle">Remove</button>
          </div>
        </div>`;
    }).join('');

    grid.querySelectorAll('.bundle-card').forEach(card => {
      const id = card.dataset.id;
      card.querySelectorAll('.iconbtn').forEach(b => {
        b.addEventListener('click', async () => {
          const act = b.dataset.act;
          const pid = b.dataset.pid;
          if (act === 'dlVideo' && pid) window.location = `/api/projects/${pid}/file/video?download=1`;
          else if (act === 'dlAudio' && pid) window.location = `/api/projects/${pid}/file/audio?download=1`;
          else if (act === 'dlScript' && pid) window.location = `/api/projects/${pid}/file/script?download=1`;
          else if (act === 'openPublish') {
            // Find the bundle's video child and pre-select it in publish.
            try {
              const r2 = await fetch(`/api/bundles/${id}`);
              const data = await r2.json();
              const bundle = data.bundle;
              const videoId = bundle?.children?.video?.id;
              document.querySelector('.tab-btn[data-tab="publish"]')?.click();
              if (videoId) {
                setTimeout(() => {
                  const sel = document.getElementById('publishVideoProject');
                  if (sel) {
                    sel.value = videoId;
                    sel.dispatchEvent(new Event('change'));
                  }
                }, 200);
              }
            } catch {}
          }
          else if (act === 'reopen') reopenBundle(id);
          else if (act === 'delBundle') {
            if (!confirm('Remove this bundle? Individual artifacts (script, audio, MP4) stay in "All artifacts".')) return;
            try {
              await fetch(`/api/bundles/${id}`, { method: 'DELETE' });
              card.remove();
              loadLibrary();
            } catch { toast('Could not remove bundle', true); }
          }
        });
      });
    });
  } catch (e) {
    grid.innerHTML = '<div class="empty-state"><h3>Could not load videos</h3></div>';
  }
}

async function reopenBundle(bundleId) {
  try {
    const r = await fetch(`/api/bundles/${bundleId}`);
    const d = await r.json();
    if (!d.ok || !d.bundle) {
      toast('Could not reopen bundle', true);
      return;
    }
    const params = d.bundle.params || {};
    // Switch to Create Video tab.
    document.querySelector('.tab-btn[data-tab="make"]')?.click();
    // Wait for tab to render, then pre-fill form fields from saved params.
    setTimeout(() => {
      const setIfExists = (id, value) => {
        const el = document.getElementById(id);
        if (el && value !== undefined && value !== null && value !== '') el.value = value;
      };
      setIfExists('makeTopic', params.topic);
      setIfExists('makePreferredTitle', params.preferredTitle || params.title);
      setIfExists('makeNiche', params.niche);
      setIfExists('makeAudience', params.audience);
      setIfExists('makeFormat', params.format);
      setIfExists('makeRecipe', params.recipe);
      setIfExists('makeHookStyle', params.hookStyle);
      setIfExists('makeTensionFormat', params.tensionFormat);
      setIfExists('makeLoopType', params.loopType);
      setIfExists('makeTone', params.tone);
      setIfExists('makeVideoMode', params.videoMode);
      setIfExists('makeDuration', params.duration);
      setIfExists('makeAspect', params.aspect);
      setIfExists('makeCaptions', params.captions);
      setIfExists('makeCaptionStyle', params.captionStyle);
      setIfExists('makeVoice', params.voice);
      setIfExists('makeMusicPrompt', params.musicPrompt);
      setIfExists('makeVisualPreset', params.visualPreset);
      setIfExists('makeVisualStyle', params.visualStyle);
      setIfExists('makeAmbience', params.visualAmbience);
      setIfExists('makeVisualCharacter', params.visualCharacter);
      setIfExists('makePinnedComment', params.pinnedComment);
      setIfExists('makeHashtags', params.hashtags);
      // Restore selected idea if present so prompts have full context.
      if (params.selectedIdea && typeof window.makeSelectedIdea !== 'undefined') {
        window.makeSelectedIdea = params.selectedIdea;
      }
      // Trigger downstream handlers that watch form changes.
      ['makeVideoMode', 'makeDuration', 'makeFormat', 'makeRecipe'].forEach(id => {
        document.getElementById(id)?.dispatchEvent(new Event('change'));
      });
      if (typeof updateMakeReadiness === 'function') updateMakeReadiness();
      toast('Bundle reopened. Edit anything and re-render.');
    }, 200);
  } catch {
    toast('Could not reopen bundle', true);
  }
}

// Optimistic delete with undo toast. Hides the card, then if the user
// hasn't clicked Undo within 5 seconds, fires the actual DELETE.
function optimisticDelete(card, id) {
  card.style.transition = 'opacity 0.2s, transform 0.2s';
  card.style.opacity = '0.0';
  card.style.transform = 'scale(0.97)';
  const undoToast = showUndoToast(`Deleted "${card.querySelector('h3')?.textContent || 'project'}"`);
  let cancelled = false;
  undoToast.onUndo(() => {
    cancelled = true;
    card.style.opacity = '1';
    card.style.transform = 'scale(1)';
  });
  setTimeout(async () => {
    if (cancelled) return;
    card.remove();
    try {
      await fetch('/api/projects/' + id, { method: 'DELETE' });
      loadRecentWork();
    } catch (e) { toast('Delete failed', true); loadLibrary(); }
  }, 5000);
}

function showUndoToast(message) {
  const el = document.createElement('div');
  el.className = 'toast show';
  el.style.display = 'flex';
  el.style.alignItems = 'center';
  el.style.gap = '14px';
  el.style.pointerEvents = 'auto';
  el.innerHTML = `<span>${escapeHtml(message)}</span>`;
  const btn = document.createElement('button');
  btn.textContent = 'Undo';
  btn.style.cssText = 'background:transparent;border:1px solid var(--accent);color:var(--accent);padding:4px 10px;border-radius:6px;cursor:pointer;font-family:inherit;font-size:12px;font-weight:600;';
  el.appendChild(btn);
  document.body.appendChild(el);
  let onUndoCb = () => {};
  btn.addEventListener('click', () => {
    onUndoCb();
    el.classList.remove('show');
    setTimeout(() => el.remove(), 200);
  });
  setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 200); }, 5000);
  return { onUndo: (cb) => { onUndoCb = cb; } };
}

document.querySelectorAll('.library-toolbar .filter-btn').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.library-toolbar .filter-btn').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    libFilter = b.dataset.filter;
    loadLibrary();
  });
});

setupNotifications();
document.querySelector('.tab-btn[data-tab="library"]').addEventListener('click', loadLibrary);
loadLibrary();

updatePasteCount();
loadVoices();
refreshOllama();
setInterval(refreshOllama, 15000);

// Idle-mode dashboard on load + periodic recent-work refresh
setGenMode('idle');
loadRecentWork();
setInterval(loadRecentWork, 30000);
loadPublishPosts().catch(() => {});
setInterval(() => loadPublishPosts().catch(() => {}), 15000);

// ---------- Generation tray (bottom-right, persistent) ----------
let trayKnownJobs = new Set();
async function pollGenTray() {
  // Aggregate any visible in-flight jobs across our local trackers.
  // We piggyback on the per-tab pollers but also independently surface
  // into the tray so the user sees jobs while on other tabs.
  const tray = $('genTray');
  if (!tray) return;
  const tracked = [
    { id: currentJob,    poll: '/api/status/',           kind: 'Story' },
    { id: shortJob,      poll: '/api/status/',           kind: 'Short' },
    { id: ttsJob,        poll: '/api/tts/status/',       kind: 'Narration' },
    { id: pasteJob,      poll: '/api/tts/status/',       kind: 'Narration' },
    { id: musicJob,      poll: '/api/music/status/',     kind: 'Music' },
    { id: mixJob,        poll: '/api/mix/status/',       kind: 'Mix' },
  ].filter(t => t.id);

  if (!tracked.length) {
    tray.classList.remove('shown');
    return;
  }

  const jobs = await Promise.all(tracked.map(async t => {
    try {
      const r = await fetch(t.poll + t.id);
      const d = await r.json();
      const j = d.job || {};
      return { kind: t.kind, id: t.id, title: j.title || j.name || (t.kind + ' job'),
               status: j.status || 'working', done: !!j.done, error: j.error,
               progress: computeProgress(j) };
    } catch { return null; }
  }));

  const active = jobs.filter(j => j && !j.done);
  if (!active.length) {
    tray.classList.remove('shown');
    return;
  }
  tray.classList.add('shown');
  $('gtList').innerHTML = active.map(j => `
    <div class="gt-item">
      <div class="gt-row1">
        <div class="gt-title">${escapeHtml(j.kind)}: ${escapeHtml(j.title)}</div>
        <div class="gt-stage">${escapeHtml(j.status)}</div>
      </div>
      <div class="gt-bar"><div style="width:${j.progress}%"></div></div>
    </div>
  `).join('');
}

function computeProgress(j) {
  if (!j) return 0;
  if (j.target && j.words != null)
    return Math.min(100, Math.round((j.words / Math.max(1, j.target)) * 100));
  if (j.chars_total && j.chars_done != null)
    return Math.min(100, Math.round((j.chars_done / Math.max(1, j.chars_total)) * 100));
  const s = (j.status || '').toLowerCase();
  if (s.includes('encoding')) return 90;
  if (s.includes('crossfade')) return 70;
  if (s.includes('composing')) return 35;
  if (s.includes('loading')) return 10;
  return 5;
}

$('gtClose')?.addEventListener('click', () => { $('genTray').classList.remove('shown'); });
setInterval(pollGenTray, 2000);

// ---------- Wavesurfer audio decoration ----------
function mountWaveform(audioEl) {
  // Replace a native <audio> with a styled wavesurfer wave + custom play button.
  if (!window.WaveSurfer) return;
  if (audioEl.dataset.waveMounted === '1') return;
  const url = audioEl.getAttribute('src');
  if (!url) return;
  const wrap = document.createElement('div');
  wrap.className = 'wave-wrap';
  const playBtn = document.createElement('button');
  playBtn.className = 'wave-play';
  playBtn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
  const canvas = document.createElement('div');
  canvas.className = 'wave-canvas';
  const time = document.createElement('div');
  time.className = 'wave-time';
  time.textContent = '0:00';
  wrap.appendChild(playBtn); wrap.appendChild(canvas); wrap.appendChild(time);
  audioEl.replaceWith(wrap);

  const ws = WaveSurfer.create({
    container: canvas,
    waveColor: 'rgba(34, 231, 245,0.34)',
    progressColor: '#69cff0',
    cursorColor: 'transparent',
    barWidth: 2, barGap: 2, barRadius: 2,
    height: 40,
    normalize: true,
    url: url,
  });
  playBtn.addEventListener('click', () => ws.playPause());
  ws.on('play',  () => playBtn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>');
  ws.on('pause', () => playBtn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>');
  ws.on('audioprocess', () => time.textContent = formatTime(ws.getCurrentTime()));
  ws.on('ready',        () => time.textContent = formatTime(ws.getDuration()));
  wrap.dataset.waveMounted = '1';
}
function formatTime(s) {
  s = Math.floor(s || 0);
  const m = Math.floor(s / 60); const r = s % 60;
  return m + ':' + (r < 10 ? '0' : '') + r;
}
function decorateAudio(root) {
  (root || document).querySelectorAll('audio[src]').forEach(mountWaveform);
}
// MutationObserver picks up audio elements added by the various tabs (library cards,
// inline TTS preview, music download, mix download).
const _audioObserver = new MutationObserver(muts => {
  muts.forEach(m => m.addedNodes.forEach(n => {
    if (!(n instanceof HTMLElement)) return;
    if (n.tagName === 'AUDIO' && n.getAttribute('src')) mountWaveform(n);
    else decorateAudio(n);
    forceReadableButtons(n);
  }));
  // Also catch <audio> elements whose src was set after insertion.
  document.querySelectorAll('audio[src]:not([data-wave-mounted])').forEach(mountWaveform);
  forceReadableButtons();
});
_audioObserver.observe(document.body, { childList: true, subtree: true });
window.addEventListener('load', () => decorateAudio());

// =============================================================================
// Simple mode + telemetry + feedback widget
// =============================================================================
const GH_PREFS_KEY = 'ghostline.prefs.v1';
const GH_SESSION = (() => {
  try {
    let id = localStorage.getItem('ghostline.session');
    if (!id) {
      id = (crypto.randomUUID && crypto.randomUUID()) || ('s_' + Math.random().toString(36).slice(2));
      localStorage.setItem('ghostline.session', id);
    }
    return id;
  } catch { return 's_' + Math.random().toString(36).slice(2); }
})();

function ghPrefs() {
  try { return JSON.parse(localStorage.getItem(GH_PREFS_KEY) || '{}') || {}; }
  catch { return {}; }
}
function ghSavePrefs(patch) {
  const next = { ...ghPrefs(), ...patch };
  try { localStorage.setItem(GH_PREFS_KEY, JSON.stringify(next)); } catch {}
  return next;
}
function applySimpleMode() {
  const p = ghPrefs();
  const simple = p.simpleMode !== false; // default ON for new installs
  document.body.classList.toggle('simple-mode', simple);
  // Control the sidebar's "Advanced Tools" panel: simple = collapsed by
  // default, non-simple = expanded. The toggle button can still flip it
  // mid-session without simple-mode fighting back.
  const tabs = document.querySelector('.tabs');
  if (tabs) tabs.classList.toggle('show-advanced', !simple);
  const cb = document.getElementById('settingsSimpleMode');
  if (cb) cb.checked = simple;
  const tcb = document.getElementById('settingsTelemetry');
  if (tcb) tcb.checked = p.telemetry !== false;
}
applySimpleMode();
document.getElementById('settingsSimpleMode')?.addEventListener('change', (e) => {
  ghSavePrefs({ simpleMode: !!e.target.checked });
  applySimpleMode();
  if (typeof toast === 'function') toast(e.target.checked ? 'Simple mode on' : 'Advanced tools visible');
});
document.getElementById('settingsTelemetry')?.addEventListener('change', (e) => {
  ghSavePrefs({ telemetry: !!e.target.checked });
});

// Telemetry: best-effort, opt-out via Settings.
window.ghTelemetry = function (type, payload) {
  try {
    if (ghPrefs().telemetry === false) return;
    fetch('/api/telemetry/event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, payload: payload || null, session: GH_SESSION }),
      keepalive: true,
    }).catch(() => {});
  } catch {}
};
window.addEventListener('error', (e) => {
  window.ghTelemetry('js-error', { message: String(e.message || ''), source: String(e.filename || ''), line: e.lineno });
});
window.addEventListener('unhandledrejection', (e) => {
  window.ghTelemetry('promise-rejection', { reason: String((e.reason && e.reason.message) || e.reason || '') });
});

// Feedback widget. Mounted at body; works on every screen.
(function mountFeedbackWidget() {
  if (document.getElementById('ghFeedbackFab')) return;
  const fab = document.createElement('button');
  fab.id = 'ghFeedbackFab';
  fab.className = 'gh-feedback-fab';
  fab.type = 'button';
  fab.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg> Feedback';
  document.body.appendChild(fab);
  const modal = document.createElement('div');
  modal.id = 'ghFeedbackModal';
  modal.className = 'gh-feedback-modal';
  modal.innerHTML = `
    <div class="gh-feedback-card">
      <h3>Send feedback</h3>
      <p>Bug, idea, or "this confused me." Anything helps.</p>
      <textarea id="ghFeedbackText" placeholder="What happened, or what would you change?"></textarea>
      <div class="row">
        <input id="ghFeedbackEmail" type="email" placeholder="email (optional, only if you want a reply)" style="width:100%;" />
      </div>
      <div class="actions">
        <button id="ghFeedbackCancel" class="btn secondary" type="button">Cancel</button>
        <button id="ghFeedbackSend" class="btn" type="button">Send</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  const open = () => modal.classList.add('shown');
  const close = () => modal.classList.remove('shown');
  fab.addEventListener('click', open);
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
  document.getElementById('ghFeedbackCancel').addEventListener('click', close);
  document.getElementById('ghFeedbackSend').addEventListener('click', async () => {
    const message = document.getElementById('ghFeedbackText').value.trim();
    const email = document.getElementById('ghFeedbackEmail').value.trim();
    if (!message) {
      if (typeof toast === 'function') toast('Type something first', true);
      return;
    }
    try {
      const res = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message, email,
          context: { tab: document.querySelector('.tab-btn.active')?.dataset.tab || '', session: GH_SESSION },
        }),
      });
      const data = await res.json();
      if (data.ok) {
        document.getElementById('ghFeedbackText').value = '';
        document.getElementById('ghFeedbackEmail').value = '';
        close();
        if (typeof toast === 'function') toast('Thanks, feedback received.');
      } else {
        if (typeof toast === 'function') toast(data.error || 'Could not send', true);
      }
    } catch (err) {
      if (typeof toast === 'function') toast('Could not reach server', true);
    }
  });
})();

// =============================================================================
// First-run walkthrough. Five overlay tooltips that point at the real Make
// Video flow. Dismissible, replayable from Settings.
// =============================================================================
const GH_ONBOARDING_KEY = 'ghostline.onboarding.seen.v2';

// Steps 2–4 live inside the Make tab panel; if that tab isn't active the
// targets exist in the DOM but have zero size, so getBoundingClientRect()
// returns (0,0,0,0) and the highlight renders in the page's top-left corner.
// `before` runs ahead of measurement to make the target visible.
const ghActivateMakeTab = () => {
  const btn = document.querySelector('.tab-btn[data-tab="make"]');
  if (btn && !btn.classList.contains('active')) btn.click();
};

const GH_WALK_STEPS = [
  {
    targetId: 'tab-btn-make',
    finder: () => document.querySelector('.tab-btn[data-tab="make"]'),
    title: '1. Open Create Video',
    body: 'This is where you make every video. The other tabs are optional.',
    side: 'right',
  },
  {
    targetId: 'makeShuffleIdeaBtn',
    before: ghActivateMakeTab,
    title: '2. Generate ideas',
    body: 'Click here to ask the local AI for fresh angles. You pick one and the rest of the form fills in.',
    side: 'top',
  },
  {
    targetId: 'makeTitleIdeasBtn',
    before: ghActivateMakeTab,
    title: '3. Get title options',
    body: 'After picking an idea, generate twelve clickable titles. Strong-fit titles for your channel rise to the top.',
    side: 'top',
  },
  {
    targetId: 'makeVideoBtn',
    before: ghActivateMakeTab,
    title: '4. Render the video',
    body: 'One click runs the full local pipeline: script, narration, captions, music, visuals, MP4.',
    side: 'top',
  },
  {
    targetId: 'ghFeedbackFab',
    title: '5. Stuck? Tap Feedback',
    body: 'The Feedback button stays in the corner of every screen. Tell us what broke or what to add. We read every one.',
    side: 'left',
  },
];

function ghWalkthroughSeen() {
  try { return localStorage.getItem(GH_ONBOARDING_KEY) === '1'; }
  catch { return true; }  // if storage is broken, never show
}

function ghWalkthroughMark() {
  try { localStorage.setItem(GH_ONBOARDING_KEY, '1'); }
  catch {}
}

function ghStartWalkthrough() {
  // Tear down any prior instance.
  document.querySelectorAll('.gh-walk-backdrop, .gh-walk-highlight, .gh-walk-tooltip').forEach(el => el.remove());

  const backdrop = document.createElement('div');
  backdrop.className = 'gh-walk-backdrop';
  backdrop.setAttribute('aria-hidden', 'true');
  // Block click-through but allow Escape to dismiss.
  document.body.appendChild(backdrop);

  const highlight = document.createElement('div');
  highlight.className = 'gh-walk-highlight';
  highlight.setAttribute('aria-hidden', 'true');
  document.body.appendChild(highlight);

  const tooltip = document.createElement('div');
  tooltip.className = 'gh-walk-tooltip';
  tooltip.setAttribute('role', 'dialog');
  tooltip.setAttribute('aria-live', 'polite');
  document.body.appendChild(tooltip);

  let stepIndex = 0;

  function targetElement(step) {
    if (step.finder) {
      try { return step.finder(); } catch { return null; }
    }
    if (step.targetId) return document.getElementById(step.targetId);
    return null;
  }

  function centerFallback() {
    highlight.style.display = 'none';
    tooltip.style.left = '50%';
    tooltip.style.top = '50%';
    tooltip.style.transform = 'translate(-50%, -50%)';
  }

  function applyPositioning(target, step) {
    const rect = target.getBoundingClientRect();
    // Hidden targets (display:none, inside an inactive tab, etc.) report
    // a zero-size rect. bail out to the centered fallback rather than
    // pinning the highlight to the page's top-left corner.
    if (rect.width === 0 && rect.height === 0) {
      centerFallback();
      return;
    }
    const pad = 8;
    highlight.style.display = '';
    highlight.style.top = `${rect.top - pad}px`;
    highlight.style.left = `${rect.left - pad}px`;
    highlight.style.width = `${rect.width + pad * 2}px`;
    highlight.style.height = `${rect.height + pad * 2}px`;

    const tipW = tooltip.offsetWidth || 360;
    const tipH = tooltip.offsetHeight || 160;
    const margin = 18;
    let top, left;
    const side = step.side || 'bottom';
    if (side === 'top') {
      top = rect.top - tipH - margin;
      left = rect.left + rect.width / 2 - tipW / 2;
    } else if (side === 'bottom') {
      top = rect.bottom + margin;
      left = rect.left + rect.width / 2 - tipW / 2;
    } else if (side === 'left') {
      top = rect.top + rect.height / 2 - tipH / 2;
      left = rect.left - tipW - margin;
    } else { // right
      top = rect.top + rect.height / 2 - tipH / 2;
      left = rect.right + margin;
    }
    // Keep on-screen.
    top = Math.max(12, Math.min(top, window.innerHeight - tipH - 12));
    left = Math.max(12, Math.min(left, window.innerWidth - tipW - 12));
    tooltip.style.transform = '';
    tooltip.style.top = `${top}px`;
    tooltip.style.left = `${left}px`;
  }

  // Each positionFor schedules late re-positioning passes; clearReposition
  // cancels them when the user advances or dismisses, so an old step's
  // late timeout doesn't fire after we've moved on.
  let positionTimers = [];
  function clearReposition() {
    positionTimers.forEach((id) => clearTimeout(id));
    positionTimers = [];
  }

  function positionFor(step) {
    clearReposition();
    try { step.before?.(); } catch {}
    const target = targetElement(step);
    if (!target) {
      centerFallback();
      return;
    }
    // Only scroll if the target is offscreen. re-scrolling sticky-positioned
    // targets (like the sidebar tabs) makes them shift unpredictably. Use
    // 'instant' so CSS scroll-behavior:smooth (set on landing pages, not the
    // studio, but we don't trust the inheritance) doesn't turn this into a
    // ~600ms animation that defeats rect measurement.
    const scrollIfOffscreen = () => {
      const r = target.getBoundingClientRect();
      const vh = window.innerHeight;
      if (r.bottom < 60 || r.top > vh - 60) {
        target.scrollIntoView({ behavior: 'instant', block: 'center', inline: 'nearest' });
      }
    };
    scrollIfOffscreen();
    const reposition = () => { scrollIfOffscreen(); applyPositioning(target, step); };
    // First pass: double rAF so the before() tab switch + scroll commit
    // before measurement.
    requestAnimationFrame(() => requestAnimationFrame(reposition));
    // Late passes catch async content shifts (insights panel fetching,
    // skeleton-to-real swaps) over up to ~1.7s. CSS transition (0.25s on
    // top/left/width/height) animates each correction smoothly.
    [350, 900, 1700].forEach((ms) => positionTimers.push(setTimeout(reposition, ms)));
  }

  function renderStep() {
    const step = GH_WALK_STEPS[stepIndex];
    const isLast = stepIndex === GH_WALK_STEPS.length - 1;
    tooltip.innerHTML = `
      <h4></h4>
      <p></p>
      <div class="gh-walk-bar">
        <span class="gh-walk-step">Step ${stepIndex + 1} of ${GH_WALK_STEPS.length}</span>
        <div class="gh-walk-actions">
          <button type="button" data-walk="skip">Skip</button>
          <button type="button" class="gh-walk-primary" data-walk="next">${isLast ? 'Done' : 'Next'}</button>
        </div>
      </div>
    `;
    tooltip.querySelector('h4').textContent = step.title;
    tooltip.querySelector('p').textContent = step.body;
    positionFor(step);
  }

  function teardown(markSeen) {
    clearReposition();
    backdrop.remove();
    highlight.remove();
    tooltip.remove();
    document.removeEventListener('keydown', onKey);
    window.removeEventListener('resize', onResize);
    if (markSeen) ghWalkthroughMark();
  }

  function onKey(e) {
    if (e.key === 'Escape') teardown(true);
    else if (e.key === 'Enter' || e.key === 'ArrowRight') advance();
  }

  function onResize() {
    positionFor(GH_WALK_STEPS[stepIndex]);
  }

  function advance() {
    stepIndex += 1;
    if (stepIndex >= GH_WALK_STEPS.length) {
      teardown(true);
      return;
    }
    renderStep();
  }

  tooltip.addEventListener('click', (e) => {
    const action = e.target?.dataset?.walk;
    if (action === 'skip') teardown(true);
    else if (action === 'next') advance();
  });
  backdrop.addEventListener('click', () => teardown(true));
  document.addEventListener('keydown', onKey);
  window.addEventListener('resize', onResize);

  renderStep();
}

window.ghStartWalkthrough = ghStartWalkthrough;

// Wire the Settings "Replay walkthrough" button. Resets the seen flag so a
// future browser-fresh visit re-shows it too.
document.getElementById('settingsReplayWalkBtn')?.addEventListener('click', () => {
  try { localStorage.removeItem(GH_ONBOARDING_KEY); } catch {}
  ghStartWalkthrough();
});

// Auto-show on first ever load. Wait for layout to settle.
if (!ghWalkthroughSeen()) {
  window.addEventListener('load', () => {
    setTimeout(ghStartWalkthrough, 1200);
  });
}

// =============================================================================
// Loading skeletons. Replaces empty/blank panels while async data lands so
// the app feels responsive instead of frozen.
// =============================================================================

function ghShowSkeletons(target, count = 6) {
  const el = typeof target === 'string' ? document.getElementById(target) : target;
  if (!el) return;
  const cells = Array.from({ length: count }, () => '<div class="gh-skeleton-card"></div>').join('');
  el.innerHTML = `<div class="gh-skeleton-grid" role="status" aria-live="polite" aria-label="Loading">${cells}</div>`;
}

// Wrap the original loaders so they show skeletons during the fetch. We
// preserve the original implementations and call through.
(function wrapLoadersWithSkeletons() {
  function wrap(fnName, gridId, count) {
    const original = window[fnName];
    if (typeof original !== 'function') return;
    window[fnName] = async function (...args) {
      const grid = document.getElementById(gridId);
      // Only show skeletons if grid is currently empty. avoid flash on
      // refreshes where data already populates the grid.
      if (grid && (!grid.children.length || grid.querySelector('.empty-state'))) {
        ghShowSkeletons(grid, count);
      }
      return original.apply(this, args);
    };
  }
  wrap('loadLibrary', 'libGrid', 8);
})();

// =============================================================================
// Render ETA. Tracks the last N successful render durations per (mode, ratio)
// combo in localStorage and displays a rolling-average estimate on the
// active workflow step. Telemetry already pings on render-complete; here we
// also stash the elapsed time locally for instant ETAs.
// =============================================================================

const GH_ETA_KEY = 'ghostline.renderTimes.v1';
const GH_ETA_WINDOW = 8;  // last N renders per bucket

function _ghEtaBucketKey() {
  const ratio = document.getElementById('makeAspect')?.value || 'unknown';
  const mode = document.getElementById('makeVideoMode')?.value || 'unknown';
  return `${mode}::${ratio}`;
}

function _ghLoadRenderTimes() {
  try { return JSON.parse(localStorage.getItem(GH_ETA_KEY) || '{}') || {}; }
  catch { return {}; }
}

function _ghSaveRenderTime(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0 || seconds > 7200) return;
  const all = _ghLoadRenderTimes();
  const key = _ghEtaBucketKey();
  const arr = Array.isArray(all[key]) ? all[key] : [];
  arr.push(Math.round(seconds));
  if (arr.length > GH_ETA_WINDOW) arr.shift();
  all[key] = arr;
  try { localStorage.setItem(GH_ETA_KEY, JSON.stringify(all)); } catch {}
}

function _ghCurrentEtaSeconds() {
  const arr = _ghLoadRenderTimes()[_ghEtaBucketKey()];
  if (!Array.isArray(arr) || !arr.length) return null;
  return Math.round(arr.reduce((a, b) => a + b, 0) / arr.length);
}

function _ghFormatRemaining(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  if (seconds < 60) return `~${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `~${m}m ${s}s` : `~${m}m`;
}

let _ghEtaTracker = null;

function ghStartEtaTracker() {
  ghStopEtaTracker();
  const eta = _ghCurrentEtaSeconds();
  if (!eta) return; // no history, no ETA
  const start = Date.now();
  _ghEtaTracker = { start, total: eta, interval: null };
  const tick = () => {
    const elapsed = (Date.now() - start) / 1000;
    const remaining = eta - elapsed;
    const running = document.querySelector('.workflow-step.running');
    document.querySelectorAll('.gh-eta').forEach(el => el.remove());
    if (!running) return;
    const stateEl = running.querySelector('.state');
    if (!stateEl) return;
    const chip = document.createElement('span');
    chip.className = 'gh-eta';
    chip.setAttribute('aria-live', 'polite');
    chip.textContent = remaining > 5 ? `${_ghFormatRemaining(remaining)} left` : 'wrapping up';
    stateEl.appendChild(chip);
  };
  tick();
  _ghEtaTracker.interval = setInterval(tick, 2000);
}

function ghStopEtaTracker() {
  if (_ghEtaTracker?.interval) clearInterval(_ghEtaTracker.interval);
  _ghEtaTracker = null;
  document.querySelectorAll('.gh-eta').forEach(el => el.remove());
}

// Hook into the existing render success/fail telemetry so we capture timing
// without modifying makeVideoWorkflow.
(function instrumentRenderEta() {
  const originalTelemetry = window.ghTelemetry;
  if (typeof originalTelemetry !== 'function') return;
  let renderStartedAt = null;
  // Patch makeVideoBtn click to mark start time.
  document.getElementById('makeVideoBtn')?.addEventListener('click', () => {
    renderStartedAt = Date.now();
    setTimeout(ghStartEtaTracker, 600);
  });
  window.ghTelemetry = function (type, payload) {
    if (type === 'render-complete' && renderStartedAt) {
      const elapsed = (Date.now() - renderStartedAt) / 1000;
      _ghSaveRenderTime(elapsed);
      renderStartedAt = null;
      ghStopEtaTracker();
    } else if (type === 'render-error') {
      renderStartedAt = null;
      ghStopEtaTracker();
    }
    return originalTelemetry.apply(this, arguments);
  };
})();

// =============================================================================
// Accessibility quick wins: aria-labels on icon-only buttons + a skip link.
// =============================================================================

(function a11yAugment() {
  const labelMap = {
    notificationBell: 'Open notifications',
    ghFeedbackFab: 'Send feedback',
    advancedNavToggle: 'Toggle advanced tools panel',
  };
  for (const [id, label] of Object.entries(labelMap)) {
    const el = document.getElementById(id);
    if (el && !el.getAttribute('aria-label')) el.setAttribute('aria-label', label);
  }
  // Skip-link: landing on Tab from page top jumps over the header to main.
  if (!document.querySelector('.gh-skip-link')) {
    const a = document.createElement('a');
    a.className = 'gh-skip-link';
    a.href = '#tab-launch';
    a.textContent = 'Skip to main content';
    document.body.prepend(a);
  }
})();

// =============================================================================
// PWA: register the service worker so the site is installable on Android +
// iOS home screens, and capture the beforeinstallprompt event so we can show
// our own "Install" button in Settings later.
// =============================================================================
let _ghDeferredInstallPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  _ghDeferredInstallPrompt = e;
  // Surface the install button if present in DOM.
  const btn = document.getElementById('settingsInstallAppBtn');
  if (btn) {
    btn.style.display = '';
    btn.disabled = false;
  }
});
window.ghPromptInstall = async function () {
  if (!_ghDeferredInstallPrompt) return false;
  _ghDeferredInstallPrompt.prompt();
  const choice = await _ghDeferredInstallPrompt.userChoice;
  _ghDeferredInstallPrompt = null;
  const btn = document.getElementById('settingsInstallAppBtn');
  if (btn) btn.style.display = 'none';
  return choice && choice.outcome === 'accepted';
};
document.getElementById('settingsInstallAppBtn')?.addEventListener('click', () => {
  window.ghPromptInstall();
});

if ('serviceWorker' in navigator) {
  // Defer registration so it doesn't compete with first paint.
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
  });
}

// =============================================================================
// License + usage UI. The Settings tab shows the active tier, lets the user
// paste a key, and surfaces this-month usage against the free-tier quotas.
// Render/scheduler API errors with code "QUOTA" or "UPGRADE" cause inline
// upgrade nudges in the Make Video flow.
// =============================================================================
const TIER_LABEL = { free: 'Free', pro: 'Pro', studio: 'Studio' };

async function refreshLicenseStatus() {
  const tierEl = document.getElementById('settingsLicenseTier');
  const statusEl = document.getElementById('settingsLicenseStatus');
  if (!tierEl) return null;
  try {
    const r = await fetch('/api/license');
    const d = await r.json();
    const info = (d && d.license) || { tier: 'free' };
    const tier = info.tier || 'free';
    tierEl.textContent = TIER_LABEL[tier] || tier;
    if (statusEl) {
      const lines = [];
      if (info.email) lines.push(`Licensed to ${info.email}.`);
      if (tier === 'free') {
        lines.push('Free tier: limited monthly renders, no scheduler, no Optimize Library.');
      } else if (info.expires_at) {
        const exp = new Date(info.expires_at * 1000);
        lines.push(`${TIER_LABEL[tier]} tier active. Expires ${exp.toLocaleDateString()}.`);
      } else {
        lines.push(`${TIER_LABEL[tier]} tier active. Lifetime license.`);
      }
      if (info.error) lines.push(info.error);
      statusEl.textContent = lines.join(' ');
    }
    if (d && d.pricing) renderPricingGrid(d.pricing, tier);
    return info;
  } catch {
    if (statusEl) statusEl.textContent = 'Could not check license.';
    return null;
  }
}

function renderPricingGrid(pricing, currentTier) {
  const grid = document.getElementById('settingsPricingGrid');
  if (!grid || !pricing || !Array.isArray(pricing.tiers)) return;
  const cards = pricing.tiers.map(t => {
    const isCurrent = t.id === currentTier || (t.tier_unlocked && t.tier_unlocked === currentTier);
    const isFounding = t.id === 'founding';
    const priceBlock = (() => {
      if (t.price_lifetime) return `<div class="pricing-tier-price">$${t.price_lifetime}<small> once</small></div>`;
      if (t.price_yearly && t.price_monthly) {
        return `
          <div class="pricing-tier-price">$${t.price_monthly}<small>/mo</small></div>
          <div class="pricing-tier-secondary">or $${t.price_yearly}/yr · save ${Math.round((1 - t.price_yearly / (t.price_monthly * 12)) * 100)}%</div>
        `;
      }
      if (t.id === 'free') return `<div class="pricing-tier-price">$0</div>`;
      return '';
    })();
    const features = (t.features || []).map(f => `<li>${escapeHtml(f)}</li>`).join('');
    const cta = isCurrent
      ? `<span class="pricing-tier-current-badge">Current</span>`
      : (t.checkout_url
          ? `<a class="btn" href="${escapeHtml(t.checkout_url)}" target="_blank" rel="noopener">${escapeHtml(t.cta || 'Choose')}</a>`
          : `<button class="btn secondary" type="button" disabled>${escapeHtml(t.cta || 'Choose')}</button>`);
    return `
      <div class="pricing-tier-card ${isCurrent ? 'is-current' : ''} ${isFounding ? 'is-founding' : ''}">
        <div>
          <div class="pricing-tier-name">${escapeHtml(t.name)}</div>
          <div class="pricing-tier-tagline">${escapeHtml(t.tagline || '')}</div>
        </div>
        ${priceBlock}
        <ul class="pricing-tier-features">${features}</ul>
        ${cta}
      </div>
    `;
  }).join('');
  grid.innerHTML = cards;
}

async function refreshUsageStatus() {
  const el = document.getElementById('settingsUsageStatus');
  if (!el) return;
  try {
    const r = await fetch('/api/usage');
    const d = await r.json();
    const counters = d.counters || {};
    const limits = d.limits || {};
    const renders = counters.renders_per_month || 0;
    const renderLimit = limits.renders_per_month;
    const publishes = counters.publishes_per_month || 0;
    const reset = d.days_until_reset != null ? `Resets in ${d.days_until_reset} day${d.days_until_reset === 1 ? '' : 's'}.` : '';
    if (renderLimit === 'unlimited') {
      el.textContent = `${renders} render${renders === 1 ? '' : 's'} this month · unlimited.`;
    } else {
      el.textContent = `${renders}/${renderLimit} renders this month · ${reset}`;
    }
  } catch {
    el.textContent = '';
  }
}

document.getElementById('settingsLicenseApplyBtn')?.addEventListener('click', async () => {
  const input = document.getElementById('settingsLicenseKey');
  const key = (input?.value || '').trim();
  if (!key) {
    if (typeof toast === 'function') toast('Paste a license key first', true);
    return;
  }
  try {
    const r = await fetch('/api/license', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    const d = await r.json();
    if (d.ok) {
      if (typeof toast === 'function') toast(`License applied: ${TIER_LABEL[d.license?.tier] || 'updated'}`);
      if (input) input.value = '';
      await refreshLicenseStatus();
      await refreshUsageStatus();
    } else {
      if (typeof toast === 'function') toast(d.error || 'License rejected', true);
    }
  } catch {
    if (typeof toast === 'function') toast('Could not reach license endpoint', true);
  }
});

document.getElementById('settingsLicenseClearBtn')?.addEventListener('click', async () => {
  if (!confirm('Remove the active license? You\'ll revert to the free tier.')) return;
  try {
    await fetch('/api/license', { method: 'DELETE' });
    if (typeof toast === 'function') toast('License cleared');
    await refreshLicenseStatus();
    await refreshUsageStatus();
  } catch {
    if (typeof toast === 'function') toast('Could not clear license', true);
  }
});

// Refresh license/usage when Settings tab opens.
document.querySelector('.tab-btn[data-tab="settings"]')?.addEventListener('click', () => {
  refreshLicenseStatus();
  refreshUsageStatus();
  refreshEngineStatus();
});
window.addEventListener('load', () => {
  refreshLicenseStatus();
  refreshUsageStatus();
  refreshEngineStatus();
});

// =============================================================================
// AI engine selection. The Make Video flow can run script generation on the
// local Ghostline server (Ollama) OR in the browser (WebLLM + Llama 3.2 1B).
// Mobile/PWA users will pick "device" so they don't need a PC running.
// =============================================================================
function refreshEngineStatus() {
  if (!window.GhostlineEngines) return;
  const active = window.GhostlineEngines.activeId();
  const list = window.GhostlineEngines.list();
  const statusEl = document.getElementById('settingsEngineStatus');
  if (statusEl) {
    const label = list.find(e => e.id === active)?.label || active;
    statusEl.textContent = label;
  }
  const serverInput = document.getElementById('engineServer');
  const deviceInput = document.getElementById('engineDevice');
  if (serverInput) serverInput.checked = active === 'server';
  if (deviceInput) {
    deviceInput.checked = active === 'device';
    const deviceMeta = list.find(e => e.id === 'device');
    if (deviceMeta && !deviceMeta.available) {
      deviceInput.disabled = true;
      deviceInput.parentElement?.setAttribute(
        'data-blocked-reason',
        "WebGPU isn't available in this browser. Try Chrome on Android or Safari 17+ on iPhone."
      );
    }
  }
}

document.querySelectorAll('input[name="ghEngine"]').forEach((input) => {
  input.addEventListener('change', async (e) => {
    if (!e.target.checked || !window.GhostlineEngines) return;
    const id = e.target.value;
    window.GhostlineEngines.set(id);
    refreshEngineStatus();
    if (id === 'device') {
      // Eagerly init so the model download starts; show progress in card.
      const progressEl = document.getElementById('engineLoadProgress');
      if (progressEl) progressEl.style.display = '';
      try {
        await window.GhostlineEngines.current().init((p) => {
          if (progressEl) {
            const pct = p.progress != null ? `${Math.round(p.progress * 100)}%` : '';
            progressEl.textContent = `${p.text || 'Loading model…'} ${pct}`.trim();
          }
        });
        if (progressEl) progressEl.textContent = 'Model loaded. Ready to generate locally.';
        if (typeof toast === 'function') toast('On-device AI ready');
      } catch (err) {
        if (progressEl) progressEl.textContent = `Could not load on-device engine: ${err.message || err}`;
        if (typeof toast === 'function') toast(err.message || 'Engine load failed', true);
      }
    }
  });
});

// Mobile-aware UI: when the engine is "device", hide MusicGen + FLUX panels
// and any control that depends on the local Python pipeline. Body class
// drives the visibility via CSS.
function applyEngineUIHints() {
  const engine = window.GhostlineEngines?.activeId() || 'server';
  document.body.classList.toggle('engine-device', engine === 'device');
  document.body.classList.toggle('engine-server', engine === 'server');
}
window.addEventListener('load', applyEngineUIHints);
document.addEventListener('change', (e) => {
  if (e.target?.name === 'ghEngine') applyEngineUIHints();
});

// =============================================================================
// Pexels (stock video) API key handling. Free signup; key stays in the
// user's browser via localStorage. Never sent to your server.
// =============================================================================
function loadPexelsKey() {
  const input = document.getElementById('settingsPexelsKey');
  if (!input) return;
  const lib = window.GhostlineMobileLibs?.pexels;
  if (lib) input.value = lib.apiKey() || '';
}
document.getElementById('settingsPexelsSaveBtn')?.addEventListener('click', () => {
  const input = document.getElementById('settingsPexelsKey');
  const status = document.getElementById('settingsPexelsStatus');
  const lib = window.GhostlineMobileLibs?.pexels;
  if (!input || !lib) return;
  lib.setApiKey(input.value);
  if (status) status.textContent = input.value.trim() ? 'Key saved.' : 'Key cleared.';
  if (typeof toast === 'function') toast(input.value.trim() ? 'Pexels key saved' : 'Pexels key cleared');
});
document.getElementById('settingsPexelsTestBtn')?.addEventListener('click', async () => {
  const status = document.getElementById('settingsPexelsStatus');
  const lib = window.GhostlineMobileLibs?.pexels;
  if (!lib) return;
  if (status) status.textContent = 'Searching…';
  try {
    const results = await lib.search('cinematic city night', { perPage: 3 });
    if (status) status.textContent = `Found ${results.length} clip${results.length === 1 ? '' : 's'}. Key works.`;
  } catch (err) {
    if (status) status.textContent = err.message || 'Search failed.';
  }
});

// Load key when Settings tab opens.
document.querySelector('.tab-btn[data-tab="settings"]')?.addEventListener('click', loadPexelsKey);
window.addEventListener('load', loadPexelsKey);
